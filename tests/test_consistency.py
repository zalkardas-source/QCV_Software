"""Tests for skill-extraction consistency.

Two layers:

1. Pure-Python unit tests for the deterministic normalization layer
   (`backend/skill_normalization.py`). These run on every `pytest` invocation
   — no API key, no network.

2. An opt-in integration test that runs `structure_cv_data` three times on
   the same hard-coded CV markdown and reports a consistency report. Only
   runs when `OPENROUTER_API_KEY` is set in the environment (otherwise it
   skips). Use it to verify the consistency goal:
       - ≥ 90% identical skill names across runs
       - ratings differ by at most ±1
       - categories 100% identical for skills present in all runs

Run only the unit tests:
    pytest tests/test_consistency.py

Run the integration test too (paid LLM calls):
    OPENROUTER_API_KEY=... pytest -s tests/test_consistency.py
"""

from __future__ import annotations

import os

import pytest

from backend import services
from backend.skill_normalization import (
    ALLOWED_CATEGORIES,
    SKILL_REGISTRY,
    canonical_name,
    category_for,
    count_project_evidence,
    rating_from_evidence,
)


# ── Unit tests: canonical_name ─────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    # Spelling variants of common skills
    ("MS Excel",          "Excel"),
    ("Microsoft Excel",   "Excel"),
    ("excel",             "Excel"),
    ("Power BI",          "Power BI"),
    ("PowerBI",           "Power BI"),
    ("power-bi",          "Power BI"),
    ("MS Office",         "MS Office"),
    ("microsoft office",  "MS Office"),
    ("Node.js",           "Node.js"),
    ("nodejs",            "Node.js"),
    ("React.js",          "React"),
    ("PostgreSQL",        "PostgreSQL"),
    ("postgres",          "PostgreSQL"),
    ("MS SQL Server",     "SQL Server"),
    ("mssql",             "SQL Server"),
    ("js",                "JavaScript"),
    ("TS",                "TypeScript"),
    # German vs English communication / methods terms
    ("Workshops moderieren",   "Moderation"),
    ("Moderation von Workshops", "Moderation"),
    ("Workshop-Moderation",    "Moderation"),
    ("training",               "Trainings"),
    ("Schulung",               "Schulungen"),
    ("Datenmigration",         "Data Migration"),
    ("data migration",         "Data Migration"),
    ("Projektmanagement",      "Projektmanagement"),
    ("project management",     "Projektmanagement"),
])
def test_canonical_name_handles_known_variants(raw, expected):
    assert canonical_name(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    # SAP main modules + submodule collapse
    ("SAP MM",     "SAP MM"),
    ("MM",         "SAP MM"),
    ("SAP MM-IM",  "SAP MM"),
    ("MM-IM",      "SAP MM"),
    ("MM_MI",      "SAP MM"),
    ("MM/IV",      "SAP MM"),
    ("FI-AP",      "SAP FI"),
    ("CO-PA",      "SAP CO"),
    ("HCM-PY",     "SAP HCM"),
    # SAP products (full name + alias)
    ("S/4HANA",    "SAP S/4HANA"),
    ("sap s/4hana", "SAP S/4HANA"),
    ("Fiori",      "SAP Fiori"),
    ("Ariba",      "SAP Ariba"),
    ("successfactors", "SAP SuccessFactors"),
])
def test_canonical_name_handles_sap_variants(raw, expected):
    assert canonical_name(raw) == expected


@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "Some Completely Unknown Skill",
    "Internal Tool 7.3",
])
def test_canonical_name_returns_none_for_unknown(raw):
    assert canonical_name(raw) is None


# ── Unit tests: category_for ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected_category", [
    ("Python",            "Programming Languages"),
    ("ABAP",              "Programming Languages"),
    ("Excel",             "Data & Analytics"),
    ("Power BI",          "Data & Analytics"),
    ("SAP MM",            "SAP"),
    ("Fiori",             "SAP"),
    ("Scrum",             "Methods & Frameworks"),
    ("Data Migration",    "Methods & Frameworks"),
    ("Workshops",         "Communication & Training"),
    ("Moderation",        "Communication & Training"),
    ("English",           "Languages"),
    ("HANA DB",           "Databases"),
])
def test_category_for_returns_registry_category(raw, expected_category):
    assert category_for(raw) == expected_category
    assert expected_category in ALLOWED_CATEGORIES


def test_category_for_returns_none_for_unknown():
    assert category_for("Unknown Skill") is None


# ── Unit tests: count_project_evidence ─────────────────────────────────────

def test_evidence_count_counts_distinct_projects():
    projects = [
        {"name": "BMW Rollout", "description": "SAP MM rollout, training key users."},
        {"name": "BioNTech",    "description": "MM customizing and reporting."},
        {"name": "DB",          "description": "Java backend with Spring Boot."},
    ]
    # SAP MM appears in two projects (full name + bare "MM" alias)
    assert count_project_evidence("SAP MM", projects) == 2
    # Java appears in one
    assert count_project_evidence("Java", projects) == 1
    # PostgreSQL nowhere
    assert count_project_evidence("PostgreSQL", projects) == 0


def test_evidence_count_uses_word_boundaries():
    # "MM" should NOT match inside "ummary" or "commit"
    projects = [
        {"name": "Summary",     "description": "We made a commit to write a summary."},
        {"name": "Real MM work","description": "Real SAP MM work happened here."},
    ]
    assert count_project_evidence("SAP MM", projects) == 1


def test_evidence_count_is_case_insensitive():
    projects = [
        {"name": "X", "description": "EXCEL and excel and Excel all mentioned."},
    ]
    assert count_project_evidence("Excel", projects) == 1


# ── Unit tests: rating_from_evidence ───────────────────────────────────────

@pytest.mark.parametrize("count,expected", [
    (0, 4),
    (1, 6),
    (2, 7),
    (3, 8),
    (4, 8),
    (10, 8),
])
def test_rating_mapping(count, expected):
    assert rating_from_evidence(count) == expected


# ── Unit tests: _apply_category_overrides (services-level) ─────────────────

def test_overrides_normalize_spelling_variants():
    """Run a matrix full of spelling drift through the override layer — every
    variant must collapse to the canonical name and land in the right cat."""
    matrix = [
        {"category": "Data & Analytics", "skills": [
            {"skill": "MS Excel",          "rating": 7},
            {"skill": "Microsoft Excel",   "rating": 6},
            {"skill": "PowerBI",           "rating": 5},
            {"skill": "Power-BI",          "rating": 8},
        ]},
        {"category": "Programming Languages", "skills": [
            {"skill": "nodejs",   "rating": 5},
            {"skill": "Node.js",  "rating": 7},
        ]},
    ]
    out = services._apply_category_overrides(matrix)
    flat = {name: (cat, rating) for name, cat, rating in services._flatten_skills(out)}
    # Spelling variants must collapse
    assert "Excel" in flat
    assert "MS Excel" not in flat
    assert "Microsoft Excel" not in flat
    assert flat["Excel"][1] == 7   # max rating wins
    assert "Power BI" in flat
    assert flat["Power BI"][1] == 8


def test_overrides_force_canonical_category():
    """Even if the LLM puts a known skill in the wrong category, the registry
    forces the right one."""
    matrix = [
        {"category": "SAP", "skills": [
            {"skill": "Data Migration",   "rating": 7},
            {"skill": "Workshops",        "rating": 6},
            {"skill": "ABAP",             "rating": 8},
        ]},
    ]
    out = services._apply_category_overrides(matrix)
    flat = {name: (cat, rating) for name, cat, rating in services._flatten_skills(out)}
    assert flat["Data Migration"][0] == "Methods & Frameworks"
    assert flat["Workshops"][0] == "Communication & Training"
    assert flat["ABAP"][0] == "Programming Languages"


def test_overrides_unknown_skill_keeps_llm_category():
    """A skill not in the registry should be passed through as-is, provided
    the LLM put it in an allowed category."""
    matrix = [
        {"category": "Tools & Platforms", "skills": [
            {"skill": "Some Proprietary Tool", "rating": 5},
        ]},
    ]
    out = services._apply_category_overrides(matrix)
    flat = {name: (cat, rating) for name, cat, rating in services._flatten_skills(out)}
    assert flat["Some Proprietary Tool"][0] == "Tools & Platforms"


def test_overrides_drop_unknown_category():
    """A skill with a category outside the allow-list is dropped (no Soft Skills)."""
    matrix = [
        {"category": "Soft Skills", "skills": [
            {"skill": "Leadership", "rating": 9},
        ]},
        {"category": "Programming Languages", "skills": [
            {"skill": "Python", "rating": 8},
        ]},
    ]
    out = services._apply_category_overrides(matrix)
    flat_names = [name for name, _, _ in services._flatten_skills(out)]
    assert "Leadership" not in flat_names
    assert "Python" in flat_names


# ── Unit tests: _recompute_ratings_from_evidence ───────────────────────────

def test_recompute_ratings_uses_project_evidence():
    matrix = [
        {"category": "Programming Languages", "skills": [
            {"skill": "Python", "rating": 10},  # LLM hallucinated rating
            {"skill": "Java",   "rating": 9},
        ]},
    ]
    projects = [
        {"name": "X", "description": "Used Python and pandas heavily."},
        {"name": "Y", "description": "More Python work."},
        {"name": "Z", "description": "And a third Python project."},
    ]
    out = services._recompute_ratings_from_evidence(matrix, projects)
    flat = {name: rating for name, _, rating in services._flatten_skills(out)}
    # Python: 3 project mentions → rating 8
    assert flat["Python"] == 8
    # Java: 0 mentions → rating 4 (capped down from LLM's 9)
    assert flat["Java"] == 4


def test_recompute_ratings_keeps_llm_rating_for_unknown_skills():
    """Skills not in the registry have no evidence-based path; the LLM rating
    survives unchanged."""
    matrix = [
        {"category": "Tools & Platforms", "skills": [
            {"skill": "Proprietary Tool", "rating": 7},
        ]},
    ]
    out = services._recompute_ratings_from_evidence(matrix, projects=[])
    flat = {name: rating for name, _, rating in services._flatten_skills(out)}
    assert flat["Proprietary Tool"] == 7


def test_recompute_ratings_is_deterministic():
    """Same input → same output, every time."""
    matrix = [
        {"category": "Programming Languages", "skills": [
            {"skill": "Python", "rating": 5},
            {"skill": "Java",   "rating": 9},
        ]},
        {"category": "Data & Analytics", "skills": [
            {"skill": "Excel", "rating": 6},
        ]},
    ]
    projects = [
        {"name": "P1", "description": "Python and Excel."},
        {"name": "P2", "description": "Python only."},
    ]
    a = services._recompute_ratings_from_evidence(matrix, projects)
    b = services._recompute_ratings_from_evidence(matrix, projects)
    c = services._recompute_ratings_from_evidence(matrix, projects)
    assert a == b == c


# ── Optional integration test: real LLM, 3 runs ────────────────────────────
# Hard-coded sample CV — small enough to keep token cost low, varied enough
# to exercise multiple categories.
_SAMPLE_CV_MARKDOWN = """
# Max Mustermann

Email: max@example.com
Phone: +49 170 1234567
Location: Berlin, Germany

## Summary
Senior SAP Functional Consultant with 12 years of experience in SAP MM and
SAP S/4HANA migrations. Strong background in stakeholder workshops, key user
training, and data migration.

## Skills
- SAP: SAP MM, SAP SD, SAP S/4HANA, Fiori
- Languages: ABAP, Python, SQL
- Methods: Agile, Scrum, Data Migration, Cutover
- Tools: Excel, Jira, Git, Power BI
- Communication: Workshop-Moderation, Key User Training
- Spoken: German (native), English (fluent), French (basic)

## Projects

### BMW Group — SAP S/4HANA Migration (2022-2024)
Industry: Automotive. Location: Munich, Germany.
Led the S/4HANA migration for the materials management (SAP MM) domain.
Moderated weekly fachbereichs-workshops with key users. Delivered three
training sessions per quarter. Owned cutover planning and go-live support.
Tools: SAP MM, SAP S/4HANA, Jira, Excel.

### BioNTech — Rollout EU (2020-2022)
Industry: Pharma. Location: Mainz, Germany.
Coordinated the SAP MM and SAP SD rollout across five European subsidiaries.
Designed the data migration approach. Ran key user training in English and
German. Reported progress to the program board.

### Deutsche Bank — Reporting Refresh (2018-2020)
Industry: Banking. Location: Frankfurt, Germany.
Implemented Power BI dashboards on top of SAP BW data. Wrote ABAP extractors.
Trained finance analysts in SQL and Power BI. Stakeholder management with
the controlling team.
""".strip()


def _flat_skill_dict(parsed: dict) -> dict[str, tuple[str, int]]:
    """{skill_name -> (category, rating)} from a structure_cv_data result."""
    return {
        name: (cat, rating)
        for name, cat, rating in services._flatten_skills(parsed.get("skill_matrix", []))
    }


def _consistency_report(runs: list[dict]) -> dict:
    """Compute a consistency report from N runs of structure_cv_data.

    Metrics:
    - skill_name_overlap_pct: |intersection| / |union| of skill names (Jaccard)
    - max_rating_delta: largest rating spread for any skill present in all runs
    - category_mismatch_count: skills present in all runs whose category differs
    - per_run_skill_counts: how many skills each run produced
    """
    flats = [_flat_skill_dict(r) for r in runs]
    name_sets = [set(f.keys()) for f in flats]
    intersection = set.intersection(*name_sets) if name_sets else set()
    union = set.union(*name_sets) if name_sets else set()
    overlap = (len(intersection) / len(union)) if union else 1.0

    max_delta = 0
    cat_mismatches = []
    for skill in intersection:
        ratings = [f[skill][1] for f in flats]
        categories = {f[skill][0] for f in flats}
        max_delta = max(max_delta, max(ratings) - min(ratings))
        if len(categories) > 1:
            cat_mismatches.append((skill, categories))

    return {
        "skill_name_overlap_pct": round(overlap * 100, 1),
        "max_rating_delta": max_delta,
        "category_mismatch_count": len(cat_mismatches),
        "category_mismatches": cat_mismatches,
        "per_run_skill_counts": [len(s) for s in name_sets],
        "intersection_size": len(intersection),
        "union_size": len(union),
    }


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="Requires OPENROUTER_API_KEY for live LLM calls",
)
def test_structure_cv_data_is_consistent_across_runs(capsys):
    """Calls `structure_cv_data` three times on the same CV markdown and
    asserts the consistency goal: ≥90% name overlap, max ±1 rating delta,
    zero category mismatches.

    NOTE: This test runs real LLM calls and costs money. It is skipped by
    default. Run with OPENROUTER_API_KEY set to enable.
    """
    runs = []
    for i in range(3):
        result = services.structure_cv_data(_SAMPLE_CV_MARKDOWN)
        runs.append(result)
        print(f"\n=== Run {i+1} skills ({len(_flat_skill_dict(result))}) ===")
        for name, (cat, rating) in sorted(_flat_skill_dict(result).items()):
            print(f"  [{cat:30}] {name:30} → {rating}")

    report = _consistency_report(runs)
    print("\n=== Consistency report ===")
    for k, v in report.items():
        print(f"  {k}: {v}")

    assert report["skill_name_overlap_pct"] >= 90.0, (
        f"Skill name overlap too low: {report['skill_name_overlap_pct']}% "
        f"(intersection {report['intersection_size']} / union {report['union_size']})"
    )
    assert report["max_rating_delta"] <= 1, (
        f"Ratings drifted by {report['max_rating_delta']} (> 1 allowed)"
    )
    assert report["category_mismatch_count"] == 0, (
        f"Category mismatches: {report['category_mismatches']}"
    )
