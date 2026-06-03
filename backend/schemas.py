from pydantic import BaseModel, Field
from typing import List, Literal, Optional

# Single source of truth for allowed skill categories. The Literal below makes
# Pydantic reject any other value at validation time, so a drifted LLM output
# triggers the self-healing retry loop instead of silently storing a bogus
# category.
SkillCategory = Literal[
    "Programming Languages",
    "Frameworks & Libraries",
    "Databases",
    "Tools & Platforms",
    "SAP",
    "Data & Analytics",
    "Methods & Frameworks",
    "Communication & Training",
    "Languages",
]


class PersonalInformation(BaseModel):
    full_name: str = Field(description="The full name of the candidate. If not found, output 'Unknown Name'")
    age_or_dob: Optional[str] = Field(default=None, description="Age or Date of Birth of the candidate")
    nationality: Optional[str] = Field(default=None, description="Nationality of the candidate")
    marital_status: Optional[str] = Field(default=None, description="Marital status")
    email: Optional[str] = Field(default=None, description="Email address")
    phone: Optional[str] = Field(default=None, description="Phone number")
    location: Optional[str] = Field(default=None, description="Current city or location")
    linkedin: Optional[str] = Field(default=None, description="LinkedIn profile URL")
    website: Optional[str] = Field(default=None, description="Personal website or portfolio URL")

class Skill(BaseModel):
    skill: str = Field(description="The name of the skill, e.g. Python, SQL")
    rating: int = Field(default=4, description="Numeric rating 1-10 based on evidence in the CV. See rating rules.")

class SkillGroup(BaseModel):
    category: SkillCategory = Field(description="Category of the skills. Must be one of the nine allowed values.")
    skills: List[Skill] = Field(description="List of skills within this category")

class Project(BaseModel):
    name: str = Field(description="Name of the project, job title, or role")
    description: Optional[str] = Field(default=None, description="Description of responsibilities or achievements")
    duration: Optional[str] = Field(default=None, description="Duration or dates of the project/experience")
    industry: Optional[str] = Field(default=None, description="Industry/sector of the client or project (e.g. Pharma, Automotive, Banking, Retail, Manufacturing, Telco, Public Sector). One or two words, English.")
    location: Optional[str] = Field(default=None, description="Country or city where the project/role was located (e.g. 'Germany', 'Berlin', 'Remote'). Used as a fallback when industry is unclear.")


# Allowed values for spoken-language proficiency. Keep the set small so the
# Word/PDF export looks uniform across candidates.
LanguageLevel = Literal["native", "fluent", "good", "basic"]


class LanguageEntry(BaseModel):
    name: str = Field(description="The spoken language, in English. Examples: English, German, French, Spanish, Russian, Mandarin, Polish, Italian, Portuguese, Dutch, Turkish, Arabic, Japanese.")
    level: Optional[LanguageLevel] = Field(default=None, description="Proficiency level, normalized to one of: native, fluent, good, basic. Map common CV phrasing: 'Muttersprache' / 'native speaker' → native; 'verhandlungssicher' / 'C1' / 'C2' / 'business-fluent' → fluent; 'gut' / 'B1' / 'B2' → good; 'Grundkenntnisse' / 'A1' / 'A2' → basic. If the CV gives no level, leave null.")


class ProjectHighlight(BaseModel):
    """A curated project bullet for the 'Relevant Experience' column of the
    one-pager/full-CV PDF. One entry per noteworthy engagement.
    """
    client_label: str = Field(description="Bold lead label for the bullet. If the CV names the client explicitly (e.g. 'BMW Group', 'BioNTech', 'Animex'), use that exact name. Otherwise generate a descriptive label from industry + size hint + region, e.g. 'Leading German automotive supplier', '10th largest pharmaceutical company in Europe', 'Polish e-commerce platform'. Keep it under 8 words.")
    description: str = Field(description="One short paragraph (1-2 sentences) summarizing the role, scope and SAP/tech modules used. Mirror the BearingPoint pattern: 'SAP S/4HANA implementation, PP/QM module. Pre-implementation analysis, system configuration, user training.'")


class BackgroundEntry(BaseModel):
    """One row of the chronological 'Professional Background' column.
    Concise: duration + company + role, optionally a trailing sentence.
    """
    duration: str = Field(description="Date range as written in the CV, e.g. '03/2018 – present', '04/2014 – 03/2018', '2020-2022'. Keep the exact format the CV uses.")
    company: str = Field(description="Employer name as written. If the candidate is a contractor through an agency, prefer the agency name unless the actual client is more recognizable.")
    role: str = Field(description="Job title held during this period, e.g. 'Senior SAP Consultant', 'Associate Director', 'Junior ABAP Programmer'.")
    note: Optional[str] = Field(default=None, description="Optional trailing detail (e.g. 'Maintenance and development of SAP SD/WM/HR modules.'). Keep empty unless the CV gives one specific concrete sentence worth showing in a compact list.")


class CVData(BaseModel):
    """
    The main schema for the CV extraction.
    The order here dictates how the JSON is structured.
    """
    personal_information: PersonalInformation = Field(description="Core personal details")
    role_title: Optional[str] = Field(default=None, description="Headline role under the candidate's name, e.g. 'Senior SAP Consultant', 'Associate Director', 'Project Manager'. Take the most recent / highest job title from the CV. Used as the red sub-line under the name in the PDF header.")
    small_summary: Optional[str] = Field(default=None, description="Professional summary, 4-6 sentences. Sentence 1-2: role, years of experience, focus area. Sentence 3-4: natural-language mention of the 3-5 strongest skills (mix of technical, methodological, and communication skills) drawn from skill_matrix. Sentence 5-6: industry exposure, project types, geographic scope. Be factual, no marketing language. Used internally for dashboard previews — NOT rendered in the new PDF layout.")
    total_experience_years: Optional[int] = Field(default=None, description="Total years of professional work experience, computed by summing project durations. Null if not derivable.")
    professional_focus: List[str] = Field(default_factory=list, description="4-6 bullet statements summarizing the candidate's competencies and value. Each bullet is one short sentence. Examples: 'Expert knowledge of SAP modules: EWM, MM, WM, SD, RETAIL'; 'Integrations SAP with third party supplier systems'; 'Proficiency in SAP ABAP programming language'. Curate from project evidence — don't dump every tool.")
    relevant_experience: List[ProjectHighlight] = Field(default_factory=list, description="Curated project highlights for the right column of the PDF. 6-10 entries. Each entry has a bold client_label and a short description. Pick the most notable / recent projects.")
    professional_background: List[BackgroundEntry] = Field(default_factory=list, description="Chronological work history, most recent first. Concise duration + company + role. Max 8 entries.")
    education_certificates: List[str] = Field(default_factory=list, description="Combined list of education and certifications, formatted as bullets. Examples: 'Wrocław University of Technology, Software Engineering'; 'SAP Certified Application Associate - SAP Extended Warehouse Management 9.4'; 'Prince 2 Practitioner'. Highest education first, then certifications.")
    industries: List[str] = Field(default_factory=list, description="Deduplicated list of industries the candidate has worked in. Max 5. One or two words each, English. Examples: 'Transport & Logistics', 'Manufacturing', 'Retail', 'Pharma', 'Banking', 'Public Sector'. Derive from projects[].industry.")
    skill_matrix: List[SkillGroup] = Field(default_factory=list, description="Categorized list of skills. Do NOT include spoken languages here — those go into the separate `languages` field. Kept in the schema for internal job-matching only — NOT rendered in the new PDF layout.")
    languages: List[LanguageEntry] = Field(default_factory=list, description="Spoken languages with proficiency level. Separate from skill_matrix because spoken languages are competencies of a different kind — they have a level (native/fluent/...), not a 1-10 rating.")
    projects: List[Project] = Field(default_factory=list, description="List of work experiences or projects (full data, used for matching + relevant_experience curation).")
