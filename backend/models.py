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

class ParsingCache(Base):
    __tablename__ = "parsing_cache"
    
    file_hash = Column(String, primary_key=True, index=True)
    json_data = Column(Text) # Store the extracted JSON
    created_at = Column(DateTime, default=datetime.utcnow)
