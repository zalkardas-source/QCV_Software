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

# Docling converters are initialized lazily and cached globally.
# We keep two: a fast text-only one (default), and an OCR-enabled fallback
# for scanned PDFs. Most modern CVs are digital and don't need OCR.
_converter_fast = None
_converter_ocr = None

# If the fast pass returns less text than this, we assume it's a scanned
# PDF and re-run with OCR. 100 chars is well below any real CV's body text.
_OCR_FALLBACK_MIN_CHARS = 100


def _build_converter(do_ocr: bool, do_tables: bool):
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = do_ocr
    pipeline_options.do_table_structure = do_tables
    pipeline_options.images_scale = 0.0
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def get_docling_converter(with_ocr: bool = False):
    global _converter_fast, _converter_ocr
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

def structure_cv_data(raw_markdown: str) -> dict:
    """Structures CV Markdown into strict JSON using Minimax-m2.7 with optimized prompt."""
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=settings.openrouter_api_key)
    schema_json = CVData.model_json_schema()
    
    system_prompt = f"""
    Return ONLY a valid JSON object matching this schema:
    {json.dumps(schema_json)}

    RULES:

    1. SKILLS — Extract every skill mentioned anywhere in the CV.
       Use these standard categories (create a new one only if truly nothing fits):
       - "Programming Languages"  (Python, Java, C#, SQL, ...)
       - "Frameworks & Libraries" (React, Django, Spring, ...)
       - "Databases"              (PostgreSQL, MongoDB, Oracle, ...)
       - "Tools & Platforms"      (Git, Docker, AWS, Jira, Linux, ...)
       - "SAP"                    (SAP ERP, S/4HANA, FI, CO, MM, SD, ...)
       - "Data & Analytics"       (Excel, Power BI, Tableau, Pandas, ...)
       - "Languages"              (English, German, French, ... — spoken languages only)
       Do NOT extract soft skills (Leadership, Communication, Teamwork, ...) — skip them entirely.
       Do NOT repeat the same skill in multiple categories.

    2. RATINGS — Assign 1-10 based on actual evidence across the entire CV.
       Cross-reference every skill against the work experience and projects:
       - 9-10: expert/lead/architect label AND confirmed by multiple projects as primary technology
       - 7-8:  advanced level AND appears as a main tool in at least one significant role
       - 5-6:  used regularly, present in project descriptions but not the main focus
       - 3-4:  mentioned once or only in a skills list with no supporting project evidence
       - 1-2:  briefly mentioned, no evidence of real use anywhere
       If the candidate claims a high level but no project confirms it, cap the rating at 4.
       Never rely solely on what the candidate states — the projects are the ground truth.

    3. SUMMARY — Write 2-3 sentences max. Mention the candidate's field, years of experience,
       and strongest skills. Be factual, no marketing language.

    4. EXPERIENCE — Extract every job and project. Keep descriptions in the original language.

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
                model="openai/gpt-4o-mini",
                messages=messages,
                temperature=0,
                seed=42,
                response_format={"type": "json_object"},
                timeout=60,
            )
            logger.info("[LLM] gpt-4o-mini responded in %.2fs", time.perf_counter() - t0)
            
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
            # Return as dict for the database
            return validated_data.model_dump()
            
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
                model="openai/gpt-4o-mini",
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
