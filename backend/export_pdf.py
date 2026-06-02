import base64
import re
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape


def _weasy_html():
    """Lazy import of weasyprint.HTML so tests / context-builders can run
    without WeasyPrint installed (e.g. on Windows where Cairo/Pango DLLs
    aren't always set up). Only the actual PDF write needs it."""
    from weasyprint import HTML  # noqa: WPS433
    return HTML

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"
_PHOTO_STORAGE_DIR = Path(__file__).resolve().parent / "data" / "photos"

_DESCRIPTION_SCORE_CAP = 500
_ONGOING_TOKENS = ("present", "current", "now", "today", "heute", "laufend", "aktuell")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

# One-pager constraints (landscape A4, must fit one page).
# Tightened after real-world Frolov CV overflowed onto page 2.
_ONEPAGER_MAX_RELEVANT_EXPERIENCE = 6
_ONEPAGER_MAX_BACKGROUND = 3
_ONEPAGER_MAX_FOCUS = 5
_ONEPAGER_MAX_EDUCATION = 4
_ONEPAGER_MAX_INDUSTRIES = 5

_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)


def _score_project(p: dict) -> tuple[int, int]:
    """Return (recency_year, capped_description_length). Used to sort projects
    most-recent first for the heuristic backfill."""
    duration = (p.get("duration") or "").lower()
    if any(token in duration for token in _ONGOING_TOKENS):
        recency = datetime.now().year
    else:
        years = _YEAR_RE.findall(duration)
        recency = max((int(y) for y in years), default=0)
    desc_len = min(len(p.get("description") or ""), _DESCRIPTION_SCORE_CAP)
    return (recency, desc_len)


def _make_initials(full_name: str) -> str:
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        return "CV"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _split_sentences(text: str, max_count: int) -> list[str]:
    if not text:
        return []
    sentences = [s.strip() for s in _SENTENCE_BOUNDARY.split(text.strip()) if s.strip()]
    return sentences[:max_count]


def _backfill_new_fields(data: dict) -> dict:
    """Derive sensible defaults for the new layout's fields when an old CV
    (pre-redesign) is being rendered. Mutates `data` in-place and returns it.

    Old snapshots in cv_versions.snapshot_json predate role_title / professional_focus
    / relevant_experience / professional_background / education_certificates /
    industries — without backfill the new templates would show empty middle and
    right columns.

    Heuristics:
    - role_title  ← projects[0].name (already what the old one-pager used)
    - industries  ← deduped projects[].industry
    - relevant_experience ← projects[].{industry-or-name, description}
    - professional_background ← projects[].{duration, name as role, "" as company}
                                (legacy Project schema has no `company` field)
    - professional_focus ← first sentences of small_summary
    - education_certificates ← left empty (no source in legacy data)
    """
    projects_sorted = sorted(
        [p for p in (data.get("projects") or []) if isinstance(p, dict)],
        key=_score_project,
        reverse=True,
    )

    if not (data.get("role_title") or "").strip() if isinstance(data.get("role_title"), str) else not data.get("role_title"):
        for p in projects_sorted:
            if p.get("name"):
                data["role_title"] = p["name"]
                break

    if not data.get("industries"):
        seen: set[str] = set()
        industries: list[str] = []
        for p in projects_sorted:
            ind = (p.get("industry") or "").strip()
            if ind and ind.lower() not in seen:
                seen.add(ind.lower())
                industries.append(ind)
        data["industries"] = industries

    if not data.get("relevant_experience"):
        re_items: list[dict] = []
        for p in projects_sorted:
            label = (p.get("industry") or p.get("name") or "Project").strip()
            description = (p.get("description") or "").strip()
            if not description:
                continue
            re_items.append({"client_label": label, "description": description})
        data["relevant_experience"] = re_items

    if not data.get("professional_background"):
        bg_items: list[dict] = []
        for p in projects_sorted:
            duration = (p.get("duration") or "").strip()
            role = (p.get("name") or "").strip()
            if not (duration or role):
                continue
            bg_items.append({
                "duration": duration or "—",
                "company": "",
                "role": role or "—",
                "note": None,
            })
        data["professional_background"] = bg_items

    if not data.get("professional_focus"):
        sentences = _split_sentences(data.get("small_summary") or "", _ONEPAGER_MAX_FOCUS)
        data["professional_focus"] = sentences

    if "education_certificates" not in data or data["education_certificates"] is None:
        data["education_certificates"] = []

    return data


def _photo_to_data_url(photo_path: str | Path | None) -> str | None:
    """Encode a stored JPEG into a data: URL so WeasyPrint can render it
    without any path-resolution worries. None → None (template falls back to
    initials).

    Accepts the bare-filename format ("42.jpg") and legacy "data/photos/42.jpg".
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
        resolved = Path(__file__).resolve().parent / s
    try:
        data = resolved.read_bytes()
    except Exception:
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _logo_to_data_url() -> str | None:
    """Embed the Quatelio logo as a data URL for WeasyPrint. The file lives in
    backend/static/. Cached read on first call; None if the asset is missing
    so the template can fall back to a text wordmark."""
    path = _STATIC_DIR / "quatelio_logo.png"
    if not path.exists():
        return None
    try:
        data = path.read_bytes()
    except Exception:
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _prepare_context(data: dict) -> dict:
    """Build the one-pager context for the BearingPoint-style 3-column layout.

    Applies heuristic backfill so old snapshots render even without the new
    fields. The personal contact block is intentionally omitted on the
    one-pager (Quatelio house style — recruiter pitch, not contact sheet).
    """
    data = _backfill_new_fields(dict(data))
    personal = data.get("personal_information", {}) or {}
    full_name = personal.get("full_name") or "Candidate Profile"

    relevant_experience = list(data.get("relevant_experience") or [])[:_ONEPAGER_MAX_RELEVANT_EXPERIENCE]
    professional_background = list(data.get("professional_background") or [])[:_ONEPAGER_MAX_BACKGROUND]
    professional_focus = list(data.get("professional_focus") or [])[:_ONEPAGER_MAX_FOCUS]
    education_certificates = list(data.get("education_certificates") or [])[:_ONEPAGER_MAX_EDUCATION]
    industries = list(data.get("industries") or [])[:_ONEPAGER_MAX_INDUSTRIES]

    languages = [
        {"name": (l.get("name") or "").strip(), "level": (l.get("level") or "").strip()}
        for l in (data.get("languages") or [])
        if isinstance(l, dict) and (l.get("name") or "").strip()
    ]

    return {
        "full_name": full_name,
        "initials": _make_initials(full_name),
        "role_title": (data.get("role_title") or "").strip(),
        "professional_focus": professional_focus,
        "relevant_experience": relevant_experience,
        "professional_background": professional_background,
        "education_certificates": education_certificates,
        "industries": industries,
        "languages": languages,
        "logo_data_url": _logo_to_data_url(),
    }


def create_pdf_summary(data: dict, photo_path: str | Path | None = None) -> bytes:
    """Render the one-pager candidate profile to PDF bytes using WeasyPrint."""
    template = _env.get_template("cv_profile.html")
    context = _prepare_context(data)
    context["photo_data_url"] = _photo_to_data_url(photo_path)
    html_str = template.render(**context)
    return _weasy_html()(string=html_str, base_url=str(_TEMPLATE_DIR)).write_pdf()


# ── Full CV (classic portrait, but new section structure) ──────────────
# Same fields as the one-pager but NO caps — every entry the LLM produced
# is rendered. This makes the cascade Webseite → Full CV → One-Pager
# explicit: same data, three densities.


# Personal-data row labels for the Full CV's Personal Data block. Order
# matters — on-page order. (json_key, display_label).
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
    data = _backfill_new_fields(dict(data))
    personal = data.get("personal_information", {}) or {}
    full_name = personal.get("full_name") or "Candidate Profile"

    personal_rows = []
    for key, label in _PERSONAL_ROW_ORDER:
        value = (personal.get(key) or "")
        if isinstance(value, str):
            value = value.strip()
        if value:
            personal_rows.append({"label": label, "value": value})

    languages = [
        {"name": (l.get("name") or "").strip(), "level": (l.get("level") or "").strip()}
        for l in (data.get("languages") or [])
        if isinstance(l, dict) and (l.get("name") or "").strip()
    ]

    return {
        "full_name": full_name,
        "role_title": (data.get("role_title") or "").strip(),
        "personal_rows": personal_rows,
        # New sections — full lists, no caps.
        "professional_focus": list(data.get("professional_focus") or []),
        "education_certificates": list(data.get("education_certificates") or []),
        "industries": list(data.get("industries") or []),
        "languages": languages,
        "professional_background": list(data.get("professional_background") or []),
        "relevant_experience": list(data.get("relevant_experience") or []),
    }


def create_full_cv_pdf(data: dict, photo_path: str | Path | None = None) -> bytes:
    """Render the classic-portrait Quatelio Full CV (single column, all data).
    `photo_path` is accepted for API compatibility but the portrait template
    is intentionally text-only — photos live on the one-pager."""
    template = _env.get_template("cv_full.html")
    context = _prepare_full_cv_context(data)
    html_str = template.render(**context)
    return _weasy_html()(string=html_str, base_url=str(_TEMPLATE_DIR)).write_pdf()
