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

class CVData(BaseModel):
    """
    The main schema for the CV extraction.
    The order here dictates how the JSON is structured.
    """
    personal_information: PersonalInformation = Field(description="Core personal details")
    small_summary: Optional[str] = Field(default=None, description="Professional summary, 4-6 sentences. Sentence 1-2: role, years of experience, focus area. Sentence 3-4: natural-language mention of the 3-5 strongest skills (mix of technical, methodological, and communication skills) drawn from skill_matrix. Sentence 5-6: industry exposure, project types, geographic scope. Be factual, no marketing language.")
    total_experience_years: Optional[int] = Field(default=None, description="Total years of professional work experience, computed by summing project durations. Null if not derivable.")
    skill_matrix: List[SkillGroup] = Field(default_factory=list, description="Categorized list of skills")
    projects: List[Project] = Field(default_factory=list, description="List of work experiences or projects")
