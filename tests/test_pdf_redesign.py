"""Tests for the BearingPoint-style PDF redesign:
- Schema accepts the new top-level fields with safe defaults.
- _backfill_new_fields heuristically populates new fields from old projects.
- Jinja templates render without crash for both old and new shapes.
"""
import sys
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.schemas import CVData, ProjectHighlight, BackgroundEntry
from backend.export_pdf import _backfill_new_fields, _prepare_context, _prepare_full_cv_context


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "backend" / "templates"


def test_cvdata_accepts_minimal_payload():
    """A minimal payload (legacy shape) still validates — all new fields are
    Optional with safe defaults."""
    d = CVData(personal_information={"full_name": "Jane Doe"})
    dump = d.model_dump()
    assert dump["role_title"] is None
    assert dump["professional_focus"] == []
    assert dump["relevant_experience"] == []
    assert dump["professional_background"] == []
    assert dump["education_certificates"] == []
    assert dump["industries"] == []


def test_cvdata_accepts_full_new_payload():
    """A payload with all the new fields validates."""
    d = CVData(
        personal_information={"full_name": "John Smith"},
        role_title="Senior SAP Consultant",
        professional_focus=["Expert in SAP MM/SD", "ABAP development"],
        relevant_experience=[
            {"client_label": "Leading German automotive supplier", "description": "SAP S/4HANA rollout."}
        ],
        professional_background=[
            {"duration": "03/2018 – present", "company": "Quatelio", "role": "Senior Consultant"}
        ],
        education_certificates=["TU Munich, Computer Science"],
        industries=["Automotive", "Manufacturing"],
    )
    assert d.role_title == "Senior SAP Consultant"
    assert len(d.relevant_experience) == 1
    assert isinstance(d.relevant_experience[0], ProjectHighlight)
    assert isinstance(d.professional_background[0], BackgroundEntry)


def test_backfill_role_title_from_projects():
    """Old CV without role_title gets it filled from the most recent project."""
    data = {
        "projects": [
            {"name": "Junior Dev", "duration": "2015-2017"},
            {"name": "Senior Consultant", "duration": "2020-present"},
        ],
    }
    out = _backfill_new_fields(data)
    assert out["role_title"] == "Senior Consultant"


def test_backfill_industries_deduplicates():
    """Industries get deduped (case-insensitive) from projects[].industry."""
    data = {
        "projects": [
            {"name": "P1", "industry": "Pharma"},
            {"name": "P2", "industry": "pharma"},
            {"name": "P3", "industry": "Banking"},
        ],
    }
    out = _backfill_new_fields(data)
    assert out["industries"] == ["Pharma", "Banking"] or out["industries"] == ["Banking", "Pharma"]
    # case-insensitive dedup: only one Pharma entry survives
    assert sum(1 for i in out["industries"] if i.lower() == "pharma") == 1


def test_backfill_does_not_override_existing_fields():
    """When new fields are already populated, the backfill leaves them alone."""
    data = {
        "role_title": "Custom Role",
        "industries": ["Custom"],
        "professional_focus": ["bullet one"],
        "projects": [{"name": "Old Project", "industry": "Automotive"}],
    }
    out = _backfill_new_fields(data)
    assert out["role_title"] == "Custom Role"
    assert out["industries"] == ["Custom"]
    assert out["professional_focus"] == ["bullet one"]


def test_backfill_relevant_experience_from_projects():
    """Relevant experience falls back to projects when LLM didn't curate."""
    data = {
        "projects": [
            {"name": "P1", "industry": "Pharma", "description": "S/4HANA rollout."},
            {"name": "P2", "industry": "", "description": "No industry given."},
            {"name": "P3", "industry": "Banking", "description": ""},  # no description → skipped
        ],
    }
    out = _backfill_new_fields(data)
    assert len(out["relevant_experience"]) == 2
    labels = [r["client_label"] for r in out["relevant_experience"]]
    assert "Pharma" in labels
    assert "P2" in labels  # fallback to name when industry is empty


def test_backfill_focus_from_summary():
    """Professional focus heuristic: split small_summary into sentences."""
    data = {
        "small_summary": "Senior SAP MM/SD consultant with 20 years of experience. "
                         "Specialized in rollouts and migrations. "
                         "Strong stakeholder communication."
    }
    out = _backfill_new_fields(data)
    assert len(out["professional_focus"]) == 3
    assert "20 years" in out["professional_focus"][0]


def test_one_pager_template_renders_with_minimal_data():
    """Template must render without crash even with sparse legacy data —
    relies on Jinja's `if` guards around every section."""
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("cv_profile.html")
    html = template.render(
        full_name="Jane Doe",
        initials="JD",
        role_title="",
        photo_data_url=None,
        logo_data_url=None,
        professional_focus=[],
        relevant_experience=[],
        professional_background=[],
        education_certificates=[],
        industries=[],
        languages=[],
    )
    assert "Jane Doe" in html
    # Empty sections must collapse (their boxes are inside `if` guards)
    assert "Professional Focus" not in html
    assert "Relevant Experience" not in html


def test_one_pager_template_renders_with_full_data():
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("cv_profile.html")
    html = template.render(
        full_name="John Smith",
        initials="JS",
        role_title="Senior SAP Consultant",
        photo_data_url=None,
        logo_data_url=None,
        professional_focus=["Expert in SAP MM/SD"],
        relevant_experience=[{"client_label": "BMW Group", "description": "Rollout."}],
        professional_background=[{"duration": "2018-present", "company": "Quatelio", "role": "Consultant", "note": None}],
        education_certificates=["TU Munich"],
        industries=["Automotive"],
        languages=[{"name": "English", "level": "fluent"}],
    )
    assert "Senior SAP Consultant" in html
    assert "BMW Group" in html
    assert "TU Munich" in html
    assert "Automotive" in html
    assert "Professional Focus" in html
    assert "Relevant Experience" in html


def test_full_cv_template_renders_with_contact_block():
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("cv_full.html")
    html = template.render(
        full_name="John Smith",
        initials="JS",
        role_title="Senior SAP Consultant",
        photo_data_url=None,
        logo_data_url=None,
        contact={"email": "j.smith@example.com", "phone": "+49 123", "location": "Berlin", "linkedin": "", "website": ""},
        professional_focus=["Expert in SAP MM/SD"],
        relevant_experience=[{"client_label": "BMW Group", "description": "Rollout."}],
        professional_background=[{"duration": "2018-present", "company": "Quatelio", "role": "Consultant", "note": None}],
        education_certificates=["TU Munich"],
        industries=["Automotive"],
        languages=[{"name": "English", "level": "fluent"}],
    )
    assert "j.smith@example.com" in html
    assert "Berlin" in html


def test_prepare_context_caps_to_one_pager_limits():
    """One-pager caps each section so the result fits a single page."""
    data = {
        "personal_information": {"full_name": "Test"},
        "relevant_experience": [{"client_label": f"Client {i}", "description": "x"} for i in range(20)],
        "professional_background": [{"duration": f"{i}", "company": "X", "role": "Y"} for i in range(20)],
        "professional_focus": [f"bullet {i}" for i in range(20)],
    }
    ctx = _prepare_context(data)
    assert len(ctx["relevant_experience"]) <= 8
    assert len(ctx["professional_background"]) <= 5
    assert len(ctx["professional_focus"]) <= 6


def test_prepare_full_cv_context_does_not_cap():
    """Full CV doesn't cap — all entries pass through."""
    data = {
        "personal_information": {"full_name": "Test"},
        "relevant_experience": [{"client_label": f"Client {i}", "description": "x"} for i in range(20)],
        "professional_background": [{"duration": f"{i}", "company": "X", "role": "Y"} for i in range(20)],
        "professional_focus": [f"bullet {i}" for i in range(20)],
    }
    ctx = _prepare_full_cv_context(data)
    assert len(ctx["relevant_experience"]) == 20
    assert len(ctx["professional_background"]) == 20
    assert len(ctx["professional_focus"]) == 20
