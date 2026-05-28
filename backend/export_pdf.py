import base64
import re
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_DESCRIPTION_SCORE_CAP = 500
_ONGOING_TOKENS = ("present", "current", "now", "today", "heute", "laufend", "aktuell")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

# One-pager constraints (landscape A4, must fit one page).
_ONEPAGER_MAX_PROJECTS = 5
_ONEPAGER_SUMMARY_SENTENCES = 4     # fills the middle-column gap under the name
_ONEPAGER_PROJECT_DESC_SENTENCES = 2
_ONEPAGER_PROJECT_DESC_MAX_CHARS = 200
_MIN_RATING_FOR_PDF = 8             # skills block: only ratings 8+ unless boosted
_MAX_SKILLS_PER_CATEGORY = 4        # tighter pitch: top 4 per category
_MAX_TOTAL_SKILLS = 12              # hard cap across all categories

# Display order for the unified Skills matrix (dots, rating-sorted per group).
# "Communication & Training" intentionally omitted — generic noise (Trainings, Workshops).
# "Languages" handled separately in the photo column, not in the skills matrix.
_SKILL_ORDER = (
    "Programming Languages",
    "Frameworks & Libraries",
    "Databases",
    "Tools & Platforms",
    "SAP",
    "Data & Analytics",
    "Methods & Frameworks",
)

_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


def _normalize_rating(value) -> int:
    try:
        r = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(10, r))


def _score_project(p: dict) -> tuple[int, int]:
    """Return (recency_year, capped_description_length). Higher = more 'ideal'.

    Recency wins; description length is a tiebreaker so projects with substance
    rank above one-liners from the same year.
    """
    duration = (p.get("duration") or "").lower()
    if any(token in duration for token in _ONGOING_TOKENS):
        recency = datetime.now().year
    else:
        years = _YEAR_RE.findall(duration)
        recency = max((int(y) for y in years), default=0)
    desc_len = min(len(p.get("description") or ""), _DESCRIPTION_SCORE_CAP)
    return (recency, desc_len)


def _short_summary(text: str, max_sentences: int) -> str:
    """Return the first N sentences of a longer summary, intact.

    The DB stores a detailed 4-6 sentence summary; the one-pager only shows the
    headline portion. Splits on sentence-ending punctuation followed by whitespace
    so we never cut a sentence mid-word.
    """
    if not text:
        return ""
    sentences = _SENTENCE_BOUNDARY.split(text.strip())
    return " ".join(sentences[:max_sentences]).strip()


def _trim_project_description(text: str) -> str:
    """Trim a project description for the one-pager: first N sentences,
    then a hard char cap on a word boundary, with ellipsis if truncated.
    """
    if not text:
        return ""
    short = _short_summary(text, _ONEPAGER_PROJECT_DESC_SENTENCES)
    if len(short) <= _ONEPAGER_PROJECT_DESC_MAX_CHARS:
        return short
    cut = short[:_ONEPAGER_PROJECT_DESC_MAX_CHARS]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",.;: ") + "…"


def _make_initials(full_name: str) -> str:
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        return "CV"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _prepare_context(data: dict) -> dict:
    """Build the one-pager (landscape) context.

    Personal data (email, phone, etc.) is intentionally OMITTED — the
    one-pager goes to clients and Quatelio's house style keeps contact
    info out. The Full-CV is the place for those details.
    """
    personal = data.get("personal_information", {}) or {}
    full_name = personal.get("full_name") or "Candidate Profile"

    raw_projects = [p for p in (data.get("projects") or []) if isinstance(p, dict)]
    sorted_projects = sorted(raw_projects, key=_score_project, reverse=True)

    # Job title = name of the most-recent project (the LLM uses `name` as the
    # role/title). Fallback to a neutral placeholder.
    job_title = ""
    for p in sorted_projects:
        if p.get("name"):
            job_title = p["name"]
            break

    # Unified Skills matrix: dots, configured categories, rating ≥ threshold,
    # sorted by rating desc, with per-category and total caps for one-page fit.
    skill_groups: dict[str, list[dict]] = {}
    for group in data.get("skill_matrix", []) or []:
        if not isinstance(group, dict):
            continue
        category = group.get("category") or ""
        if category not in _SKILL_ORDER:
            continue
        for s in group.get("skills", []) or []:
            if not isinstance(s, dict):
                continue
            name = (s.get("skill") or "").strip()
            if not name:
                continue
            rating = _normalize_rating(s.get("rating"))
            if rating < _MIN_RATING_FOR_PDF:
                continue
            skill_groups.setdefault(category, []).append(
                {"skill": name, "rating_int": rating}
            )

    skill_matrix = []
    total_skills = 0
    for cat in _SKILL_ORDER:
        if total_skills >= _MAX_TOTAL_SKILLS:
            break
        skills = skill_groups.get(cat) or []
        if not skills:
            continue
        skills.sort(key=lambda x: x["rating_int"], reverse=True)
        remaining = _MAX_TOTAL_SKILLS - total_skills
        take = min(len(skills), _MAX_SKILLS_PER_CATEGORY, remaining)
        skill_matrix.append({"category": cat, "skills": skills[:take]})
        total_skills += take

    top_projects = sorted_projects[:_ONEPAGER_MAX_PROJECTS]
    projects = [
        {
            "name": p.get("name", ""),
            "context": (p.get("industry") or p.get("location") or "").strip(),
            "description": _trim_project_description(p.get("description") or ""),
        }
        for p in top_projects
    ]

    languages = [
        {"name": (l.get("name") or "").strip(), "level": (l.get("level") or "").strip()}
        for l in (data.get("languages") or [])
        if isinstance(l, dict) and (l.get("name") or "").strip()
    ]

    return {
        "full_name": full_name,
        "initials": _make_initials(full_name),
        "job_title": job_title,
        "summary": _short_summary(data.get("small_summary", "") or "", _ONEPAGER_SUMMARY_SENTENCES),
        "skill_matrix": skill_matrix,
        "languages": languages,
        "projects": projects,
    }


_PHOTO_STORAGE_DIR = Path(__file__).resolve().parent / "data" / "photos"


def _photo_to_data_url(photo_path: str | Path | None) -> str | None:
    """Encode a stored JPEG into a data: URL so WeasyPrint can render it without
    any path-resolution worries. None → None (template falls back to initials).

    Accepts both the new bare-filename format ("42.jpg") and the legacy
    "data/photos/42.jpg" so old DB rows keep rendering.
    """
    if not photo_path:
        return None
    s = str(photo_path)
    p = Path(s)
    if p.is_absolute():
        resolved = p
    elif "/" not in s and "\\" not in s:
        resolved = _PHOTO_STORAGE_DIR / s
    else:
        # Legacy "data/photos/N.jpg" — relative to backend/
        resolved = Path(__file__).resolve().parent / s
    try:
        data = resolved.read_bytes()
    except Exception:
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def create_pdf_summary(data: dict, photo_path: str | Path | None = None) -> bytes:
    """Render the candidate profile to PDF bytes using WeasyPrint."""
    template = _env.get_template("cv_profile.html")
    context = _prepare_context(data)
    context["photo_data_url"] = _photo_to_data_url(photo_path)
    html_str = template.render(**context)
    return HTML(string=html_str, base_url=str(_TEMPLATE_DIR)).write_pdf()


# ── Full CV (multi-page, internal use) ─────────────────────────────────────

# Section ordering for the full CV's "Core Skills" block. Languages live in
# their own section above and must not appear here a second time.
_FULL_CV_CATEGORY_ORDER = (
    "Programming Languages",
    "Frameworks & Libraries",
    "Databases",
    "Tools & Platforms",
    "SAP",
    "Data & Analytics",
    "Methods & Frameworks",
    "Communication & Training",
)


# Personal-data row labels for the Full-CV's Personal Data block. Order matters
# — this is the on-page order. Each entry: (json_key, display_label).
_PERSONAL_ROW_ORDER = (
    ("age_or_dob",     "Date of Birth"),
    ("nationality",    "Nationality"),
    ("marital_status", "Marital Status"),
    ("location",       "Location"),
    ("email",          "E-Mail"),
    ("phone",          "Phone"),
    ("linkedin",       "LinkedIn"),
    ("website",        "Website"),
)


def _prepare_full_cv_context(data: dict) -> dict:
    personal = data.get("personal_information", {}) or {}
    full_name = personal.get("full_name") or "Candidate Profile"

    personal_rows = []
    for key, label in _PERSONAL_ROW_ORDER:
        value = (personal.get(key) or "")
        if isinstance(value, str):
            value = value.strip()
        if value:
            personal_rows.append({"label": label, "value": value})

    raw_projects = [p for p in (data.get("projects") or []) if isinstance(p, dict)]
    # Same chronological sort as the one-pager, but NO Top-5 cap — full CV.
    sorted_projects = sorted(raw_projects, key=_score_project, reverse=True)

    # Job title = most-recent project's name (= role/title in the LLM schema),
    # falls back to a neutral placeholder.
    job_title = ""
    for p in sorted_projects:
        if p.get("name"):
            job_title = p["name"]
            break

    projects = []
    for p in sorted_projects:
        name = (p.get("name") or "").strip()
        duration = (p.get("duration") or "").strip()
        industry = (p.get("industry") or "").strip()
        # Header format: "Job Title – Industry (Duration)"; pieces are dropped
        # gracefully when missing so we never end up with "  ( )".
        pieces = [name] if name else []
        if industry:
            pieces.append(f"– {industry}")
        if duration:
            pieces.append(f"({duration})")
        header = " ".join(pieces).strip() or "Position"
        projects.append({
            "header": header,
            "description": (p.get("description") or "").strip(),
        })

    # Spoken languages: prefer the dedicated `languages` field (current schema).
    # Fall back to skill_matrix Languages-category for legacy stored CVs.
    languages: list[str] = []
    for l in data.get("languages") or []:
        if not isinstance(l, dict):
            continue
        name = (l.get("name") or "").strip()
        if not name:
            continue
        level = (l.get("level") or "").strip()
        languages.append(f"{name} ({level})" if level else name)

    skill_lines: list[dict] = []
    grouped: dict[str, list[str]] = {}
    for group in data.get("skill_matrix", []) or []:
        if not isinstance(group, dict):
            continue
        cat = group.get("category") or ""
        names = []
        for s in group.get("skills", []) or []:
            if not isinstance(s, dict):
                continue
            n = (s.get("skill") or "").strip()
            if n:
                names.append(n)
        if not names:
            continue
        if cat == "Languages":
            # Legacy fallback only — current pipeline routes languages to the
            # dedicated field. Skip if we already populated from there.
            if not languages:
                languages.extend(names)
        else:
            grouped[cat] = names

    for cat in _FULL_CV_CATEGORY_ORDER:
        names = grouped.get(cat)
        if names:
            skill_lines.append({"category": cat, "skills": ", ".join(names)})

    return {
        "full_name": full_name,
        "job_title": job_title,
        "personal_rows": personal_rows,
        "summary": (data.get("small_summary") or "").strip(),
        # Fields the schema doesn't carry yet — kept as empty lists so the
        # template's "if" guards collapse the sections cleanly.
        "education": [],
        "certifications": [],
        "hobbies": [],
        "languages": languages,
        "skill_lines": skill_lines,
        "projects": projects,
    }


def create_full_cv_pdf(data: dict) -> bytes:
    """Render the multi-page Quatelio-format CV to PDF bytes."""
    template = _env.get_template("cv_full.html")
    context = _prepare_full_cv_context(data)
    html_str = template.render(**context)
    return HTML(string=html_str, base_url=str(_TEMPLATE_DIR)).write_pdf()
