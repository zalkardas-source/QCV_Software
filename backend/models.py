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
    raw_json = Column(Text) # Store the structured data as a JSON string
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    skills = relationship("Skill", back_populates="cv_profile", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="cv_profile", cascade="all, delete-orphan")

class Skill(Base):
    __tablename__ = "skills"
    
    id = Column(Integer, primary_key=True, index=True)
    cv_profile_id = Column(Integer, ForeignKey("cv_profiles.id"))
    name = Column(String, index=True)
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

class BatchJob(Base):
    __tablename__ = "batch_jobs"
    
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="pending") # pending, processing, completed, error
    
    items = relationship("BatchJobItem", back_populates="batch_job", cascade="all, delete-orphan")

class BatchJobItem(Base):
    __tablename__ = "batch_job_items"
    
    id = Column(Integer, primary_key=True, index=True)
    batch_job_id = Column(Integer, ForeignKey("batch_jobs.id"))
    filename = Column(String)
    status = Column(String, default="pending") # pending, processing, done, error
    error_message = Column(Text, nullable=True)
    
    batch_job = relationship("BatchJob", back_populates="items")
