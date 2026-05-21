"""Skill-based candidate↔job matching.

Pure functions — no DB, no HTTP. Easy to test in isolation.
"""
import re


# Alias map: alternative form → canonical form.
# Each skill family has ONE canonical form. The matcher adds reverse lookups
# automatically (see match_score), so a job using either form still matches.
SKILL_ALIASES: dict[str, str] = {
    # Spoken languages — canonical = English
    "englisch": "english", "deutsch": "german", "französisch": "french",
    "spanisch": "spanish", "italienisch": "italian", "russisch": "russian",
    "chinesisch": "chinese", "japanisch": "japanese", "arabisch": "arabic",
    "portugiesisch": "portuguese", "niederländisch": "dutch", "türkisch": "turkish",
    "polnisch": "polish", "schwedisch": "swedish", "koreanisch": "korean",
    # MS Office — canonical = short form
    "microsoft excel": "excel", "ms excel": "excel",
    "microsoft word": "word", "ms word": "word",
    "microsoft powerpoint": "powerpoint", "ms powerpoint": "powerpoint",
    "microsoft office": "ms office", "microsoft 365": "ms office",
    # Tech — canonical chosen per family
    "node.js": "nodejs",
    "react.js": "react", "vue.js": "vue", "angular.js": "angular",
    "postgresql": "postgres",
    "js": "javascript", "ts": "typescript",
}

# Words that carry no skill meaning and should be ignored during token matching.
STOP_WORDS = {
    "oder", "or", "und", "and", "mit", "with", "für", "for", "im", "in",
    "von", "bei", "als", "an", "auf",
    "kenntnisse", "kenntnissen", "erfahrung", "erfahrungen",
    "grundkenntnisse", "grundwissen", "expertise", "knowledge",
    "skills", "skill", "experience", "grundlegende", "grundlegenden",
    "fundierte", "fundierten", "sehr", "gute", "guten", "solide", "tiefen",
}


# Level modifiers in required-skill strings → minimum rating (1-10).
# Keys are stems; required_level() also accepts up to 3 trailing chars to
# cover German inflections (fließend → fließende, fließendes, fließenden).
# Highest-matching modifier wins (e.g. "fließende Grundkenntnisse" → 8, not 3).
LEVEL_MODIFIERS: dict[str, int] = {
    # Highest tier (≥ 8) — full proficiency
    "fließend": 8, "fliessend": 8,
    "verhandlungssicher": 8,
    "muttersprachlich": 9, "muttersprache": 9, "native": 9,
    "expert": 8, "experte": 8, "expertin": 8,
    "fluent": 8,
    # Advanced (≥ 6)
    "fortgeschritten": 6,
    "advanced": 6, "proficient": 6,
    "tiefgehend": 6,
    # Solid (≥ 5)
    "intermediate": 5, "gut": 5,
    # Basic (≥ 3)
    "basic": 3, "basics": 3,
    "grundlegend": 3, "grundkenntnisse": 3, "grundwissen": 3,
}


def normalize(name: str) -> str:
    """Lowercase, strip, and apply alias mapping → canonical form."""
    n = name.lower().strip()
    return SKILL_ALIASES.get(n, n)


def tokens(name: str) -> set[str]:
    """Split a skill name into meaningful tokens, removing stop words."""
    words = re.split(r'[\s\-_/().,;:]+', normalize(name))
    return {w for w in words if len(w) >= 2 and w not in STOP_WORDS}


def required_level(skill: str) -> int:
    """Extract minimum required rating (1-10) from modifier words in a skill string.

    "Deutsch"                       → 1 (no modifier, anything counts)
    "fließende Deutschkenntnisse"   → 8 (fließend + -e inflection)
    "verhandlungssicheres Englisch" → 8 (verhandlungssicher + -es)
    "Grundkenntnisse Französisch"   → 3 (grundkenntnisse)
    "Python (Expert)"               → 8 (expert)
    Highest matching modifier wins. Modifier stems may have up to 3 trailing
    chars (covers German -e/-es/-er/-en/-em/-es endings).
    """
    words = re.split(r'[\s\-_/().,;:]+', skill.lower())
    levels = []
    for w in words:
        if not w:
            continue
        if w in LEVEL_MODIFIERS:
            levels.append(LEVEL_MODIFIERS[w])
            continue
        for stem, level in LEVEL_MODIFIERS.items():
            if w.startswith(stem) and len(w) - len(stem) <= 3:
                levels.append(level)
                break
    return max(levels) if levels else 1


# Score component weights. When a component is not applicable for a given
# match (e.g. job has no experience requirement), its weight is redistributed
# proportionally across the remaining components.
SKILLS_WEIGHT = 0.6
EXPERIENCE_WEIGHT = 0.25
LOCATION_WEIGHT = 0.15

# Required-skill threshold: if fewer than REQUIRED_THRESHOLD of required skills
# are matched, the final score is capped at REQUIRED_CAP. Prevents candidates
# with many nice-to-haves but no required skills from scoring high.
REQUIRED_THRESHOLD = 0.5  # 50% of required must be matched
REQUIRED_CAP = 30          # else score capped at 30


def _skill_match_score(candidate_skills: list, required: list, nice: list) -> dict:
    """Scores only the skill component. Returns score 0-100 + match details."""
    skill_map: dict[str, int] = {}
    for s in candidate_skills:
        raw = s.get("skill", "").lower().strip()
        if not raw:
            continue
        rating = s.get("rating", 4)
        skill_map[raw] = rating
        norm = normalize(raw)
        if norm != raw:
            skill_map[norm] = rating
        # Add reverse aliases (so "english" is findable when CV has "englisch")
        for alias_from, alias_to in SKILL_ALIASES.items():
            if raw == alias_to and alias_from not in skill_map:
                skill_map[alias_from] = rating

    def find(job_skill: str) -> int | None:
        needle = normalize(job_skill.lower().strip())
        # 1. Exact match (after normalization)
        if needle in skill_map:
            return skill_map[needle]
        # 2. Substring match
        for k, v in skill_map.items():
            if needle in k or k in needle:
                return v
        # 3. Token-overlap: for compound job requirements like "SAP Modulbetreuung (MM oder SD)".
        #    "SAP MM" candidate (tokens {"sap","mm"}) ⊆ job tokens {"sap","modulbetreuung","mm","sd"} → match.
        #    Guard: only apply candidate⊆job direction when candidate has ≥2 tokens,
        #    so single-word "SAP" does NOT falsely match multi-word "SAP MM".
        needle_tokens = tokens(job_skill)
        if needle_tokens:
            for k, v in skill_map.items():
                k_tokens = tokens(k)
                if not k_tokens:
                    continue
                # Candidate skill is a specific sub-skill of a compound requirement
                if len(k_tokens) >= 2 and k_tokens.issubset(needle_tokens):
                    return v
                # Candidate skill is a superset of the job requirement (candidate knows more)
                if needle_tokens.issubset(k_tokens):
                    return v
        return None

    total_weight = len(required) * 1.0 + len(nice) * 0.5
    if total_weight == 0:
        return {
            "score": 0,
            "matched_required": [],
            "partial_required": [],
            "missing_required": [],
            "matched_nice": [],
        }

    score = 0.0
    matched_required, partial_required, missing_required, matched_nice = [], [], [], []

    for skill in required:
        rating = find(skill)
        if rating is None:
            missing_required.append(skill)
            continue
        min_level = required_level(skill)
        if rating >= min_level:
            score += (rating / 10) * 1.0
            matched_required.append(skill)
        else:
            # Skill present but below required level — does not count toward
            # the score nor toward the required-skill threshold.
            partial_required.append(skill)

    for skill in nice:
        rating = find(skill)
        if rating is not None:
            score += (rating / 10) * 0.5
            matched_nice.append(skill)

    return {
        "score": round((score / total_weight) * 100),
        "matched_required": matched_required,
        "partial_required": partial_required,
        "missing_required": missing_required,
        "matched_nice": matched_nice,
    }


def _experience_score(candidate_years: int | None, required_years: int | None) -> int | None:
    """Scores experience match. Returns None when no comparison is possible.

    - required_years None or 0 → no requirement → None
    - candidate_years None → unknown, don't penalize → None
    - candidate >= required → 100
    - else linear: candidate / required * 100
    """
    if not required_years:
        return None
    if candidate_years is None:
        return None
    if candidate_years >= required_years:
        return 100
    if candidate_years <= 0:
        return 0
    return round((candidate_years / required_years) * 100)


def _is_remote_offered(remote: str | bool | None) -> bool:
    """Accepts the various representations of the JobRequirement.remote field."""
    if remote is True:
        return True
    if isinstance(remote, str):
        return remote.lower() == "true"
    return False


def _location_score(
    candidate_location: str | None,
    job_location: str | None,
    job_remote: str | bool | None,
) -> int | None:
    """Scores location match.

    - Job has no location requirement → None (skip)
    - Job offers remote → 100 (location irrelevant)
    - Candidate location unknown → None (don't penalize)
    - Substring match in either direction (e.g. 'Berlin' ↔ 'Berlin, Germany') → 100
    - Otherwise → 0
    """
    if not job_location:
        return None
    if _is_remote_offered(job_remote):
        return 100
    if not candidate_location:
        return None
    cand = candidate_location.lower().strip()
    job = job_location.lower().strip()
    if cand in job or job in cand:
        return 100
    return 0


def match_score(
    candidate_skills: list,
    required: list,
    nice: list,
    *,
    candidate_years: int | None = None,
    required_years: int | None = None,
    candidate_location: str | None = None,
    job_location: str | None = None,
    job_remote: str | bool | None = None,
) -> dict:
    """Scores a candidate against job requirements.

    Returns a dict with:
      - score: combined final score 0-100
      - skills_score, experience_score, location_score: component scores (None if N/A)
      - matched_required, partial_required, missing_required, matched_nice: skill lists
    """
    skill_part = _skill_match_score(candidate_skills, required, nice)
    skills_score = skill_part["score"]
    exp_score = _experience_score(candidate_years, required_years)
    loc_score = _location_score(candidate_location, job_location, job_remote)

    # Weighted combine — drop None components and renormalize.
    components: list[tuple[int, float]] = [(skills_score, SKILLS_WEIGHT)]
    if exp_score is not None:
        components.append((exp_score, EXPERIENCE_WEIGHT))
    if loc_score is not None:
        components.append((loc_score, LOCATION_WEIGHT))

    total_w = sum(w for _, w in components)
    combined = round(sum(s * w for s, w in components) / total_w)

    # Hard cap when too few required skills matched.
    if required:
        match_ratio = len(skill_part["matched_required"]) / len(required)
        if match_ratio < REQUIRED_THRESHOLD:
            combined = min(combined, REQUIRED_CAP)

    return {
        "score": combined,
        "skills_score": skills_score,
        "experience_score": exp_score,
        "location_score": loc_score,
        "matched_required": skill_part["matched_required"],
        "partial_required": skill_part["partial_required"],
        "missing_required": skill_part["missing_required"],
        "matched_nice": skill_part["matched_nice"],
    }
