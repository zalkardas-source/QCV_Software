import re
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_MAX_PROJECTS = 5
_DESCRIPTION_SCORE_CAP = 500
_ONGOING_TOKENS = ("present", "current", "now", "today", "heute", "laufend", "aktuell")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

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


def _make_initials(full_name: str) -> str:
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        return "CV"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _prepare_context(data: dict) -> dict:
    personal = data.get("personal_information", {}) or {}
    full_name = personal.get("full_name") or "Candidate Profile"

    # Subtitle: experience years + location, joined with separator dots
    subtitle_parts = []
    years = data.get("total_experience_years")
    if years:
        subtitle_parts.append(f"{years}+ years of experience")
    if personal.get("location"):
        subtitle_parts.append(personal["location"])

    # Contacts as list of dicts so the template can lay them out as a
    # wrapping flex grid (each item stays on one line, the row wraps).
    contacts = []
    if personal.get("email"):
        contacts.append({"icon": "✉", "value": personal["email"]})
    if personal.get("phone"):
        contacts.append({"icon": "✆", "value": personal["phone"]})
    if personal.get("linkedin"):
        contacts.append({"icon": "in", "value": personal["linkedin"]})
    if personal.get("website"):
        contacts.append({"icon": "⌘", "value": personal["website"]})

    skill_matrix = []
    for group in data.get("skill_matrix", []) or []:
        if not isinstance(group, dict):
            continue
        skills = []
        for s in group.get("skills", []) or []:
            if not isinstance(s, dict):
                continue
            skills.append({
                "skill": s.get("skill", ""),
                "rating_int": _normalize_rating(s.get("rating")),
            })
        skill_matrix.append({
            "category": group.get("category") or "General",
            "skills": skills,
        })

    raw_projects = [p for p in (data.get("projects") or []) if isinstance(p, dict)]
    top_projects = sorted(raw_projects, key=_score_project, reverse=True)[:_MAX_PROJECTS]
    projects = [
        {
            "name": p.get("name", ""),
            "duration": p.get("duration", ""),
            "description": p.get("description", ""),
        }
        for p in top_projects
    ]

    return {
        "full_name": full_name,
        "initials": _make_initials(full_name),
        "subtitle_parts": subtitle_parts,
        "contacts": contacts,
        "summary": data.get("small_summary", "") or "",
        "skill_matrix": skill_matrix,
        "projects": projects,
    }


def create_pdf_summary(data: dict) -> bytes:
    """Render the candidate profile to PDF bytes using WeasyPrint."""
    template = _env.get_template("cv_profile.html")
    html_str = template.render(**_prepare_context(data))
    return HTML(string=html_str, base_url=str(_TEMPLATE_DIR)).write_pdf()
