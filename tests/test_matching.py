"""Unit tests for skill-based candidate↔job matching."""
from backend.matching import (
    match_score,
    normalize,
    tokens,
    REQUIRED_CAP,
    SKILLS_WEIGHT,
    EXPERIENCE_WEIGHT,
    LOCATION_WEIGHT,
)


# ── normalize() ──────────────────────────────────────────────────────────────

def test_normalize_lowercases_and_strips():
    assert normalize("  Python  ") == "python"


def test_normalize_applies_alias():
    assert normalize("Englisch") == "english"
    assert normalize("Microsoft Excel") == "excel"


def test_normalize_passes_unknown_through():
    assert normalize("Rust") == "rust"


# ── tokens() ─────────────────────────────────────────────────────────────────

def test_tokens_splits_on_separators():
    assert tokens("SAP MM") == {"sap", "mm"}
    assert tokens("Node.js") == tokens("node.js")  # alias applied first


def test_tokens_removes_stop_words():
    assert tokens("Python oder Java") == {"python", "java"}
    assert tokens("gute Excel Kenntnisse") == {"excel"}


def test_tokens_filters_short_words():
    # words shorter than 2 chars are dropped
    assert "a" not in tokens("a Python")


# ── match_score() — empty inputs ─────────────────────────────────────────────

def test_empty_requirements_returns_zero():
    result = match_score([], [], [])
    assert result["score"] == 0
    assert result["matched_required"] == []
    assert result["missing_required"] == []
    assert result["matched_nice"] == []


def test_no_candidate_skills_means_all_missing():
    result = match_score([], ["Python", "Java"], [])
    assert result["score"] == 0
    assert result["matched_required"] == []
    assert result["missing_required"] == ["Python", "Java"]


# ── match_score() — exact matches ────────────────────────────────────────────

def test_exact_match_full_score():
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(candidate, ["Python"], [])
    assert result["score"] == 100
    assert result["matched_required"] == ["Python"]
    assert result["missing_required"] == []


def test_no_match_zero_score():
    candidate = [{"skill": "Java", "rating": 10}]
    result = match_score(candidate, ["Rust"], [])
    assert result["score"] == 0
    assert result["missing_required"] == ["Rust"]


def test_partial_match():
    """One of two required skills matches at rating 10 → 50%."""
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(candidate, ["Python", "Rust"], [])
    # (10/10 * 1.0) / 2.0 = 0.5 → 50
    assert result["score"] == 50
    assert "Python" in result["matched_required"]
    assert "Rust" in result["missing_required"]


def test_rating_affects_score():
    """Lower rating yields proportionally lower score."""
    candidate = [{"skill": "Python", "rating": 5}]
    result = match_score(candidate, ["Python"], [])
    # 5/10 * 1.0 / 1.0 = 0.5 → 50
    assert result["score"] == 50


def test_missing_rating_defaults_to_4():
    candidate = [{"skill": "Python"}]
    result = match_score(candidate, ["Python"], [])
    # 4/10 → 40
    assert result["score"] == 40


# ── match_score() — aliases ──────────────────────────────────────────────────

def test_alias_german_cv_english_job():
    candidate = [{"skill": "Englisch", "rating": 8}]
    result = match_score(candidate, ["English"], [])
    assert "English" in result["matched_required"]
    assert result["score"] == 80


def test_alias_english_cv_german_job():
    """Reverse alias — CV in canonical form, job uses alias."""
    candidate = [{"skill": "English", "rating": 8}]
    result = match_score(candidate, ["Englisch"], [])
    assert "Englisch" in result["matched_required"]


def test_alias_ms_office_short_form():
    candidate = [{"skill": "Microsoft Excel", "rating": 7}]
    result = match_score(candidate, ["Excel"], [])
    assert "Excel" in result["matched_required"]


# ── match_score() — substring matching ───────────────────────────────────────

def test_substring_match_candidate_more_specific():
    """Candidate has 'SAP MM Consultant', job needs 'SAP MM'."""
    candidate = [{"skill": "SAP MM Consultant", "rating": 7}]
    result = match_score(candidate, ["SAP MM"], [])
    assert "SAP MM" in result["matched_required"]


def test_substring_match_job_more_specific():
    """Candidate has 'SAP', job needs 'SAP MM' — substring catches it."""
    candidate = [{"skill": "SAP", "rating": 6}]
    result = match_score(candidate, ["SAP MM"], [])
    assert "SAP MM" in result["matched_required"]


# ── match_score() — token-overlap (compound requirements) ────────────────────

def test_token_overlap_compound_parenthetical():
    """'SAP MM' candidate matches 'SAP Modulbetreuung (MM oder SD)' job."""
    candidate = [{"skill": "SAP MM", "rating": 8}]
    result = match_score(candidate, ["SAP Modulbetreuung (MM oder SD)"], [])
    assert result["matched_required"] == ["SAP Modulbetreuung (MM oder SD)"]


# ── match_score() — nice-to-have weighting ───────────────────────────────────

def test_nice_to_have_half_weighted():
    """Required matched, nice not matched → 1.0 / 1.5 = 67%."""
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(candidate, ["Python"], ["Docker"])
    assert result["score"] == 67
    assert result["matched_nice"] == []
    assert "Python" in result["matched_required"]


def test_nice_to_have_match_listed():
    candidate = [
        {"skill": "Python", "rating": 10},
        {"skill": "Docker", "rating": 6},
    ]
    result = match_score(candidate, ["Python"], ["Docker"])
    # (10/10 * 1.0) + (6/10 * 0.5) = 1.0 + 0.3 = 1.3
    # 1.3 / 1.5 = 0.866... → 87
    assert result["score"] == 87
    assert "Docker" in result["matched_nice"]


def test_only_nice_to_have_matched_capped_by_required_threshold():
    """No required matched → score capped at REQUIRED_CAP (Task #8)."""
    candidate = [{"skill": "Docker", "rating": 10}]
    result = match_score(candidate, ["Python"], ["Docker"])
    # Raw skills score would be 33, but missing all required caps it
    assert result["score"] == REQUIRED_CAP
    assert result["skills_score"] == 33  # raw skills score preserved
    assert result["missing_required"] == ["Python"]
    assert result["matched_nice"] == ["Docker"]


# ── match_score() — edge cases ───────────────────────────────────────────────

def test_empty_skill_names_skipped():
    candidate = [
        {"skill": "", "rating": 10},
        {"skill": "Python", "rating": 8},
    ]
    result = match_score(candidate, ["Python"], [])
    assert result["score"] == 80


def test_case_insensitive_matching():
    candidate = [{"skill": "PYTHON", "rating": 10}]
    result = match_score(candidate, ["python"], [])
    assert result["score"] == 100


def test_whitespace_tolerant():
    candidate = [{"skill": "  Python  ", "rating": 10}]
    result = match_score(candidate, ["Python"], [])
    assert result["score"] == 100


# ── match_score() — score breakdown ──────────────────────────────────────────

def test_result_includes_breakdown_keys():
    """Result always has skills_score and experience_score keys."""
    result = match_score([{"skill": "Python", "rating": 10}], ["Python"], [])
    assert "skills_score" in result
    assert "experience_score" in result
    assert result["skills_score"] == 100
    assert result["experience_score"] is None  # no experience info provided


# ── match_score() — experience scoring ───────────────────────────────────────

def test_experience_score_none_when_no_requirement():
    """Job has no experience_years → experience component ignored."""
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(candidate, ["Python"], [], candidate_years=5, required_years=None)
    assert result["experience_score"] is None
    assert result["score"] == 100  # skills only


def test_experience_score_none_when_candidate_unknown():
    """Old CVs without experience info → don't penalize."""
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(candidate, ["Python"], [], candidate_years=None, required_years=5)
    assert result["experience_score"] is None
    assert result["score"] == 100


def test_experience_full_match():
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(candidate, ["Python"], [], candidate_years=10, required_years=5)
    assert result["experience_score"] == 100
    # 0.7 * 100 + 0.3 * 100 = 100
    assert result["score"] == 100


def test_experience_partial():
    """Candidate has half the required years."""
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(candidate, ["Python"], [], candidate_years=2, required_years=4)
    assert result["experience_score"] == 50
    # Renormalized: (100*0.6 + 50*0.25) / 0.85 ≈ 85
    assert result["score"] == 85


def test_experience_zero_years():
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(candidate, ["Python"], [], candidate_years=0, required_years=5)
    assert result["experience_score"] == 0
    # Renormalized: (100*0.6 + 0*0.25) / 0.85 ≈ 71
    assert result["score"] == 71


def test_weighting_constants_sum_to_one():
    """Sanity check: weights must sum to 1.0."""
    assert SKILLS_WEIGHT + EXPERIENCE_WEIGHT + LOCATION_WEIGHT == 1.0


# ── match_score() — required-skill threshold ─────────────────────────────────

def test_required_threshold_caps_score():
    """Many nice-to-haves matched but few required → cap kicks in."""
    candidate = [
        {"skill": "Python", "rating": 10},
        {"skill": "Docker", "rating": 10},
        {"skill": "AWS", "rating": 10},
        {"skill": "Kubernetes", "rating": 10},
        {"skill": "Terraform", "rating": 10},
    ]
    # 1 of 4 required (25%) matched → ratio < 0.5 → cap
    required = ["Python", "Java", "Rust", "Go"]
    nice = ["Docker", "AWS", "Kubernetes", "Terraform"]
    # total_weight = 4 + 4*0.5 = 6
    # score = 1.0 (Python) + 4*0.5 (nice) = 3.0
    # raw skills score = 3/6 * 100 = 50
    result = match_score(candidate, required, nice)
    assert result["skills_score"] == 50  # uncapped
    assert result["score"] == REQUIRED_CAP  # capped at 30


# ── match_score() — location scoring ─────────────────────────────────────────

def test_location_score_none_when_job_has_no_location():
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(candidate, ["Python"], [], candidate_location="Berlin", job_location=None)
    assert result["location_score"] is None
    assert result["score"] == 100


def test_location_score_full_match():
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(
        candidate, ["Python"], [],
        candidate_location="Berlin", job_location="Berlin",
    )
    assert result["location_score"] == 100
    assert result["score"] == 100


def test_location_score_substring_match():
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(
        candidate, ["Python"], [],
        candidate_location="Berlin, Germany", job_location="Berlin",
    )
    assert result["location_score"] == 100


def test_location_remote_offered_overrides_mismatch():
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(
        candidate, ["Python"], [],
        candidate_location="Hamburg", job_location="Berlin", job_remote=True,
    )
    assert result["location_score"] == 100


def test_location_remote_string_true():
    """Remote field can be the string 'true' (stored that way in DB)."""
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(
        candidate, ["Python"], [],
        candidate_location="Hamburg", job_location="Berlin", job_remote="true",
    )
    assert result["location_score"] == 100


def test_location_mismatch_no_remote():
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(
        candidate, ["Python"], [],
        candidate_location="Hamburg", job_location="Berlin", job_remote=False,
    )
    assert result["location_score"] == 0
    # Components: skills=100 (0.6), location=0 (0.15) → 60/0.75 = 80
    assert result["score"] == 80


def test_location_candidate_unknown_returns_none():
    candidate = [{"skill": "Python", "rating": 10}]
    result = match_score(
        candidate, ["Python"], [],
        candidate_location=None, job_location="Berlin",
    )
    assert result["location_score"] is None


def test_required_threshold_not_triggered_at_half():
    """Match 2 of 4 required (50%) → exactly at threshold, no cap."""
    candidate = [
        {"skill": "Python", "rating": 10},
        {"skill": "Java", "rating": 10},
    ]
    required = ["Python", "Java", "Rust", "Go"]
    result = match_score(candidate, required, [])
    # 50% matched at full rating → skills score 50, no cap
    assert result["score"] == 50
