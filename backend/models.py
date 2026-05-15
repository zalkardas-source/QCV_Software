from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from backend.database import Base
from datetime import datetime

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)

class CVProfile(Base):
    __tablename__ = "cv_profiles"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    name = Column(String)
    email = Column(String)
    location = Column(String) # New field
    small_summary = Column(Text) # New field
    raw_json = Column(Text)
    status = Column(String, default="new")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    skills = relationship("Skill", back_populates="cv_profile", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="cv_profile", cascade="all, delete-orphan")

class Skill(Base):
    __tablename__ = "skills"
    
    id = Column(Integer, primary_key=True, index=True)
    cv_profile_id = Column(Integer, ForeignKey("cv_profiles.id"))
    name = Column(String, index=True)
    category = Column(String, index=True, nullable=True)
    rating = Column(Integer, nullable=True)
    
    cv_profile = relationship("CVProfile", back_populates="skills")

class Project(Base):
    __tablename__ = "projects"
    
    id = Column(Integer, primary_key=True, index=True)
    cv_profile_id = Column(Integer, ForeignKey("cv_profiles.id"))
    name = Column(String, index=True)
    duration = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    
    cv_profile = relationship("CVProfile", back_populates="projects")

class JobRequirement(Base):
    __tablename__ = "job_requirements"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    description = Column(Text, nullable=True)
    required_skills = Column(Text)   # JSON list
    nice_to_have_skills = Column(Text, nullable=True)  # JSON list
    experience_years = Column(Integer, nullable=True)
    location = Column(String, nullable=True)
    remote = Column(String, nullable=True)  # "true"/"false"/None
    status = Column(String, default="open")  # open / filled / cancelled
    raw_email = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ParsingCache(Base):
    __tablename__ = "parsing_cache"

    file_hash = Column(String, primary_key=True, index=True)
    json_data = Column(Text) # Store the extracted JSON
    created_at = Column(DateTime, default=datetime.utcnow)


class OAuthAccount(Base):
    """Per-user OAuth connection to an external email provider.

    The refresh_token is encrypted at rest (see backend.crypto). Access tokens
    are short-lived and not stored — fetched fresh from MSAL's token cache
    each request and discarded.
    """
    __tablename__ = "oauth_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String, nullable=False)  # "microsoft" | "google" (future)
    email_address = Column(String, nullable=False)
    refresh_token_encrypted = Column(Text, nullable=False)
    scopes = Column(Text, nullable=True)  # space-separated
    connected_at = Column(DateTime, default=datetime.utcnow)
