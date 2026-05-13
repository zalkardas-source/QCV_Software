from pydantic import BaseModel, Field
from typing import List, Optional


class JobRequirementData(BaseModel):
    title: str = Field(description="Job title or role, e.g. 'Senior SAP FI/CO Consultant'")
    description: str = Field(description="2-3 sentence summary of the role and context")
    required_skills: List[str] = Field(default_factory=list, description="Skills explicitly required for the role")
    nice_to_have_skills: List[str] = Field(default_factory=list, description="Skills mentioned as optional or beneficial")
    experience_years: Optional[int] = Field(default=None, description="Minimum years of experience required, as integer")
    location: Optional[str] = Field(default=None, description="City or region where the role is based")
    remote: Optional[bool] = Field(default=None, description="True if remote work is possible, False if on-site only, null if not mentioned")
