from pydantic import BaseModel, Field
from typing import List, Optional

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
    skill: str = Field(description="The name of the skill, e.g. Python, Leadership")
    rating: Optional[str] = Field(default=None, description="Rating of the skill (e.g. 1-5) if provided, otherwise null")

class Project(BaseModel):
    name: str = Field(description="Name of the project, job title, or role")
    description: Optional[str] = Field(default=None, description="Description of responsibilities or achievements")
    duration: Optional[str] = Field(default=None, description="Duration or dates of the project/experience")

class CVData(BaseModel):
    """
    The main schema for the CV extraction.
    The order here dictates how the JSON is structured.
    """
    personal_information: PersonalInformation = Field(description="Core personal details")
    small_summary: Optional[str] = Field(default=None, description="A short professional summary or objective of the candidate")
    skill_matrix: List[Skill] = Field(default_factory=list, description="List of skills")
    projects: List[Project] = Field(default_factory=list, description="List of work experiences or projects")
