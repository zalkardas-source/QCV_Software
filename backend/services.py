import logging
import os
import sys
import json
import tempfile

logger = logging.getLogger(__name__)

# Windows DLL Fix for PyTorch/Docling
if os.name == 'nt':
    try:
        import site
        for path in site.getsitepackages():
            torch_lib_path = os.path.join(path, 'torch', 'lib')
            if os.path.exists(torch_lib_path):
                # Only add if it's a valid directory to avoid Errno 22
                os.add_dll_directory(os.path.abspath(torch_lib_path))
    except Exception:
        pass

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableStructureOptions
from openai import OpenAI
from pydantic import ValidationError
from backend.schemas import CVData
from backend.job_schemas import JobRequirementData
from backend.config import settings
from backend.skill_normalization import (
    ALLOWED_CATEGORIES,
    SKILL_REGISTRY,
    canonical_name,
    count_project_evidence,
    rating_from_evidence,
)

# Docling converters are initialized lazily and cached globally.
# We keep three: fast text-only (default), OCR fallback for scanned PDFs,
# and a picture-enabled one used by the upload flow to grab the profile photo.
_converter_fast = None
_converter_ocr = None
_converter_pictures = None

# If the fast pass returns less text than this, we assume it's a scanned
# PDF and re-run with OCR. 100 chars is well below any real CV's body text.
_OCR_FALLBACK_MIN_CHARS = 100


def _build_converter(do_ocr: bool, do_tables: bool, with_pictures: bool = False):
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = do_ocr
    pipeline_options.do_table_structure = do_tables
    if with_pictures:
        pipeline_options.images_scale = 1.5
        pipeline_options.generate_picture_images = True
    else:
        pipeline_options.images_scale = 0.0
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def get_docling_converter(with_ocr: bool = False, with_pictures: bool = False):
    global _converter_fast, _converter_ocr, _converter_pictures
    if with_pictures:
        if _converter_pictures is None:
            _converter_pictures = _build_converter(do_ocr=False, do_tables=False, with_pictures=True)
        return _converter_pictures
    if with_ocr:
        if _converter_ocr is None:
            _converter_ocr = _build_converter(do_ocr=True, do_tables=True)
        return _converter_ocr
    if _converter_fast is None:
        _converter_fast = _build_converter(do_ocr=False, do_tables=False)
    return _converter_fast


def warmup_docling():
    """Initializes the fast Docling converter so the first request isn't slow.
    The OCR converter is built lazily only if a scanned PDF actually arrives."""
    logger.info("Warming up Docling engine...")
    try:
        get_docling_converter(with_ocr=False)
        logger.info("Docling engine ready.")
    except Exception as e:
        logger.error("Docling warmup failed: %s", e)


def extract_text_from_file(file_content: bytes, filename: str) -> str:
    """Extracts markdown from a document using Docling.

    Strategy: fast pass without OCR / table detection. If the result looks
    empty (a scanned PDF where Docling can't see text), retry with OCR on.
    """
    import time
    import uuid
    ext = filename.split('.')[-1].lower() if "." in filename else "pdf"
    tmp_path = os.path.abspath(f"tmp_{uuid.uuid4().hex}.{ext}")

    try:
        with open(tmp_path, 'wb') as f:
            f.write(file_content)

        t0 = time.perf_counter()
        result = get_docling_converter(with_ocr=False).convert(tmp_path)
        markdown = result.document.export_to_markdown()
        logger.info(
            "[Docling] fast pass: %d chars in %.2fs",
            len(markdown), time.perf_counter() - t0,
        )

        if len(markdown.strip()) < _OCR_FALLBACK_MIN_CHARS:
            logger.info("[Docling] fast pass returned almost no text, retrying with OCR.")
            t1 = time.perf_counter()
            result = get_docling_converter(with_ocr=True).convert(tmp_path)
            markdown = result.document.export_to_markdown()
            logger.info(
                "[Docling] OCR pass: %d chars in %.2fs",
                len(markdown), time.perf_counter() - t1,
            )

        return markdown
    except Exception as e:
        logger.error("Docling conversion error: %s: %s", type(e).__name__, e)
        raise e
    finally:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

def _pick_profile_photo(doc) -> bytes | None:
    """Best-guess profile photo from a Docling document.

    Strategy: scan all picture items, pick the one most likely to be a person
    portrait — early in the document, roughly square (0.5-2.0 aspect ratio),
    not tiny (>= 60 px on the short side). Returns JPEG bytes or None.

    Defensive: any exception means "no photo" — we never want photo extraction
    to break the upload pipeline.
    """
    try:
        from io import BytesIO
        pictures = getattr(doc, "pictures", None) or []
        candidates = []
        for idx, pic in enumerate(pictures):
            try:
                # Docling API has shifted across versions; try the common accessors.
                pil_image = None
                if hasattr(pic, "get_image"):
                    try:
                        pil_image = pic.get_image(doc)
                    except Exception:
                        pil_image = None
                if pil_image is None:
                    img_attr = getattr(pic, "image", None)
                    if img_attr is not None:
                        pil_image = getattr(img_attr, "pil_image", None) or img_attr
                if pil_image is None or not hasattr(pil_image, "size"):
                    continue

                w, h = pil_image.size
                if min(w, h) < 60:
                    continue
                aspect = w / h if h else 0
                if aspect < 0.5 or aspect > 2.0:
                    continue
                # Earlier-in-document and squarer images score higher.
                squareness = 1.0 - abs(1.0 - aspect)
                score = squareness - (idx * 0.05)
                candidates.append((score, pil_image))
            except Exception as e:
                logger.debug("[PHOTO] Skipping picture %d: %s", idx, e)

        if not candidates:
            return None

        candidates.sort(key=lambda c: c[0], reverse=True)
        best = candidates[0][1]
        if best.mode not in ("RGB", "L"):
            best = best.convert("RGB")
        buf = BytesIO()
        best.save(buf, format="JPEG", quality=88, optimize=True)
        logger.info("[PHOTO] Selected %dx%d photo from %d candidates", *best.size, len(candidates))
        return buf.getvalue()
    except Exception as e:
        logger.warning("[PHOTO] Profile photo extraction failed: %s", e)
        return None


def extract_text_and_photo(file_content: bytes, filename: str) -> tuple[str, bytes | None]:
    """Like extract_text_from_file, but additionally returns the candidate's
    profile photo (JPEG bytes) when one can be reliably identified.

    Uses a separate Docling pass with picture extraction enabled. Text
    extraction is best-effort with the same OCR fallback as the fast path.
    Photo extraction is fail-safe: any error → returns (markdown, None).
    """
    import time
    import uuid

    ext = filename.split('.')[-1].lower() if "." in filename else "pdf"
    tmp_path = os.path.abspath(f"tmp_{uuid.uuid4().hex}.{ext}")

    try:
        with open(tmp_path, 'wb') as f:
            f.write(file_content)

        t0 = time.perf_counter()
        result = get_docling_converter(with_ocr=False, with_pictures=True).convert(tmp_path)
        markdown = result.document.export_to_markdown()
        logger.info(
            "[Docling] picture-enabled pass: %d chars in %.2fs",
            len(markdown), time.perf_counter() - t0,
        )

        if len(markdown.strip()) < _OCR_FALLBACK_MIN_CHARS:
            logger.info("[Docling] picture pass returned almost no text, retrying with OCR.")
            t1 = time.perf_counter()
            result = get_docling_converter(with_ocr=True).convert(tmp_path)
            markdown = result.document.export_to_markdown()
            logger.info(
                "[Docling] OCR pass: %d chars in %.2fs",
                len(markdown), time.perf_counter() - t1,
            )
            return markdown, None  # OCR fallback runs without pictures

        photo = _pick_profile_photo(result.document)
        return markdown, photo

    except Exception as e:
        logger.error("Docling conversion error (text+photo): %s: %s", type(e).__name__, e)
        raise
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def structure_cv_data(raw_markdown: str) -> dict:
    """Structures CV Markdown into strict JSON using Minimax-m2.7 with optimized prompt."""
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=settings.openrouter_api_key)
    schema_json = CVData.model_json_schema()
    
    system_prompt = f"""
    Return ONLY a valid JSON object matching this schema:
    {json.dumps(schema_json)}

    RULES:

    0. PERSONAL DATA — Search the ENTIRE CV (header, sidebar, "Persönliche Daten"
       / "Personal details" section, contact block, signature) for every field
       listed in personal_information. Fill in whatever the CV provides:
       - full_name        : the candidate's full name
       - age_or_dob       : age or date of birth (any format the CV uses)
       - nationality      : nationality / citizenship
       - marital_status   : marital status (Single, Verheiratet, ...)
       - email            : verbatim from the CV
       - phone            : ONE primary phone number, verbatim
       - location         : current city / country
       - linkedin         : LinkedIn URL or profile path
       - website          : personal website / portfolio URL
       Leave a field as null ONLY when the CV truly does not contain it. Do not
       guess or fabricate values.

    1. SKILLS — Extract EVERY concrete skill that appears ANYWHERE in the CV.
       Evidence sources you MUST scan (not only the skills section!):
       - the dedicated skills section
       - every project description (tools, frameworks, methodologies actually used)
       - job titles and role descriptions
       - certifications and trainings
       - the summary / about section

       COMMUNICATION-RELATED SKILLS — mandatory active mining:
       Scan project descriptions and summary text for any concrete communication
       activity and extract a corresponding skill. The activity is the proof; the
       skill is what to record. Examples of trigger phrases and their skills:
       - "Workshops moderiert", "Fachbereichs-Workshops"          → Moderation, Workshops
       - "Trainings durchgeführt", "Schulungen erstellt"          → Trainings, Schulungen
       - "Präsentationen vor Vorstand / C-Level"                  → Präsentationstechniken
       - "Reporting an Management / Stakeholder"                  → Stakeholder Reporting
       - "Verhandlungen mit Lieferanten / Kunden"                 → Verhandlungsführung
       - "Kundenkommunikation", "Client Communication"            → Kundenkommunikation
       - "Coaching neuer Mitarbeiter", "Onboarding-Begleitung"    → Coaching, Wissensvermittlung
       - "Stakeholder Management", "Sprint-Abstimmungen"          → Stakeholder Management
       Place these in the most fitting existing category (typically
       "Communication & Training", occasionally "Methods & Frameworks" when
       the activity is process-driven like Stakeholder Management). Never
       create a new top-level category for them.

       Only derive when the activity is clearly described, not on vague mentions.

       USE THESE CANONICAL SPELLINGS for skills you recognize (case-sensitive):
       - Programming:  Python, Java, C#, C++, JavaScript, TypeScript, Go, SQL, ABAP
       - Tools:        Git, Docker, Kubernetes, Jira, Linux, Kafka, AWS, Azure, GCP
       - Office/Data:  Excel, Power BI, MS Office, PowerPoint, Word, Pandas, NumPy
       - Databases:    PostgreSQL, MySQL, MongoDB, Oracle, SQL Server, Redis, HANA DB
       - SAP modules:  SAP MM, SAP SD, SAP FI, SAP CO, SAP HCM, SAP PP, SAP WM,
                       SAP EWM, SAP TM, SAP PS, SAP S/4HANA, SAP Fiori, SAP BW,
                       SAP Ariba, SAP SuccessFactors, SAP Solution Manager
       - Methods:      Agile, Scrum, Kanban, Waterfall, Projektmanagement,
                       Change Management, Data Migration, Rollout, Cutover,
                       Stakeholder Management, UAT Testing, Anforderungsanalyse
       - Communication: Workshops, Moderation, Trainings, Schulungen, Coaching,
                       Kundenkommunikation, Präsentationstechniken
       - Languages (spoken): English, German, French, Spanish, Italian, Polish,
                       Russian, Chinese, Portuguese, Dutch, Turkish, Arabic
       For skills NOT in this list, use the most common spelling found in the CV.

       Use these standard categories (create a new one only if truly nothing fits):
       - "Programming Languages"    (Python, Java, C#, SQL, ABAP, ...)
       - "Frameworks & Libraries"   (React, Django, Spring, ...)
       - "Databases"                (PostgreSQL, MongoDB, Oracle, HANA DB, ...)
       - "Tools & Platforms"        (Git, Docker, AWS, Jira, Linux, ...)
       - "SAP"                      (STRICT: only actual SAP modules and SAP-branded
                                     products. Examples: SAP MM, SD, FI, CO, HCM, PP,
                                     WM, EWM, PM, QM, TM, S/4HANA, Fiori, BW, BPC,
                                     SAC, Solution Manager, ABAP, BTP, Ariba,
                                     SuccessFactors, Concur, IBP. NOTHING ELSE belongs
                                     here — see rule below.)
       - "Data & Analytics"         (Excel, Power BI, Tableau, Pandas, Looker, ...)
       - "Methods & Frameworks"     (Agile, Scrum, Kanban, Waterfall, Projektmanagement,
                                     Change Management, Data Migration, Rollout,
                                     Go-Live Support, Cutover, ITIL, Six Sigma,
                                     Anforderungsanalyse, Testmanagement, ...)
       - "Communication & Training" (Trainings, Schulungen, Moderation, Workshops,
                                     Präsentationen, Coaching, Verhandlung,
                                     Kundenkommunikation, Wissensvermittlung,
                                     Stakeholder Management, ...)
       - "Languages"                (English, German, French, ... — spoken languages only)

       STRICT RULES for the SAP category:
       Activities that happen IN AN SAP CONTEXT but are not themselves SAP products
       belong to other categories, NOT to "SAP":
       - "Data Migration", "Rollout", "Go-Live", "Cutover"     → Methods & Frameworks
       - "Training", "Key User Training", "Schulungen"         → Communication & Training
       - "Projektmanagement", "Change Management", "Agile"     → Methods & Frameworks
       - "ABAP"                                                → Programming Languages
       - "HANA DB" as a database engine                        → Databases
       Only the SAP module/product itself goes into "SAP".

       Do NOT extract character-style soft skills (Teamfähigkeit, Leadership, Belastbarkeit,
       Eigeninitiative, Zuverlässigkeit, Motivation, Flexibilität, Kommunikationsstark, ...).
       Distinction: "Kommunikation" as a trained skill (moderating workshops, presenting
       to executives) → keep it under "Communication & Training". "Kommunikationsstark" or
       "kommunikativ" as a personality trait → skip.
       Do NOT repeat the same skill in multiple categories.

    2. RATINGS — Project evidence is the ground truth. Before assigning a rating,
       count how many projects mention the skill and how central it is in each.
       - 9-10: senior/lead/architect context AND the skill appears as primary in MULTIPLE projects
       - 7-8:  the skill is a MAIN tool in at least one significant project (or appears across several)
       - 5-6:  the skill appears in project descriptions but is not the main focus
       - 3-4:  only in the skills list, no supporting project evidence
       - 1-2:  briefly mentioned, no evidence of real use
       A claim alone is never enough: if the candidate lists a skill but no project shows it,
       cap the rating at 4. Skills derived purely from activities (no explicit naming) start at
       rating 5 if the activity is described in detail, rating 4 otherwise.

    3. SUMMARY — Write 4-6 sentences.
       - Sentence 1-2: role, years of total experience, focus area / specialization.
         Example: "Senior SAP Functional Consultant with 20+ years of experience
         specializing in SAP MM/SD rollouts and S/4HANA migrations."
       - Sentence 3-4: name the candidate's 3-5 STRONGEST skills in a natural-
         sounding sentence — these MUST come from skill_matrix and MUST be a
         mix of technical, methodological, and communication skills. Pick the
         highest-rated skills across categories.
         Example: "Brings strong hands-on expertise in SAP MM, data migration,
         agile project management, and stakeholder workshops, with proven
         training delivery for international key user teams."
       - Sentence 5-6: industry exposure, project types, and geographic scope
         where applicable. Pull industries from the projects' `industry` field
         (e.g. Pharma, Automotive, Banking). Project types: rollouts, migrations,
         implementations, change initiatives. Geographic: countries or regions
         the candidate has worked in.
         Example: "Industry exposure spans Pharma, Automotive, and Banking,
         delivering rollouts and post-go-live support across Germany, Russia,
         and Spain."
       Be factual, no marketing language. Skills mentioned in the summary
       must also be present in skill_matrix — never invent skills for the
       summary that aren't in the matrix. If a part has no evidence in the
       CV (e.g. no clear industry), simply skip that sentence rather than
       fabricating content.

    4. EXPERIENCE — Extract every job and project. Keep descriptions in the original language.
       For each project, also fill in:
       - `industry`: the client's industry/sector in one or two English words. Examples:
         "Pharma", "Automotive", "Banking", "Insurance", "Retail", "Manufacturing",
         "Telco", "Public Sector", "Energy", "Logistics", "Chemicals", "Consumer Goods".
         Infer from the client name or project context (e.g. BioNTech → Pharma, BMW →
         Automotive, Deutsche Bank → Banking). Null only if truly unclear.
       - `location`: country or city of the project (e.g. "Germany", "Berlin", "Remote").
         Null if not mentioned.

    5. TOTAL EXPERIENCE — Compute `total_experience_years` as the SUM of all project/job durations
       in years (integer, rounded to nearest). Handle overlapping roles by counting each
       calendar year only once. Date format examples:
       - "2020-2023" → 3 years
       - "Jan 2020 - Dec 2022" → 3 years
       - "2020-present" → use 2026 as the current year
       - "6 months" → 0 (round down) or 1 (round up); prefer the closest integer
       Null if no durations are available.

    6. FORMAT — No preamble, no markdown fences, raw JSON only.
    """
    
    logger.debug("Raw CV markdown (first 500 chars):\n%s", raw_markdown[:500])

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"CV Text:\n{raw_markdown}"}
    ]
    
    import time
    max_retries = 3
    for attempt in range(max_retries):
        text_content = None
        try:
            logger.info("[LLM] Extraction attempt %d/%d", attempt + 1, max_retries)
            t0 = time.perf_counter()
            response = client.chat.completions.create(
                model="google/gemini-2.5-flash",
                messages=messages,
                temperature=0,
                top_p=0,
                seed=42,
                response_format={"type": "json_object"},
                timeout=60,
            )
            logger.info("[LLM] gemini-2.5-flash responded in %.2fs", time.perf_counter() - t0)
            
            logger.debug("[LLM] Response received. Choices: %d", len(response.choices))
            
            if not response.choices or not response.choices[0].message.content:
                logger.error("[LLM] Empty response from API. Full response: %s", response)
                raise ValueError("LLM returned an empty response. The model may be unavailable.")
            
            text_content = response.choices[0].message.content.strip()
            logger.debug("[LLM] Raw response length: %d, first 200 chars: %s", len(text_content), text_content[:200])
            
            # Clean up potential markdown formatting just in case
            if text_content.startswith("```json"):
                text_content = text_content[7:-3].strip()
            elif text_content.startswith("```"):
                text_content = text_content[3:-3].strip()
                
            parsed_json = json.loads(text_content)
            
            # VALIDATION STEP: The Mold checks the data
            validated_data = CVData(**parsed_json)
            
            logger.info("[LLM] Validation successful.")
            initial_dict = validated_data.model_dump()

            # Diagnostics: which personal-information fields actually got filled
            personal = initial_dict.get("personal_information", {}) or {}
            filled_personal = sorted([
                k for k, v in personal.items()
                if v and (not isinstance(v, str) or v.strip())
            ])
            missing_personal = sorted([k for k in personal if k not in filled_personal])
            logger.info("[PERSONAL] filled (%d): %s | missing: %s",
                        len(filled_personal), filled_personal, missing_personal)

            initial_skill_names = sorted({
                name for name, _, _ in _flatten_skills(initial_dict.get("skill_matrix", []))
            }, key=str.lower)
            logger.info("[SKILLS] Initial extraction (%d): %s",
                        len(initial_skill_names), initial_skill_names)

            # Communication-related skills the extractor picked up
            comm_skills = [
                (name, rating) for name, cat, rating
                in _flatten_skills(initial_dict.get("skill_matrix", []))
                if cat == "Communication & Training"
            ]
            logger.info("[COMM_SKILLS] from extractor (%d): %s",
                        len(comm_skills), comm_skills)

            # Second pass: scan the CV again to mine project-evidence skills
            # that the initial extractor missed. Fail-safe → empty list.
            mined_matrix = _mine_project_skills(raw_markdown, initial_dict)
            mined_names = sorted({
                name for name, _, _ in _flatten_skills(mined_matrix)
            }, key=str.lower)
            logger.info("[SKILLS] Derived from projects (%d): %s",
                        len(mined_names), mined_names)

            initial_dict["skill_matrix"] = _fuse_skills(
                initial_dict.get("skill_matrix", []), mined_matrix
            )
            pre_reviewer_names = sorted({
                name for name, _, _ in _flatten_skills(initial_dict["skill_matrix"])
            }, key=str.lower)
            logger.info("[SKILLS] After fusion, before reviewer (%d): %s",
                        len(pre_reviewer_names), pre_reviewer_names)

            # Third pass: QA review (fail-safe: returns input on reviewer failure)
            reviewed = review_cv_data(raw_markdown, initial_dict)
            post_reviewer_names = sorted({
                name for name, _, _ in _flatten_skills(reviewed.get("skill_matrix", []))
            }, key=str.lower)
            logger.info("[SKILLS] After reviewer (%d): %s",
                        len(post_reviewer_names), post_reviewer_names)

            # Deterministic post-process: code-based category overrides eliminate
            # the main source of run-to-run drift (LLM mis-categorizing known skills).
            reviewed["skill_matrix"] = _apply_category_overrides(reviewed.get("skill_matrix", []))
            final_names = sorted({
                name for name, _, _ in _flatten_skills(reviewed["skill_matrix"])
            }, key=str.lower)
            logger.info("[SKILLS] After category overrides (%d): %s",
                        len(final_names), final_names)

            # Deterministic rating recomputation: for every recognized skill,
            # the rating is now a pure function of how many projects mention it.
            # This eliminates rating drift entirely for known skills.
            reviewed["skill_matrix"] = _recompute_ratings_from_evidence(
                reviewed["skill_matrix"], reviewed.get("projects", [])
            )
            ratings_summary = [
                (name, rating)
                for name, _, rating in _flatten_skills(reviewed["skill_matrix"])
            ]
            logger.info("[SKILLS] After rating recompute: %s", ratings_summary)

            # Final summary as it will be stored — useful for verifying the
            # 2-4-sentence + top-skills format actually landed.
            summary_text = (reviewed.get("small_summary") or "").strip()
            logger.info("[SUMMARY] %s", summary_text)

            return reviewed
            
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("[LLM] Validation failed on attempt %d: %s", attempt + 1, e)
            if attempt == max_retries - 1:
                raise ValueError(f"Failed to extract structured data after {max_retries} attempts. Last error: {str(e)}")
                
            # THE SELF-HEALING LOOP: Feed the error back to the LLM
            if text_content:
                messages.append({"role": "assistant", "content": text_content})
                
            error_message = f"Your previous output failed validation. Please correct your output and return only valid JSON.\nError details: {str(e)}"
            messages.append({"role": "user", "content": error_message})
            
        except Exception as e:
            # Catch API errors, timeouts, auth failures, etc.
            logger.exception("[LLM] Unexpected error on attempt %d: %s: %s", attempt + 1, type(e).__name__, e)
            if attempt == max_retries - 1:
                raise ValueError(f"LLM API error: {type(e).__name__}: {str(e)}")


# Module-level alias kept for backward-compat with code/tests that imported this.
# The canonical list now lives in backend.skill_normalization.
_ALLOWED_CATEGORIES = ALLOWED_CATEGORIES


def _normalize_sap_skill(name: str) -> str | None:
    """Backward-compat wrapper: returns the SAP-canonical form if `name` is a
    SAP module/product, else None. Delegates to the central registry.
    """
    canonical = canonical_name(name)
    if canonical and canonical.startswith("SAP "):
        return canonical
    return None


def _apply_category_overrides(skill_matrix: list) -> list:
    """Deterministic post-process applied AFTER every LLM pass.

    For every skill:
    1. Look up its canonical name in the registry — that fixes spelling drift
       (MS Excel / Microsoft Excel → "Excel") and SAP submodule collapse
       (FI-AP, MM-IM → "SAP FI" / "SAP MM").
    2. If recognized, force the registry's category — that fixes drift like
       "Data Migration" being shelved under "SAP".
    3. Unknown skills keep their LLM-provided name and category, but the
       category must be in ALLOWED_CATEGORIES or the skill is dropped.

    The result is deduplicated (case-insensitive on canonical name) and
    rebuilt grouped by category in the canonical order — so identical input
    always yields identical output shape.
    """
    by_name: dict[str, tuple[str, str, int]] = {}

    for name, category, rating in _flatten_skills(skill_matrix):
        canonical = canonical_name(name)
        if canonical:
            target_name = canonical
            target_category = SKILL_REGISTRY[canonical][0]
        else:
            target_name = name
            target_category = category

        if target_category not in ALLOWED_CATEGORIES:
            continue

        key = target_name.lower()
        if key in by_name:
            existing_name, _existing_cat, existing_rating = by_name[key]
            # Higher rating wins; keep the canonical-cased name we already chose.
            by_name[key] = (existing_name, target_category, max(existing_rating, rating))
        else:
            by_name[key] = (target_name, target_category, rating)

    grouped: dict[str, list[dict]] = {cat: [] for cat in ALLOWED_CATEGORIES}
    for name, category, rating in by_name.values():
        grouped[category].append({"skill": name, "rating": rating})

    # Stable sort inside each category (rating desc, then name) — eliminates
    # ordering jitter between runs.
    for cat in grouped:
        grouped[cat].sort(key=lambda s: (-s["rating"], s["skill"].lower()))

    return [
        {"category": cat, "skills": grouped[cat]}
        for cat in ALLOWED_CATEGORIES
        if grouped[cat]
    ]


def _recompute_ratings_from_evidence(skill_matrix: list, projects: list) -> list:
    """Replace LLM-assigned ratings with deterministic ones for every skill we
    recognize. Unknown skills keep their LLM rating — they live outside the
    registry, so we have no evidence-based way to score them.

    The rating comes from `count_project_evidence` + `rating_from_evidence`,
    which is a pure function of the project list — so two runs with the same
    projects produce identical ratings, even if the LLM gave wildly different
    numbers on each pass.
    """
    out: list = []
    for group in skill_matrix or []:
        if not isinstance(group, dict):
            continue
        category = group.get("category")
        new_skills: list[dict] = []
        for s in group.get("skills", []) or []:
            if not isinstance(s, dict):
                continue
            name = (s.get("skill") or "").strip()
            if not name:
                continue
            try:
                rating = int(s.get("rating") or 4)
            except (TypeError, ValueError):
                rating = 4
            rating = max(1, min(10, rating))

            if name in SKILL_REGISTRY:
                evidence = count_project_evidence(name, projects)
                rating = rating_from_evidence(evidence)

            new_skills.append({"skill": name, "rating": rating})

        # Resort after rating change so the deterministic order survives.
        new_skills.sort(key=lambda s: (-s["rating"], s["skill"].lower()))
        if new_skills:
            out.append({"category": category, "skills": new_skills})
    return out


def _flatten_skills(skill_matrix: list) -> list[tuple[str, str, int]]:
    """Flattens a skill_matrix into [(skill_name, category, rating), ...]."""
    out = []
    for group in skill_matrix or []:
        if not isinstance(group, dict):
            continue
        category = group.get("category", "") or ""
        for s in group.get("skills", []) or []:
            if not isinstance(s, dict):
                continue
            name = (s.get("skill") or "").strip()
            if not name:
                continue
            try:
                rating = int(s.get("rating") or 4)
            except (TypeError, ValueError):
                rating = 4
            out.append((name, category, max(1, min(10, rating))))
    return out


def _fuse_skills(initial: list, mined: list) -> list:
    """Merge two skill_matrix lists.

    Dedup is case-insensitive on skill name. When the same skill appears in
    both lists the initial categorization wins (the first extractor sees the
    full CV and usually classifies better); the higher rating wins.
    Skills with unknown categories are dropped — mining can only contribute
    via the allowed category set.
    """
    by_name: dict[str, tuple[str, str, int]] = {}  # key = name.lower()

    for name, category, rating in _flatten_skills(initial):
        if category not in _ALLOWED_CATEGORIES:
            continue
        by_name[name.lower()] = (name, category, rating)

    for name, category, rating in _flatten_skills(mined):
        if category not in _ALLOWED_CATEGORIES:
            continue
        key = name.lower()
        if key in by_name:
            existing_name, existing_cat, existing_rating = by_name[key]
            by_name[key] = (existing_name, existing_cat, max(existing_rating, rating))
        else:
            by_name[key] = (name, category, rating)

    grouped: dict[str, list[dict]] = {cat: [] for cat in _ALLOWED_CATEGORIES}
    for name, category, rating in by_name.values():
        grouped[category].append({"skill": name, "rating": rating})

    return [
        {"category": cat, "skills": grouped[cat]}
        for cat in _ALLOWED_CATEGORIES
        if grouped[cat]
    ]


_SKILL_MINER_SYSTEM_PROMPT = """
Du bekommst:
1. Den Original-CV-Text in Markdown
2. Eine bereits extrahierte Skill-Liste

Deine Aufgabe: finde ZUSÄTZLICHE Skills, die im CV (besonders in den
Projektbeschreibungen und Trainings/Zertifikaten) sichtbar genutzt werden,
aber in der bestehenden Liste FEHLEN.

VORGEHEN:
- Lies jedes Projekt einzeln durch und notiere für dich, welche
  konkreten Tools, Frameworks, Plattformen, Sprachen, Datenbanken,
  SAP-Module und Methoden dort wirklich verwendet werden.
- Achte besonders auf nebenher genannte Technologien:
  "Implementierung mit Kafka und Redis" → Kafka, Redis fehlen oft.
- Leite methodische und Kommunikations-Skills aus klar beschriebenen
  Tätigkeiten ab (konservativ, nur wenn die Tätigkeit eindeutig ist):
    "Sprint Plannings moderiert"           → Scrum, Stakeholder Management
    "Workshops mit Fachbereichen"          → Moderation, Anforderungsanalyse
    "Neue Mitarbeiter eingearbeitet"       → Coaching, Wissensvermittlung
    "Präsentationen vor C-Level"           → Präsentationstechniken
    "Datenmigration nach S/4HANA geleitet" → Data Migration

REGELN:
- Keine Charakter-Soft-Skills (Teamfähigkeit, Belastbarkeit, Motivation,
  Eigeninitiative, Zuverlässigkeit, Flexibilität, Kommunikationsstark …).
  "Teamführung" als demonstrierte Tätigkeit OK, "Teamfähigkeit" als
  Eigenschaft NICHT.
- Keine Halluzinationen: jeder Skill MUSS im Original-CV belegbar sein.
- Wenn ein Skill schon in der Liste steht, NICHT erneut aufnehmen.
- Rating-Vergabe:
    rating 5  = Tätigkeit klar beschrieben, in einem Projekt sichtbar
    rating 6  = in mehreren Projekten sichtbar
    rating 7-8 = Hauptwerkzeug in mehreren Projekten / Senior-Kontext
  Mining-Skills bekommen NIE 9 oder 10 — die behält der Reviewer dem
  ursprünglichen Extractor vor.

ERLAUBTE KATEGORIEN (keine anderen!):
- Programming Languages
- Frameworks & Libraries
- Databases
- Tools & Platforms
- SAP (nur echte SAP-Module/-Produkte!)
- Data & Analytics
- Methods & Frameworks
- Communication & Training
- Languages

OUTPUT-FORMAT (raw JSON, keine Markdown-Fences):
{"skill_matrix": [{"category": "...", "skills": [{"skill": "...", "rating": N}]}]}

Wenn nichts Neues zu ergänzen ist:
{"skill_matrix": []}
""".strip()


def _mine_project_skills(raw_markdown: str, parsed_data: dict) -> list:
    """Second pass: scan the CV again with full focus on project evidence
    and return ONLY the additional skills that the initial extractor missed.

    Fail-safe: any error → returns [] so the caller falls back to the
    initial extraction unchanged.
    """
    import time

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=settings.openrouter_api_key)
    existing_skills = json.dumps(parsed_data.get("skill_matrix", []), ensure_ascii=False)
    user_message = (
        f"Original CV:\n{raw_markdown}\n\n"
        f"Already extracted skills:\n{existing_skills}"
    )

    try:
        logger.info("[MINE] Scanning projects for additional skills")
        t0 = time.perf_counter()
        response = client.chat.completions.create(
            model="google/gemini-2.5-flash",
            messages=[
                {"role": "system", "content": _SKILL_MINER_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0,
            top_p=0,
            seed=42,
            response_format={"type": "json_object"},
            timeout=60,
        )
        logger.info("[MINE] gemini-2.5-flash responded in %.2fs", time.perf_counter() - t0)

        if not response.choices or not response.choices[0].message.content:
            raise ValueError("Miner returned an empty response.")

        text = response.choices[0].message.content.strip()
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()

        mined = json.loads(text).get("skill_matrix", []) or []
        if not isinstance(mined, list):
            raise ValueError("skill_matrix is not a list")
        return mined

    except Exception as e:
        logger.warning("[MINE] Skill mining failed (%s) — continuing with initial extraction", e)
        return []


_REVIEWER_SYSTEM_PROMPT = """
Du bist ein QA-Reviewer für CV-Daten. Du bekommst zwei Dinge:
1. Den Original-CV-Text in Markdown
2. Ein JSON-Objekt, das ein anderes LLM aus diesem CV extrahiert hat

Deine Aufgabe: das JSON gegen das Original prüfen und korrigieren.
Gib das KORRIGIERTE JSON zurück — nicht eine Liste von Fehlern.
Wenn alles korrekt ist, gib das JSON unverändert zurück.

PRÜFREGELN:

1. HALLUZINATIONEN ENTFERNEN
   Jeder Skill, jedes Projekt, jeder Wert MUSS im Original belegt sein.
   Steht etwas im JSON aber nicht im Original → entfernen oder leeren.

2. SKILL-RATINGS PRÜFEN
   - 9-10 nur bei Senior/Lead/Expert-Rolle UND mehrfacher Projektbeleg
   - 7-8 nur wenn Skill als Hauptwerkzeug in mindestens einem Projekt
   - 5-6 wenn Skill in Projekten erwähnt, aber nicht Schwerpunkt
   - 3-4 wenn nur in Skill-Liste, kein Projektbeleg
   - 1-2 wenn nur beiläufig erwähnt
   Zu hohe Ratings ohne Beleg → herabstufen.

3. FEHLENDE SKILLS ERGÄNZEN — PROJEKTE SIND BELEGMATERIAL
   Gehe jedes Projekt einzeln durch. Wenn dort ein Tool, Framework,
   eine Plattform, Datenbank, ein SAP-Modul, eine Sprache oder eine
   Methode genutzt wird, die im JSON fehlt → ergänzen.
   Methodische und Kommunikations-Skills auch aus klar beschriebenen
   Tätigkeiten ableiten (konservativ):
     "Sprint Plannings moderiert"           → Scrum, Stakeholder Management
     "Workshops mit Fachbereichen"          → Moderation, Anforderungsanalyse
     "Neue Mitarbeiter eingearbeitet"       → Coaching, Wissensvermittlung
     "Präsentationen vor C-Level"           → Präsentationstechniken
     "Datenmigration nach S/4HANA geleitet" → Data Migration
   Nur ableiten, wenn die Tätigkeit eindeutig beschrieben ist — keine
   Ableitung aus vagen Andeutungen.

4. CHARAKTER-SOFT-SKILLS RAUS
   Eigenschaften wie Teamfähigkeit, Leadership, Belastbarkeit,
   Eigeninitiative, Zuverlässigkeit, Motivation, Flexibilität → entfernen.
   Unterscheidung:
   - "Kommunikation" als trainierte Fähigkeit (Workshops moderiert,
     Präsentationen vor Vorstand) → DRIN
   - "Kommunikationsstark" als Charaktereigenschaft → RAUS

5. ERFAHRUNGSJAHRE PRÜFEN
   total_experience_years muss zur Summe der Projektzeiträume passen.
   Überlappende Jahre nur einmal zählen. "Present" = 2026.
   Abweichung > 1 Jahr → korrigieren.

6. PERSÖNLICHE DATEN
   Name, E-Mail, Telefon, Standort: wortgenau aus dem CV. Keine
   "verbesserten" Varianten.

7. ERLAUBTE KATEGORIEN (keine anderen!)
   - Programming Languages
   - Frameworks & Libraries
   - Databases
   - Tools & Platforms
   - SAP
   - Data & Analytics
   - Methods & Frameworks
   - Communication & Training
   - Languages
   Falsch einsortierte Skills → richtige Kategorie. Doppelte Skills
   nur in die passendste Kategorie.

   SAP-Kategorie ist STRENG: nur echte SAP-Module/-Produkte
   (MM, SD, FI, CO, HCM, PP, WM, EWM, S/4HANA, Fiori, BW, ABAP, BTP,
   Ariba, SuccessFactors, Solution Manager, ...).
   Diese Skills gehören NICHT unter SAP, auch wenn sie im SAP-Kontext stehen:
   - "Data Migration", "Rollout", "Go-Live", "Cutover"     → Methods & Frameworks
   - "Training", "Schulungen", "Key User Training"         → Communication & Training
   - "Agile", "Waterfall", "Projektmanagement", "Change Management" → Methods & Frameworks
   - "ABAP" (programming) → Programming Languages, NICHT SAP
   - "HANA DB" als reine Datenbank → Databases
   Verschiebe alle solchen Einträge in die korrekte Kategorie.

8. FORMAT
   Gib NUR das korrigierte JSON zurück. Keine Erklärung, keine
   Markdown-Fences, kein Vorwort. Wenn nichts zu korrigieren ist:
   ursprüngliches JSON unverändert zurück.
""".strip()


def _count_skills(data: dict) -> int:
    """Helper for before/after logging of the reviewer pass."""
    total = 0
    for group in data.get("skill_matrix", []) or []:
        if isinstance(group, dict):
            total += len(group.get("skills", []) or [])
    return total


def review_cv_data(raw_markdown: str, parsed_data: dict) -> dict:
    """Second-pass QA review: an LLM checks the extracted JSON against the
    original CV and returns a corrected JSON.

    Fail-safe: if the reviewer call fails (timeout, API error, validation
    error after one retry), the original parsed_data is returned unchanged.
    Better an unreviewed CV in the DB than a crashed upload.
    """
    import time

    before_skills = _count_skills(parsed_data)
    logger.info("[REVIEW] Starting review (skills before: %d)", before_skills)

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=settings.openrouter_api_key)
    user_message = (
        f"Original CV:\n{raw_markdown}\n\n"
        f"Extracted JSON:\n{json.dumps(parsed_data, ensure_ascii=False)}"
    )
    messages = [
        {"role": "system", "content": _REVIEWER_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    max_retries = 2  # one initial attempt + one retry, then fail-safe
    for attempt in range(max_retries):
        text_content = None
        try:
            logger.info("[REVIEW] Attempt %d/%d", attempt + 1, max_retries)
            t0 = time.perf_counter()
            response = client.chat.completions.create(
                model="google/gemini-2.5-flash",
                messages=messages,
                temperature=0,
                top_p=0,
                seed=42,
                response_format={"type": "json_object"},
                timeout=60,
            )
            logger.info("[REVIEW] gemini-2.5-flash responded in %.2fs", time.perf_counter() - t0)

            if not response.choices or not response.choices[0].message.content:
                raise ValueError("Reviewer returned an empty response.")

            text_content = response.choices[0].message.content.strip()
            if text_content.startswith("```json"):
                text_content = text_content[7:-3].strip()
            elif text_content.startswith("```"):
                text_content = text_content[3:-3].strip()

            reviewed_json = json.loads(text_content)
            validated = CVData(**reviewed_json)
            reviewed_dict = validated.model_dump()

            after_skills = _count_skills(reviewed_dict)
            logger.info(
                "[REVIEW] Success — skills %d → %d (delta %+d)",
                before_skills, after_skills, after_skills - before_skills,
            )
            return reviewed_dict

        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("[REVIEW] Validation failed on attempt %d: %s", attempt + 1, e)
            if attempt == max_retries - 1:
                break
            if text_content:
                messages.append({"role": "assistant", "content": text_content})
            messages.append({
                "role": "user",
                "content": f"Your previous output failed validation. Please return only the corrected JSON.\nError: {e}",
            })

        except Exception as e:
            logger.warning("[REVIEW] API error on attempt %d: %s: %s", attempt + 1, type(e).__name__, e)
            if attempt == max_retries - 1:
                break

    logger.warning("[REVIEW] Reviewer failed — returning unreviewed parsed_data as fail-safe")
    return parsed_data


def parse_job_email(email_text: str) -> dict:
    """Extracts structured job requirements from a client email using the LLM."""
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=settings.openrouter_api_key)
    schema_json = JobRequirementData.model_json_schema()

    system_prompt = f"""
    Return ONLY a valid JSON object matching this schema:
    {json.dumps(schema_json)}

    RULES:
    1. TITLE — Extract the exact job title or role.
    2. DESCRIPTION — Summarize the role in 2-3 sentences. Be factual.
    3. REQUIRED SKILLS and NICE TO HAVE — CRITICAL FORMATTING RULES:
       a. Each entry must be ONE single skill or technology. Never combine two skills with "or/oder/and/und".
          WRONG: "SAP MM oder SD",  "Python oder Java",  "MM/SD"
          RIGHT: ["SAP MM", "SAP SD"],  ["Python", "Java"]
       b. Strip all qualifiers — output the bare skill name only.
          WRONG: "Python-Kenntnisse", "Erfahrung mit SAP", "gute Excel-Kenntnisse", "grundlegendes SQL"
          RIGHT: "Python", "SAP", "Excel", "SQL"
       c. Use the EXACT SAME canonical skill names a CV parser would output:
          - SAP modules: "SAP MM", "SAP SD", "SAP FI", "SAP CO", "SAP HCM", "SAP PP", "SAP WM", "SAP EWM", "SAP S/4HANA"
          - Programming: "Python", "Java", "C#", "C++", "JavaScript", "TypeScript", "SQL"
          - Databases: "PostgreSQL", "MySQL", "Oracle", "MongoDB"
          - Tools: "Git", "Docker", "Excel", "Power BI", "Jira", "Linux"
          - Spoken languages: always in English — "English", "German", "French", "Spanish"
       d. If a requirement lists alternative modules like "(MM oder SD)", split into one entry per module.
    4. EXPERIENCE — Minimum years as integer. Null if not mentioned.
    5. LOCATION — City or region. Null if not mentioned.
    6. REMOTE — true if remote explicitly offered, false if explicitly on-site only, null if unclear.
    7. FORMAT — No preamble, no markdown fences, raw JSON only.
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Client email:\n{email_text}"}
    ]

    max_retries = 3
    for attempt in range(max_retries):
        text_content = None
        try:
            logger.info("[JOB] Extraction attempt %d/%d", attempt + 1, max_retries)
            response = client.chat.completions.create(
                model="google/gemini-2.5-flash",
                messages=messages,
                temperature=0,
                seed=42,
                response_format={"type": "json_object"},
                timeout=60,
            )
            if not response.choices or not response.choices[0].message.content:
                raise ValueError("LLM returned an empty response.")

            text_content = response.choices[0].message.content.strip()
            if text_content.startswith("```json"):
                text_content = text_content[7:-3].strip()
            elif text_content.startswith("```"):
                text_content = text_content[3:-3].strip()

            parsed_json = json.loads(text_content)
            validated = JobRequirementData(**parsed_json)
            logger.info("[JOB] Extraction successful.")
            return validated.model_dump()

        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("[JOB] Validation failed on attempt %d: %s", attempt + 1, e)
            if attempt == max_retries - 1:
                raise ValueError(f"Failed to extract job data after {max_retries} attempts: {e}")
            if text_content:
                messages.append({"role": "assistant", "content": text_content})
            messages.append({"role": "user", "content": f"Fix the JSON. Error: {e}"})

        except Exception as e:
            logger.exception("[JOB] Unexpected error on attempt %d", attempt + 1)
            if attempt == max_retries - 1:
                raise ValueError(f"LLM API error: {e}")
