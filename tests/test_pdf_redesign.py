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


def _require_real_weasyprint():
    """Skip unless WeasyPrint is genuinely installed. importorskip alone is
    not enough: test_upload_cv.py stubs `weasyprint` with a MagicMock so the
    backend imports in environments without Cairo/Pango (e.g. local Windows).
    A real install exposes a string __version__; the stub does not."""
    wp = pytest.importorskip("weasyprint")
    if not isinstance(getattr(wp, "__version__", None), str):
        pytest.skip("WeasyPrint is stubbed in this environment (runtime dep of the container)")


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


def test_full_cv_template_renders_portrait_with_new_sections():
    """The full CV is portrait single-column, but uses the same section
    structure as the one-pager (Professional Focus, Personal Background,
    Professional Background, Relevant Experience). All entries, no caps."""
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("cv_full.html")
    html = template.render(
        full_name="John Smith",
        role_title="Senior SAP Consultant",
        personal_rows=[{"label": "E-Mail", "value": "j.smith@example.com"},
                       {"label": "Location", "value": "Berlin"}],
        professional_focus=["Expert in SAP MM/SD", "ABAP development"],
        education_certificates=["TU Munich, Computer Science"],
        industries=["Automotive", "Manufacturing"],
        languages=[{"name": "English", "level": "fluent"}],
        professional_background=[
            {"duration": "2018-present", "company": "Quatelio", "role": "Senior Consultant", "note": None}
        ],
        relevant_experience=[
            {"client_label": "Leading German automotive supplier", "description": "S/4HANA rollout."}
        ],
    )
    assert "John Smith" in html
    assert "Senior SAP Consultant" in html
    assert "j.smith@example.com" in html
    assert "Berlin" in html
    assert "Expert in SAP MM/SD" in html
    assert "TU Munich" in html
    assert "Automotive" in html
    assert "Leading German automotive supplier" in html
    assert "S/4HANA rollout" in html


def test_prepare_context_caps_to_one_pager_limits():
    """One-pager caps each section so the result fits a single A4 page."""
    data = {
        "personal_information": {"full_name": "Test"},
        "relevant_experience": [{"client_label": f"Client {i}", "description": "x"} for i in range(20)],
        "professional_background": [{"duration": f"{i}", "company": "X", "role": "Y"} for i in range(20)],
        "professional_focus": [f"bullet {i}" for i in range(20)],
    }
    ctx = _prepare_context(data)
    # Caps modelled on BearingPoint reference profiles (full page coverage
    # via flex stretch; ~7 experience bullets is the comfortable sweet spot).
    assert len(ctx["relevant_experience"]) <= 7
    assert len(ctx["professional_background"]) <= 5
    assert len(ctx["professional_focus"]) <= 6


def test_prepare_context_caps_languages_and_truncates_education():
    """Left-column fields are bounded too: languages capped, long education
    entries truncated at a word boundary. Uncapped left-column content was
    a driver of the page-overflow incident (2026-06-03)."""
    data = {
        "personal_information": {"full_name": "Test"},
        "languages": [{"name": f"Lang {i}", "level": "good"} for i in range(12)],
        "education_certificates": [
            "Trainings zu SAP Finanzwesen (ECC und S/4HANA) mit sehr vielen "
            "Kurscodes, die diese Zeile weit über die Spaltenbreite hinaus verlängern: "
            "TFIN20, AC105, AC110, AC040, AC200, AC305, AC805, FSC020"
        ],
    }
    ctx = _prepare_context(data)
    assert len(ctx["languages"]) <= 6
    assert len(ctx["education_certificates"][0]) <= 111  # 110 + ellipsis
    assert ctx["education_certificates"][0].endswith("…")


def _worst_case_onepager_data():
    """A CV that maxes out every one-pager cap simultaneously — long entries
    in every section. If this fits one page, real CVs do too."""
    return {
        "personal_information": {"full_name": "Maximilian Mustermann-Langenname"},
        "role_title": "SAP BRIM Solution Architect & Programme Lead",
        "professional_focus": [
            f"Expert knowledge of SAP module family {i} including all related "
            "sub-components, integration scenarios and migration paths" for i in range(10)
        ],
        "relevant_experience": [
            {
                "client_label": f"Large international client organisation {i}",
                "description": "Responsible for solution architecture, functional design, "
                "stream leadership, integration testing, cutover planning and "
                "hypercare support across multiple parallel rollout waves with "
                "distributed teams." ,
            }
            for i in range(12)
        ],
        "professional_background": [
            {"duration": f"Jan 20{10+i} – Dec 20{11+i}", "company": f"Beratungshaus {i} GmbH & Co. KG",
             "role": "Principal SAP Finance Consultant – Stream Lead PSCD"}
            for i in range(10)
        ],
        "education_certificates": [
            "Trainings zu SAP Finanzwesen (ECC und S/4HANA) – TFIN20, AC105, AC110, "
            "AC040, AC200, AC305, AC805, FSC020 sowie weitere Aufbaukurse" for _ in range(8)
        ],
        "industries": ["Public Sector", "Retail", "Utilities", "Logistics", "Insurance",
                       "Automotive", "Pharma"],
        "languages": [{"name": f"Sprache {i}", "level": "fluent"} for i in range(10)],
    }


def test_trim_ladder_only_ever_tightens():
    """Every rung of the trim ladder must be <= the previous one on every
    knob — a rung that re-grows content would make the fit loop oscillate."""
    from dataclasses import fields
    from backend.export_pdf import _ONEPAGER_TRIM_LADDER

    for prev, nxt in zip(_ONEPAGER_TRIM_LADDER, _ONEPAGER_TRIM_LADDER[1:]):
        for f in fields(prev):
            assert getattr(nxt, f.name) <= getattr(prev, f.name), f.name


def test_one_pager_renders_exactly_one_page_worst_case():
    """Regression guard for the 2026-06-03 incident: a one-pager spilled onto
    pages 2-3 (WeasyPrint's flex-basis:auto stretch overshot the page, and
    over-long content fragmented onto extra pages). The fit loop must always
    deliver exactly one page — even for a CV maxing out every section.
    Needs WeasyPrint (runtime dep of the backend container) — skipped locally."""
    _require_real_weasyprint()
    from backend.export_pdf import _render_onepager_fitted

    document, rung = _render_onepager_fitted(_worst_case_onepager_data())
    assert len(document.pages) == 1


def test_one_pager_typical_cv_fits_without_trimming():
    """A typical CV must fit on rung 0 — trimming is the exception, not the
    rule. Guards against the ladder quietly becoming the default path."""
    _require_real_weasyprint()
    from backend.export_pdf import _render_onepager_fitted

    data = _worst_case_onepager_data()
    # Typical volume: ~Griffith-sized (the CV from the incident).
    data["professional_focus"] = data["professional_focus"][:6]
    data["relevant_experience"] = data["relevant_experience"][:7]
    data["professional_background"] = data["professional_background"][:5]
    data["education_certificates"] = data["education_certificates"][:5]
    data["languages"] = data["languages"][:2]

    document, rung = _render_onepager_fitted(data, photo_path=None)
    assert len(document.pages) == 1
    assert rung == 0


def test_prepare_full_cv_context_does_not_cap_new_fields():
    """Full CV has NO caps — every entry the LLM produced gets through.
    Uses the same field shape as the one-pager (cascade design)."""
    data = {
        "personal_information": {"full_name": "Test", "email": "t@example.com"},
        "role_title": "Test Role",
        "professional_focus": [f"bullet {i}" for i in range(20)],
        "relevant_experience": [{"client_label": f"Client {i}", "description": "x"} for i in range(20)],
        "professional_background": [{"duration": f"{i}", "company": "X", "role": "Y"} for i in range(20)],
        "education_certificates": [f"Cert {i}" for i in range(10)],
        "industries": [f"Ind {i}" for i in range(8)],
        "languages": [{"name": "English", "level": "fluent"}],
    }
    ctx = _prepare_full_cv_context(data)
    assert ctx["role_title"] == "Test Role"
    assert len(ctx["professional_focus"]) == 20
    assert len(ctx["relevant_experience"]) == 20
    assert len(ctx["professional_background"]) == 20
    assert len(ctx["education_certificates"]) == 10
    assert len(ctx["industries"]) == 8
    # personal_rows still derived from personal_information
    assert any(r["label"] == "E-Mail" for r in ctx["personal_rows"])
