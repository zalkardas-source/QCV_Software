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
    # AUTOINCREMENT: profile IDs leak outside the DB (photo filenames like
    # "42.jpg", /api/cvs/{id} URLs in browser tabs and exports). Without it,
    # SQLite reuses IDs after a data reset and a *different* candidate would
    # inherit an old candidate's references. Same reasoning on CVVersion.
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String)
    name = Column(String)
    email = Column(String)
    location = Column(String) # New field
    small_summary = Column(Text) # New field
    raw_json = Column(Text)
    photo_path = Column(String, nullable=True)
    status = Column(String, default="new")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    skills = relationship("Skill", back_populates="cv_profile", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="cv_profile", cascade="all, delete-orphan")
    languages = relationship("Language", back_populates="cv_profile", cascade="all, delete-orphan")

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


class Language(Base):
    __tablename__ = "languages"

    id = Column(Integer, primary_key=True, index=True)
    cv_profile_id = Column(Integer, ForeignKey("cv_profiles.id"))
    name = Column(String, index=True)            # "English", "German", ...
    level = Column(String, nullable=True)        # "native" | "fluent" | "good" | "basic" | None

    cv_profile = relationship("CVProfile", back_populates="languages")

class CVVersion(Base):
    """Snapshot of a CVProfile at upload time.

    Every upload (insert or update) creates one row here. The one-pager and
    full-CV PDFs for an older version are regenerated on demand from
    snapshot_json — we don't store rendered PDFs.

    photo_path: archived filename for this version (e.g. "42-v1.jpg") so
    historical renderings keep the photo that was current at upload time.
    """
    __tablename__ = "cv_versions"
    # See CVProfile: version IDs appear in /api/cvs/{id}/versions/{vid}/photo
    # URLs and archived photo filenames — never reuse them.
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, index=True)
    cv_profile_id = Column(Integer, ForeignKey("cv_profiles.id"), index=True)
    version_number = Column(Integer)
    snapshot_json = Column(Text)
    source_filename = Column(String, nullable=True)
    photo_path = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


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
