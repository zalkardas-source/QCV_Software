"""Deterministic skill normalization layer.

This module is the source of truth for two things the LLM cannot do reliably:
- canonical naming of a skill (so "MS Excel", "Microsoft Excel", "Excel" all
  become "Excel")
- which category a skill belongs to

It is applied *after* every LLM pass. Adding an alias here immediately
stabilizes every future extraction run.

Structure:
    SKILL_REGISTRY[<canonical>] = (<category>, [<alias>, ...])

Add a new skill by adding a registry entry. Aliases are case-insensitive and
matched with word-boundary semantics so short codes (MM, SD, FI) don't trigger
on random substrings.
"""

from __future__ import annotations

import functools
import re

# The nine categories that may appear in CVData.skill_matrix. Order is the
# canonical sort order used when rebuilding the matrix downstream.
ALLOWED_CATEGORIES: tuple[str, ...] = (
    "Programming Languages",
    "Frameworks & Libraries",
    "Databases",
    "Tools & Platforms",
    "SAP",
    "Data & Analytics",
    "Methods & Frameworks",
    "Communication & Training",
    "Languages",
)


# SKILL_REGISTRY: canonical_name -> (category, [aliases])
# Aliases are matched lowercased + word-bounded. The canonical name itself is
# always recognized (no need to repeat it in the alias list).
SKILL_REGISTRY: dict[str, tuple[str, list[str]]] = {
    # ── Programming Languages ──────────────────────────────────────────────
    "Python":     ("Programming Languages", []),
    "Java":       ("Programming Languages", []),
    "C#":         ("Programming Languages", ["csharp", "c sharp"]),
    "C++":        ("Programming Languages", ["cpp"]),
    "C":          ("Programming Languages", []),  # bare "C" only by exact match
    "JavaScript": ("Programming Languages", ["js"]),
    "TypeScript": ("Programming Languages", ["ts"]),
    "Go":         ("Programming Languages", ["golang"]),
    "Rust":       ("Programming Languages", []),
    "Ruby":       ("Programming Languages", []),
    "PHP":        ("Programming Languages", []),
    "Scala":      ("Programming Languages", []),
    "Kotlin":     ("Programming Languages", []),
    "Swift":      ("Programming Languages", []),
    "SQL":        ("Programming Languages", []),
    "PL/SQL":     ("Programming Languages", ["plsql"]),
    "T-SQL":      ("Programming Languages", ["tsql"]),
    "Bash":       ("Programming Languages", ["shell", "shell scripting"]),
    "PowerShell": ("Programming Languages", []),
    "ABAP":       ("Programming Languages", ["sap abap"]),
    "HTML":       ("Programming Languages", []),
    "CSS":        ("Programming Languages", []),

    # ── Frameworks & Libraries ─────────────────────────────────────────────
    "React":      ("Frameworks & Libraries", ["react.js", "reactjs"]),
    "Vue":        ("Frameworks & Libraries", ["vue.js", "vuejs"]),
    "Angular":    ("Frameworks & Libraries", ["angular.js", "angularjs"]),
    "Node.js":    ("Frameworks & Libraries", ["nodejs", "node js"]),
    "Django":     ("Frameworks & Libraries", []),
    "Flask":      ("Frameworks & Libraries", []),
    "FastAPI":    ("Frameworks & Libraries", ["fast api"]),
    "Spring":     ("Frameworks & Libraries", ["spring boot", "spring-boot", "springboot"]),
    "Express":    ("Frameworks & Libraries", ["express.js", "expressjs"]),
    ".NET":       ("Frameworks & Libraries", ["dotnet", "asp.net", "asp .net"]),
    "Next.js":    ("Frameworks & Libraries", ["nextjs", "next js"]),
    "Tailwind CSS": ("Frameworks & Libraries", ["tailwind", "tailwindcss"]),
    "Bootstrap":  ("Frameworks & Libraries", []),
    "jQuery":     ("Frameworks & Libraries", []),

    # ── Databases ──────────────────────────────────────────────────────────
    "PostgreSQL": ("Databases", ["postgres"]),
    "MySQL":      ("Databases", []),
    "MongoDB":    ("Databases", ["mongo"]),
    "Oracle":     ("Databases", ["oracle db", "oracle database"]),
    "SQL Server": ("Databases", ["ms sql server", "mssql", "microsoft sql server"]),
    "Redis":      ("Databases", []),
    "HANA DB":    ("Databases", ["hana", "sap hana", "hana database"]),
    "Elasticsearch": ("Databases", ["elastic search"]),
    "SQLite":     ("Databases", []),
    "DynamoDB":   ("Databases", []),
    "Cassandra":  ("Databases", []),

    # ── Tools & Platforms ──────────────────────────────────────────────────
    "Git":         ("Tools & Platforms", []),
    "Docker":      ("Tools & Platforms", []),
    "Kubernetes":  ("Tools & Platforms", ["k8s"]),
    "AWS":         ("Tools & Platforms", ["amazon web services"]),
    "Azure":       ("Tools & Platforms", ["microsoft azure"]),
    "GCP":         ("Tools & Platforms", ["google cloud", "google cloud platform"]),
    "Jira":        ("Tools & Platforms", []),
    "Confluence":  ("Tools & Platforms", []),
    "Linux":       ("Tools & Platforms", []),
    "Windows":     ("Tools & Platforms", []),
    "macOS":       ("Tools & Platforms", []),
    "Kafka":       ("Tools & Platforms", ["apache kafka"]),
    "RabbitMQ":    ("Tools & Platforms", []),
    "Jenkins":     ("Tools & Platforms", []),
    "GitHub":      ("Tools & Platforms", []),
    "GitLab":      ("Tools & Platforms", []),
    "Bitbucket":   ("Tools & Platforms", []),
    "Terraform":   ("Tools & Platforms", []),
    "Ansible":     ("Tools & Platforms", []),
    "Nginx":       ("Tools & Platforms", []),
    "Apache":      ("Tools & Platforms", []),
    "ServiceNow":  ("Tools & Platforms", ["service now"]),

    # ── SAP (modules only; methodologies like Data Migration go to Methods) ─
    "SAP MM":       ("SAP", ["mm", "materials management", "sap materials management"]),
    "SAP SD":       ("SAP", ["sd", "sales and distribution", "sales & distribution"]),
    "SAP FI":       ("SAP", ["fi", "financial accounting"]),
    "SAP CO":       ("SAP", ["co", "controlling"]),
    "SAP FICO":     ("SAP", ["fico"]),
    "SAP HCM":      ("SAP", ["hcm", "human capital management"]),
    "SAP HR":       ("SAP", ["sap hr legacy"]),
    "SAP PP":       ("SAP", ["pp", "production planning"]),
    "SAP PM":       ("SAP", ["pm", "plant maintenance"]),
    "SAP QM":       ("SAP", ["qm", "quality management"]),
    "SAP WM":       ("SAP", ["wm", "warehouse management"]),
    "SAP EWM":      ("SAP", ["ewm", "extended warehouse management"]),
    "SAP TM":       ("SAP", ["tm", "transportation management"]),
    "SAP PS":       ("SAP", ["ps", "project system"]),
    "SAP CS":       ("SAP", ["cs", "customer service"]),
    "SAP BW":       ("SAP", ["bw", "business warehouse"]),
    "SAP BTP":      ("SAP", ["btp", "business technology platform"]),
    "SAP S/4HANA":  ("SAP", ["s/4hana", "s4 hana", "s/4 hana", "s/4", "sap s4hana", "sap s/4 hana"]),
    "SAP Fiori":    ("SAP", ["fiori"]),
    "SAP Ariba":    ("SAP", ["ariba"]),
    "SAP SuccessFactors": ("SAP", ["successfactors", "success factors"]),
    "SAP Concur":   ("SAP", ["concur"]),
    "SAP IBP":      ("SAP", ["ibp"]),
    "SAP SAC":      ("SAP", ["sac", "analytics cloud"]),
    "SAP BPC":      ("SAP", ["bpc"]),
    "SAP Solution Manager": ("SAP", ["solution manager", "solman", "sap solman"]),
    "SAP ECC":      ("SAP", ["ecc"]),
    "SAP GTS":      ("SAP", ["gts", "global trade services"]),

    # ── Data & Analytics ───────────────────────────────────────────────────
    "Excel":      ("Data & Analytics", ["ms excel", "microsoft excel"]),
    "Power BI":   ("Data & Analytics", ["powerbi", "power-bi"]),
    "Tableau":    ("Data & Analytics", []),
    "Pandas":     ("Data & Analytics", []),
    "NumPy":      ("Data & Analytics", []),
    "MS Office":  ("Data & Analytics", ["microsoft office"]),
    "PowerPoint": ("Data & Analytics", ["ms powerpoint", "microsoft powerpoint"]),
    "Word":       ("Data & Analytics", ["ms word", "microsoft word"]),
    "Looker":     ("Data & Analytics", []),
    "Qlik":       ("Data & Analytics", ["qlikview", "qlik sense"]),
    "Matplotlib": ("Data & Analytics", []),
    "scikit-learn": ("Data & Analytics", ["sklearn", "scikit learn"]),
    "TensorFlow": ("Data & Analytics", ["tensor flow"]),
    "PyTorch":    ("Data & Analytics", []),

    # ── Methods & Frameworks ───────────────────────────────────────────────
    "Agile":              ("Methods & Frameworks", ["agil"]),
    "Scrum":              ("Methods & Frameworks", []),
    "Kanban":             ("Methods & Frameworks", []),
    "Waterfall":          ("Methods & Frameworks", ["wasserfall"]),
    "Projektmanagement":  ("Methods & Frameworks", ["project management"]),
    "Change Management":  ("Methods & Frameworks", []),
    "Data Migration":     ("Methods & Frameworks", ["datenmigration"]),
    "Rollout":            ("Methods & Frameworks", ["rollouts"]),
    "Go-Live Support":    ("Methods & Frameworks", ["go-live", "go live", "go live support"]),
    "Cutover":            ("Methods & Frameworks", ["cutover planning"]),
    "ITIL":               ("Methods & Frameworks", []),
    "Six Sigma":          ("Methods & Frameworks", []),
    "Lean":               ("Methods & Frameworks", []),
    "Anforderungsanalyse": ("Methods & Frameworks", ["requirements analysis", "requirements engineering"]),
    "Testmanagement":     ("Methods & Frameworks", ["test management"]),
    "UAT Testing":        ("Methods & Frameworks", ["uat", "user acceptance testing"]),
    "Stakeholder Management": ("Methods & Frameworks", []),
    "Fit/Gap Analysis":   ("Methods & Frameworks", ["fit-gap analysis", "fit gap analysis"]),
    "Master Data Management": ("Methods & Frameworks", ["mdm"]),
    "DevOps":             ("Methods & Frameworks", []),
    "CI/CD":              ("Methods & Frameworks", ["ci cd", "continuous integration", "continuous delivery"]),
    "TDD":                ("Methods & Frameworks", ["test driven development"]),
    "Prince2":            ("Methods & Frameworks", ["prince 2"]),
    "SAFe":               ("Methods & Frameworks", ["scaled agile framework"]),
    "Risk Management":    ("Methods & Frameworks", ["risikomanagement"]),
    "Business Process Modeling": ("Methods & Frameworks", ["geschäftsprozessmodellierung"]),

    # ── Communication & Training ───────────────────────────────────────────
    "Trainings":            ("Communication & Training", ["training"]),
    "Schulungen":           ("Communication & Training", ["schulung"]),
    "Key User Training":    ("Communication & Training", ["key user trainings"]),
    "Workshops":            ("Communication & Training", ["workshop", "fachbereichs-workshops"]),
    "Moderation":           ("Communication & Training", ["workshop-moderation", "workshops moderieren", "moderation von workshops"]),
    "Präsentationen":       ("Communication & Training", ["presentations"]),
    "Präsentationstechniken": ("Communication & Training", []),
    "Coaching":             ("Communication & Training", []),
    "Wissensvermittlung":   ("Communication & Training", []),
    "Kundenkommunikation":  ("Communication & Training", ["client communication", "customer communication"]),
    "Verhandlungsführung":  ("Communication & Training", ["verhandlung", "verhandlungen", "negotiation"]),
    "Stakeholder Reporting": ("Communication & Training", []),

    # ── Languages (spoken) ─────────────────────────────────────────────────
    "English":    ("Languages", ["englisch"]),
    "German":     ("Languages", ["deutsch"]),
    "French":     ("Languages", ["französisch", "francais"]),
    "Spanish":    ("Languages", ["spanisch", "español"]),
    "Italian":    ("Languages", ["italienisch", "italiano"]),
    "Polish":     ("Languages", ["polnisch", "polski"]),
    "Russian":    ("Languages", ["russisch"]),
    "Chinese":    ("Languages", ["chinesisch", "mandarin"]),
    "Portuguese": ("Languages", ["portugiesisch"]),
    "Dutch":      ("Languages", ["niederländisch", "nederlands"]),
    "Turkish":    ("Languages", ["türkisch"]),
    "Arabic":     ("Languages", ["arabisch"]),
    "Japanese":   ("Languages", ["japanisch"]),
}


# ── Reverse lookup: alias.lower() -> canonical ─────────────────────────────
def _build_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for canonical, (_cat, aliases) in SKILL_REGISTRY.items():
        index[canonical.lower()] = canonical
        for alias in aliases:
            index[alias.lower()] = canonical
    return index


_ALIAS_INDEX: dict[str, str] = _build_alias_index()


# ── SAP submodule recognition (FI-AP, MM-IM, CO-PA, ...) ───────────────────
# Matches a 2-4 letter code optionally prefixed with "SAP " and optionally
# followed by a separator + suffix (e.g. "SAP MM-IM", "MM_MI", "MM/IV").
_SAP_PREFIX = re.compile(r"^\s*sap\s+", re.IGNORECASE)
_SAP_MODULE_PATTERN = re.compile(
    r"^([A-Za-z]{2,4})(?:[\s\-_/].+)?$"
)
# The set of 2-4 letter codes that are valid SAP main modules. Built from
# SKILL_REGISTRY so it stays in sync.
_SAP_MODULE_CODES: frozenset[str] = frozenset(
    canonical[4:].upper()  # strip "SAP "
    for canonical in SKILL_REGISTRY
    if canonical.startswith("SAP ") and len(canonical) - 4 <= 4
    and canonical[4:].isalpha()
)


def _normalize_sap_submodule(name: str) -> str | None:
    """Recognize a SAP module reference like "SAP MM-IM" or "FI-AP" and return
    the canonical short form ("SAP MM" / "SAP FI"). Returns None for non-SAP
    inputs or unknown module codes.
    """
    if not name:
        return None
    stripped = _SAP_PREFIX.sub("", name).strip()
    m = _SAP_MODULE_PATTERN.match(stripped)
    if not m:
        return None
    code = m.group(1).upper()
    if code in _SAP_MODULE_CODES:
        return f"SAP {code}"
    return None


def canonical_name(name: str) -> str | None:
    """Returns the canonical name for a skill if it is recognized, else None.

    Handles:
    - exact alias match (case-insensitive): "ms excel" → "Excel"
    - SAP "SAP X" prefix stripping: "SAP MM" → "SAP MM"
    - SAP submodule collapse: "SAP FI-AP" / "FI-AP" / "FI_AP" → "SAP FI"
    """
    if not name:
        return None
    key = name.strip().lower()
    if not key:
        return None
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    # Also accept the canonical form with whitespace squashed
    squashed = re.sub(r"\s+", " ", key)
    if squashed in _ALIAS_INDEX:
        return _ALIAS_INDEX[squashed]
    sap_canonical = _normalize_sap_submodule(name)
    if sap_canonical:
        return sap_canonical
    return None


def category_for(name: str) -> str | None:
    """Returns the registry category for a known skill, else None."""
    canonical = canonical_name(name)
    if canonical and canonical in SKILL_REGISTRY:
        return SKILL_REGISTRY[canonical][0]
    return None


# ── Evidence-based deterministic rating computation ────────────────────────

@functools.lru_cache(maxsize=4096)
def _alias_pattern(alias: str) -> re.Pattern[str]:
    """Compile a word-bounded regex for the given alias. Cached because we
    build the same patterns many times across calls.

    Word boundaries here are alphanumeric only — that way punctuation in the
    skill name (e.g. C++, .NET, Node.js) is treated as a boundary character.
    """
    escaped = re.escape(alias)
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


def _all_aliases(canonical: str) -> list[str]:
    """All search tokens for a canonical skill: the canonical itself + aliases.
    Returns the list as-stored (case preserved); matching is case-insensitive.
    """
    if canonical not in SKILL_REGISTRY:
        return [canonical]
    _, aliases = SKILL_REGISTRY[canonical]
    return [canonical] + list(aliases)


def count_project_evidence(canonical: str, projects: list[dict]) -> int:
    """Number of distinct projects whose name or description mentions this
    skill (canonical or any of its aliases, word-bounded, case-insensitive).
    """
    if not projects:
        return 0
    aliases = _all_aliases(canonical)
    count = 0
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        blob = " ".join([
            str(proj.get("name") or ""),
            str(proj.get("description") or ""),
        ])
        if not blob.strip():
            continue
        for alias in aliases:
            if _alias_pattern(alias).search(blob):
                count += 1
                break
    return count


def rating_from_evidence(project_count: int) -> int:
    """Map number of project mentions to a deterministic rating.

    The mapping is intentionally conservative — we cannot detect seniority
    deterministically, so we cap at 8. Ratings 9-10 only ever come from the
    LLM, and only when the skill is unknown to the registry.

        0 projects → 4   (listed only, no project evidence)
        1 project  → 6
        2 projects → 7
        3+ projects → 8
    """
    if project_count >= 3:
        return 8
    if project_count == 2:
        return 7
    if project_count == 1:
        return 6
    return 4
