from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List
import json
import logging
import os
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from backend.auth import verify_password, create_access_token, get_password_hash
from jose import jwt, JWTError
import os

# Load environment variables from .env file
load_dotenv()

from backend.database import engine, Base, get_db, SessionLocal
from backend.models import User, CVProfile, Skill, Project, Language, JobRequirement, OAuthAccount, CVVersion
from backend.services import (
    extract_text_from_file,
    extract_text_and_photo,
    structure_cv_data,
    warmup_docling,
    parse_job_email,
)

import pathlib

_PHOTO_DIR = pathlib.Path(__file__).parent / "data" / "photos"
_PHOTO_DIR.mkdir(parents=True, exist_ok=True)


def _save_photo(cv_id: int, photo_bytes: bytes | None) -> str | None:
    """Persist a JPEG photo to disk; returns the bare filename stored in the DB.

    Only the filename is persisted (e.g. "42.jpg"). All resolution goes through
    _PHOTO_DIR, so storage and read paths can never drift apart.
    """
    if not photo_bytes:
        return None
    filename = f"{cv_id}.jpg"
    path = _PHOTO_DIR / filename
    try:
        path.write_bytes(photo_bytes)
        return filename
    except Exception as e:
        logger.warning("[PHOTO] Failed to write %s: %s", path, e)
        return None


def _archive_current_photo(db: Session, profile: CVProfile, prev_version: int) -> None:
    """Rename the profile's current photo file so it survives the next overwrite,
    then point any existing CVVersion rows that referenced it at the new name.
    Called before saving a fresh photo on an update upload.
    """
    if not profile.photo_path:
        return
    current_path = _PHOTO_DIR / profile.photo_path
    if not current_path.exists():
        return
    archive_name = f"{profile.id}-v{prev_version}.jpg"
    archive_path = _PHOTO_DIR / archive_name
    try:
        current_path.rename(archive_path)
    except Exception as e:
        logger.warning("[PHOTO] Archive rename failed for cv=%d: %s", profile.id, e)
        return
    db.query(CVVersion).filter(
        CVVersion.cv_profile_id == profile.id,
        CVVersion.photo_path == profile.photo_path,
    ).update({CVVersion.photo_path: archive_name}, synchronize_session=False)


def _resolve_photo_path(stored: str | None) -> pathlib.Path | None:
    """Resolve the value stored in CVProfile.photo_path to an actual file path.

    Accepts both the new bare-filename format ("42.jpg") and the legacy
    "data/photos/42.jpg" format so existing rows keep working.
    """
    if not stored:
        return None
    p = pathlib.Path(stored)
    if p.is_absolute():
        return p
    # Bare filename → resolve under _PHOTO_DIR
    if "/" not in stored and "\\" not in stored:
        return _PHOTO_DIR / stored
    # Legacy: stored relative to backend/ (e.g. "data/photos/42.jpg")
    return pathlib.Path(__file__).parent / stored
from backend.scraping import fetch_url, detect_source
from backend.export_pdf import create_pdf_summary, create_full_cv_pdf
from backend import oauth_microsoft, crypto

# Initialize DB tables
Base.metadata.create_all(bind=engine)


def _ensure_photo_path_column():
    """Idempotent SQLite migration: add cv_profiles.photo_path if missing.

    create_all() never alters existing tables, so when a column is added later
    we need an explicit ALTER for existing databases. Safe to run repeatedly.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    if "cv_profiles" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("cv_profiles")}
    if "photo_path" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE cv_profiles ADD COLUMN photo_path VARCHAR"))


def _ensure_sqlite_autoincrement(bind=None):
    """Idempotent SQLite migration: rebuild tables whose IDs are referenced
    outside the DB so their primary key carries AUTOINCREMENT.

    Plain SQLite INTEGER PRIMARY KEY computes the next id as max(id)+1, so
    after rows are deleted (e.g. a test-data reset) old IDs get reassigned —
    and a *different* candidate appears under an old candidate's photo file
    and URL. AUTOINCREMENT keeps a persistent high-water mark in
    sqlite_sequence and never hands out an ID twice.

    SQLite cannot add AUTOINCREMENT via ALTER TABLE, so we rebuild: create
    the new table (from the model, which now carries sqlite_autoincrement),
    copy all rows, drop the old table, rename. Copying explicit IDs seeds
    sqlite_sequence with the current max automatically. Safe to run
    repeatedly; one transaction per startup.
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateTable

    target = bind or engine
    if target.dialect.name != "sqlite":
        return
    existing = set(inspect(target).get_table_names())
    with target.begin() as conn:
        for model in (CVProfile, CVVersion):
            table = model.__table__
            if table.name not in existing:
                continue  # create_all already built it with AUTOINCREMENT
            ddl = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"),
                {"n": table.name},
            ).scalar()
            if ddl and "AUTOINCREMENT" in ddl.upper():
                continue  # already migrated
            tmp = f"{table.name}_autoinc_tmp"
            create_sql = str(CreateTable(table).compile(target)).replace(
                f"CREATE TABLE {table.name}", f"CREATE TABLE {tmp}", 1
            )
            columns = ", ".join(c.name for c in table.columns)
            conn.execute(text(create_sql))
            conn.execute(
                text(f"INSERT INTO {tmp} ({columns}) SELECT {columns} FROM {table.name}")
            )
            conn.execute(text(f"DROP TABLE {table.name}"))
            conn.execute(text(f"ALTER TABLE {tmp} RENAME TO {table.name}"))
            for index in table.indexes:
                index.create(bind=conn, checkfirst=True)
            logger.info("[MIGRATION] %s rebuilt with AUTOINCREMENT", table.name)


_ensure_photo_path_column()
_ensure_sqlite_autoincrement()


app = FastAPI(title="QCV Professional Parser API")

@app.on_event("startup")
async def startup_event():
    # Pre-load Docling model
    warmup_docling()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

from backend.config import settings

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    return user

# Frontend is served by a separate nginx container (see frontend/Dockerfile).
# In production nginx reverse-proxies /api/* to this backend, so cross-origin
# requests don't happen. CORS settings still apply if someone hits the API
# directly from a different origin.
from fastapi.responses import RedirectResponse

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

@app.post("/api/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}

import anyio

@app.post("/api/parse-cv")
async def parse_cv(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Uploads a CV and parses it. Optimized with Async/Threading."""
    try:
        content = await file.read()

        raw_markdown = await anyio.to_thread.run_sync(
            extract_text_from_file, content, file.filename
        )

        structured_data = await anyio.to_thread.run_sync(
            structure_cv_data, raw_markdown
        )

        return {
            "status": "success",
            "filename": file.filename,
            "data": structured_data,
        }
    except Exception as e:
        logger.error("Fatal error in parse_cv", exc_info=True)
        raise HTTPException(status_code=500, detail=f"{str(e)}")

@app.post("/api/cvs/import-from-url")
async def import_cv_from_url(payload: dict, user: User = Depends(get_current_user)):
    """Imports a CV from a profile URL (LinkedIn/Xing/Freelancermap) or pasted text.

    Body: { "url": str?, "text": str? } — at least one is required.
    URL is tried first; if not present, falls back to text.
    """
    url = (payload.get("url") or "").strip()
    text = (payload.get("text") or "").strip()

    if not url and not text:
        raise HTTPException(status_code=422, detail="Either 'url' or 'text' is required")

    source = detect_source(url) if url else None

    try:
        if text:
            raw_text = text
        else:
            raw_text = await anyio.to_thread.run_sync(fetch_url, url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("URL fetch failed")
        raise HTTPException(status_code=500, detail=f"Fetch failed: {e}")

    try:
        structured_data = await anyio.to_thread.run_sync(structure_cv_data, raw_text)
    except Exception as e:
        logger.exception("LLM structuring failed for URL import")
        raise HTTPException(status_code=500, detail=f"Parsing failed: {e}")

    filename = f"{source or 'profile'}_{url[-40:] if url else 'pasted'}.html"
    return {
        "status": "success",
        "filename": filename,
        "source": source,
        "data": structured_data,
    }


def _persist_cv(db: Session, data: dict, filename: str, photo_bytes: bytes | None = None, create_version: bool = True) -> CVProfile:
    """Inserts or updates a parsed CV in the database.

    If a CVProfile with the same email already exists, that row is updated in
    place — same id, same status, same created_at, but all CV content (name,
    location, summary, raw_json, skills, projects) is overwritten. This is the
    "candidate sent an updated CV" case.

    Without an email match (or no email at all in the new CV) a fresh row is
    inserted. Caller is responsible for commit/rollback.
    """
    personal = data.get("personal_information", {})
    full_name = personal.get("full_name", "Unknown Name")
    email = (personal.get("email") or "").strip()
    location = personal.get("location", "")
    summary = data.get("small_summary", "")
    raw_json = json.dumps(data)

    profile = None
    if email:
        profile = (
            db.query(CVProfile)
            .filter(func.lower(CVProfile.email) == email.lower())
            .first()
        )

    if profile is not None:
        # Update path: overwrite content, preserve id/status/created_at
        logger.info("[PERSIST] Updating existing CV id=%d (email match: %s)", profile.id, email)
        profile.filename = filename
        profile.name = full_name
        profile.email = email
        profile.location = location
        profile.small_summary = summary
        profile.raw_json = raw_json
        # Wipe nested rows; cascade=delete-orphan would handle it via the
        # relationship but explicit DELETEs avoid a flush-order pitfall.
        db.query(Skill).filter(Skill.cv_profile_id == profile.id).delete()
        db.query(Project).filter(Project.cv_profile_id == profile.id).delete()
        db.query(Language).filter(Language.cv_profile_id == profile.id).delete()
        db.flush()
    else:
        # Insert path
        profile = CVProfile(
            filename=filename,
            name=full_name,
            email=email,
            location=location,
            small_summary=summary,
            raw_json=raw_json,
        )
        db.add(profile)
        db.flush()  # populates profile.id

    for group in data.get("skill_matrix", []) or []:
        cat_name = group.get("category", "General")
        for s in group.get("skills", []) or []:
            rating_val = None
            if s.get("rating") is not None:
                try:
                    rating_val = int(s.get("rating"))
                except (TypeError, ValueError):
                    pass
            db.add(Skill(
                cv_profile_id=profile.id,
                name=s.get("skill", ""),
                category=cat_name,
                rating=rating_val,
            ))

    for p in data.get("projects", []) or []:
        db.add(Project(
            cv_profile_id=profile.id,
            name=p.get("name", ""),
            duration=p.get("duration", ""),
            description=p.get("description", ""),
        ))

    for lang in data.get("languages", []) or []:
        if not isinstance(lang, dict):
            continue
        name = (lang.get("name") or "").strip()
        if not name:
            continue
        level = (lang.get("level") or "").strip() or None
        db.add(Language(
            cv_profile_id=profile.id,
            name=name,
            level=level,
        ))

    # Photo: archive the previous photo file (if any) before overwriting it.
    # Without archiving, older CVVersion rows would 404 on /photo because the
    # file they reference would have been clobbered.
    existing_max = (
        db.query(func.max(CVVersion.version_number))
        .filter(CVVersion.cv_profile_id == profile.id)
        .scalar()
    ) or 0
    next_version = existing_max + 1

    if photo_bytes:
        if profile.photo_path:
            _archive_current_photo(db, profile, existing_max or next_version - 1)
        stored = _save_photo(profile.id, photo_bytes)
        if stored:
            profile.photo_path = stored

    # Snapshot this upload as a new version. Skipped for incremental edits
    # (skill add/remove from the modal) — those mutate current state without
    # being a distinct "upload event".
    if create_version:
        db.add(CVVersion(
            cv_profile_id=profile.id,
            version_number=next_version,
            snapshot_json=raw_json,
            source_filename=filename,
            photo_path=profile.photo_path,
        ))

    return profile


@app.post("/api/save-cv")
def save_cv(payload: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Saves final reviewed CV data to the database (legacy two-step flow + manual edits).

    Does NOT create a new CVVersion — manual edits (skill add/remove, JSON tweaks)
    mutate current state without being a distinct upload event.
    """
    try:
        profile = _persist_cv(db, payload.get("data", {}), payload.get("filename", "unknown.pdf"), create_version=False)
        db.commit()
        db.refresh(profile)
        return {"status": "success", "id": profile.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload-cv")
async def upload_cv(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One-shot CV upload: extract text → structure+review via LLMs → save to DB.

    Replaces the legacy parse-then-save flow. The frontend no longer shows a
    manual review screen; the second LLM call (review_cv_data, invoked from
    inside structure_cv_data) takes that role.
    """
    try:
        content = await file.read()

        raw_markdown, photo_bytes = await anyio.to_thread.run_sync(
            extract_text_and_photo, content, file.filename
        )
        structured_data = await anyio.to_thread.run_sync(
            structure_cv_data, raw_markdown
        )

        profile = _persist_cv(db, structured_data, file.filename, photo_bytes)
        db.commit()
        db.refresh(profile)

        return {
            "status": "success",
            "id": profile.id,
            "filename": file.filename,
            "name": profile.name,
            "has_photo": bool(profile.photo_path),
        }
    except Exception as e:
        db.rollback()
        logger.error("Fatal error in upload_cv", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/export-pdf")
async def export_pdf(payload: dict, user: User = Depends(get_current_user)):
    """Generates a PDF profile from JSON data (used by the edit view)."""
    try:
        data = payload.get("data", {})
        if not data:
            raise ValueError("No data provided")

        pdf_bytes = await anyio.to_thread.run_sync(create_pdf_summary, data)

        personal = data.get("personal_information", {})
        name = personal.get("full_name", "CV")
        filename = f"{name.replace(' ', '_')}_Profile.pdf"

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        logger.error("PDF export error: %s", e)
        raise HTTPException(status_code=500, detail=f"{str(e)}")

@app.post("/api/export-full-cv")
async def export_full_cv(payload: dict, user: User = Depends(get_current_user)):
    """Generates a multi-page full CV PDF from JSON data (Quatelio format)."""
    try:
        data = payload.get("data", {})
        if not data:
            raise ValueError("No data provided")

        pdf_bytes = await anyio.to_thread.run_sync(create_full_cv_pdf, data)

        personal = data.get("personal_information", {})
        name = personal.get("full_name", "CV")
        filename = f"{name.replace(' ', '_')}_FullCV.pdf"

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        logger.error("Full CV export error: %s", e)
        raise HTTPException(status_code=500, detail=f"{str(e)}")


@app.get("/api/cvs/{cv_id}/full-cv")
def download_cv_full(cv_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Downloads the multi-page Quatelio-format CV for a stored candidate."""
    profile = db.query(CVProfile).filter(CVProfile.id == cv_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="CV not found")

    data = json.loads(profile.raw_json) if profile.raw_json else {}
    pdf_bytes = create_full_cv_pdf(data, photo_path=profile.photo_path)

    name = (profile.name or "CV").replace(' ', '_')
    filename = f"{name}_FullCV.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/cvs/{cv_id}/pdf")
def download_cv_pdf(cv_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Downloads the candidate profile of a stored CV as PDF."""
    profile = db.query(CVProfile).filter(CVProfile.id == cv_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="CV not found")

    data = json.loads(profile.raw_json) if profile.raw_json else {}
    pdf_bytes = create_pdf_summary(data, photo_path=profile.photo_path)

    name = (profile.name or "CV").replace(' ', '_')
    filename = f"{name}_Profile.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/api/cvs")
def list_cvs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Lists all saved CVs."""
    profiles = db.query(CVProfile).order_by(CVProfile.created_at.desc()).all()
    return [{
        "id": p.id,
        "name": p.name,
        "email": p.email,
        "filename": p.filename,
        "created_at": p.created_at,
        "status": p.status or "new",
        "has_photo": bool(p.photo_path),
        "raw_json": p.raw_json
    } for p in profiles]


@app.get("/api/cvs/{cv_id}/versions")
def list_cv_versions(cv_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Lists all snapshot versions for a CV, newest first."""
    if not db.query(CVProfile).filter(CVProfile.id == cv_id).first():
        raise HTTPException(status_code=404, detail="CV not found")
    rows = (
        db.query(CVVersion)
        .filter(CVVersion.cv_profile_id == cv_id)
        .order_by(CVVersion.version_number.desc())
        .all()
    )
    return [{
        "id": v.id,
        "version_number": v.version_number,
        "source_filename": v.source_filename,
        "created_at": v.created_at,
        "has_photo": bool(v.photo_path),
    } for v in rows]


def _load_version(db: Session, cv_id: int, version_id: int) -> CVVersion:
    v = (
        db.query(CVVersion)
        .filter(CVVersion.id == version_id, CVVersion.cv_profile_id == cv_id)
        .first()
    )
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    return v


@app.get("/api/cvs/{cv_id}/versions/{version_id}")
def get_cv_version(cv_id: int, version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Returns a single CV version as JSON (the snapshot at upload time)."""
    v = _load_version(db, cv_id, version_id)
    return {
        "id": v.id,
        "cv_profile_id": v.cv_profile_id,
        "version_number": v.version_number,
        "source_filename": v.source_filename,
        "created_at": v.created_at,
        "has_photo": bool(v.photo_path),
        "data": json.loads(v.snapshot_json) if v.snapshot_json else {},
    }


@app.get("/api/cvs/{cv_id}/versions/{version_id}/pdf")
def download_version_pdf(cv_id: int, version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """One-pager PDF rendered from a version's snapshot (on-demand)."""
    v = _load_version(db, cv_id, version_id)
    data = json.loads(v.snapshot_json) if v.snapshot_json else {}
    pdf_bytes = create_pdf_summary(data, photo_path=v.photo_path)
    personal = data.get("personal_information", {})
    name = (personal.get("full_name") or "CV").replace(' ', '_')
    filename = f"{name}_v{v.version_number}_Profile.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/cvs/{cv_id}/versions/{version_id}/full-pdf")
def download_version_full_pdf(cv_id: int, version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Full-CV PDF rendered from a version's snapshot (on-demand)."""
    v = _load_version(db, cv_id, version_id)
    data = json.loads(v.snapshot_json) if v.snapshot_json else {}
    pdf_bytes = create_full_cv_pdf(data, photo_path=v.photo_path)
    personal = data.get("personal_information", {})
    name = (personal.get("full_name") or "CV").replace(' ', '_')
    filename = f"{name}_v{v.version_number}_FullCV.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/cvs/{cv_id}/versions/{version_id}/photo")
def get_cv_version_photo(cv_id: int, version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Serves the archived photo of a specific version, or 404 if none."""
    v = _load_version(db, cv_id, version_id)
    if not v.photo_path:
        raise HTTPException(status_code=404, detail="No photo")
    full_path = _resolve_photo_path(v.photo_path)
    if not full_path or not full_path.exists():
        raise HTTPException(status_code=404, detail="Photo file missing")
    # no-store: photo content behind this URL can change (re-upload, or ID reuse
    # after a DB reset), so the browser must never serve a cached copy.
    return Response(
        content=full_path.read_bytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/cvs/{cv_id}/photo")
def get_cv_photo(cv_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Serves the candidate's profile photo (JPEG), or 404 if none stored."""
    profile = db.query(CVProfile).filter(CVProfile.id == cv_id).first()
    if not profile or not profile.photo_path:
        raise HTTPException(status_code=404, detail="No photo")
    full_path = _resolve_photo_path(profile.photo_path)
    if not full_path or not full_path.exists():
        raise HTTPException(status_code=404, detail="Photo file missing")
    # no-store: same reasoning as the version-photo endpoint above.
    return Response(
        content=full_path.read_bytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )

@app.get("/api/cvs/{cv_id}")
def get_cv(cv_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Gets a single CV profile with full JSON data."""
    profile = db.query(CVProfile).filter(CVProfile.id == cv_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="CV not found")
    return {
        "id": profile.id,
        "name": profile.name,
        "email": profile.email,
        "filename": profile.filename,
        "created_at": profile.created_at,
        "has_photo": bool(profile.photo_path),
        "data": json.loads(profile.raw_json) if profile.raw_json else {}
    }

@app.patch("/api/cvs/{cv_id}/status")
def update_cv_status(cv_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Updates the status of a CV profile."""
    valid_statuses = {"new", "in_review", "invited", "rejected"}
    new_status = payload.get("status")
    if new_status not in valid_statuses:
        raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {sorted(valid_statuses)}")
    profile = db.query(CVProfile).filter(CVProfile.id == cv_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="CV not found")
    profile.status = new_status
    db.commit()
    return {"id": cv_id, "status": new_status}

@app.delete("/api/cvs/{cv_id}")
def delete_cv(cv_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Deletes a CV profile."""
    profile = db.query(CVProfile).filter(CVProfile.id == cv_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="CV not found")
    db.delete(profile)
    db.commit()
    return {"status": "deleted", "id": cv_id}


# ── Job Requirements ─────────────────────────────────────────────────────────

@app.post("/api/jobs/parse-email")
async def parse_email_to_job(payload: dict, user: User = Depends(get_current_user)):
    """Extracts structured job requirements from a client email text."""
    email_text = payload.get("email_text", "").strip()
    if not email_text:
        raise HTTPException(status_code=422, detail="email_text is required")
    try:
        result = await anyio.to_thread.run_sync(parse_job_email, email_text)
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error("Job email parsing failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/jobs")
def save_job(payload: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Saves a parsed job requirement to the database."""
    data = payload.get("data", {})
    job = JobRequirement(
        title=data.get("title", "Untitled"),
        description=data.get("description"),
        required_skills=json.dumps(data.get("required_skills", [])),
        nice_to_have_skills=json.dumps(data.get("nice_to_have_skills", [])),
        experience_years=data.get("experience_years"),
        location=data.get("location"),
        remote=str(data.get("remote")).lower() if data.get("remote") is not None else None,
        raw_email=payload.get("raw_email"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"status": "success", "id": job.id}

@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Lists all job requirements."""
    jobs = db.query(JobRequirement).order_by(JobRequirement.created_at.desc()).all()
    return [_job_to_dict(j) for j in jobs]

@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Gets a single job requirement."""
    job = db.query(JobRequirement).filter(JobRequirement.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_dict(job)

@app.patch("/api/jobs/{job_id}/status")
def update_job_status(job_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Updates the status of a job requirement."""
    valid = {"open", "filled", "cancelled"}
    new_status = payload.get("status")
    if new_status not in valid:
        raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {sorted(valid)}")
    job = db.query(JobRequirement).filter(JobRequirement.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = new_status
    db.commit()
    return {"id": job_id, "status": new_status}

@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Deletes a job requirement."""
    job = db.query(JobRequirement).filter(JobRequirement.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    db.delete(job)
    db.commit()
    return {"status": "deleted", "id": job_id}

def _job_to_dict(j: JobRequirement) -> dict:
    return {
        "id": j.id,
        "title": j.title,
        "description": j.description,
        "required_skills": json.loads(j.required_skills or "[]"),
        "nice_to_have_skills": json.loads(j.nice_to_have_skills or "[]"),
        "experience_years": j.experience_years,
        "location": j.location,
        "remote": j.remote,
        "status": j.status or "open",
        "created_at": j.created_at,
    }


# ── CV Matching ───────────────────────────────────────────────────────────────

from backend.matching import match_score


@app.get("/api/jobs/{job_id}/matches")
def match_candidates(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Returns all candidates scored and sorted against a job requirement."""
    job = db.query(JobRequirement).filter(JobRequirement.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    required = json.loads(job.required_skills or "[]")
    nice = json.loads(job.nice_to_have_skills or "[]")

    profiles = db.query(CVProfile).filter(CVProfile.status != "rejected").all()
    results = []
    for p in profiles:
        raw = json.loads(p.raw_json or "{}") if p.raw_json else {}
        candidate_skills = [
            s for group in raw.get("skill_matrix", [])
            for s in group.get("skills", [])
        ]
        match = match_score(
            candidate_skills, required, nice,
            candidate_years=raw.get("total_experience_years"),
            required_years=job.experience_years,
            candidate_location=p.location,
            job_location=job.location,
            job_remote=job.remote,
        )
        results.append({
            "id": p.id,
            "name": p.name,
            "email": p.email,
            "status": p.status or "new",
            **match,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


@app.get("/api/cvs/{cv_id}/matches")
def match_jobs_for_candidate(cv_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Reverse matching: for a given CV, returns all OPEN jobs scored against this candidate."""
    profile = db.query(CVProfile).filter(CVProfile.id == cv_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="CV not found")

    raw = json.loads(profile.raw_json or "{}") if profile.raw_json else {}
    candidate_skills = [
        s for group in raw.get("skill_matrix", [])
        for s in group.get("skills", [])
    ]
    candidate_years = raw.get("total_experience_years")

    jobs = db.query(JobRequirement).filter(JobRequirement.status == "open").all()
    results = []
    for j in jobs:
        required = json.loads(j.required_skills or "[]")
        nice = json.loads(j.nice_to_have_skills or "[]")
        match = match_score(
            candidate_skills, required, nice,
            candidate_years=candidate_years,
            required_years=j.experience_years,
            candidate_location=profile.location,
            job_location=j.location,
            job_remote=j.remote,
        )
        results.append({
            "id": j.id,
            "title": j.title,
            "location": j.location,
            "remote": j.remote,
            "experience_years": j.experience_years,
            **match,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ── OAuth / Email Connection ─────────────────────────────────────────────────

from datetime import datetime, timedelta
import secrets as _secrets

_OAUTH_STATE_TTL_MINUTES = 10


def _make_state_token(user_id: int) -> str:
    """Short-lived JWT that survives the round trip to Microsoft, so the
    callback knows which logged-in user is connecting and that the request
    actually originated from us (CSRF protection)."""
    payload = {
        "uid": user_id,
        "nonce": _secrets.token_urlsafe(16),
        "scope": "oauth_state",
        "exp": datetime.utcnow() + timedelta(minutes=_OAUTH_STATE_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_state_token(token: str) -> int:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    if payload.get("scope") != "oauth_state":
        raise HTTPException(status_code=400, detail="Token scope mismatch")
    return int(payload["uid"])


@app.get("/api/oauth/microsoft/authorize")
def oauth_microsoft_authorize(user: User = Depends(get_current_user)):
    """Returns the Microsoft sign-in URL. Frontend redirects the browser to it."""
    if not oauth_microsoft.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Microsoft OAuth not configured on this server. Set MICROSOFT_CLIENT_ID/SECRET in .env.",
        )
    state = _make_state_token(user.id)
    url = oauth_microsoft.build_authorize_url(state=state)
    return {"url": url}


@app.get("/api/oauth/microsoft/callback")
async def oauth_microsoft_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    """Handles the Microsoft redirect after the user signs in.

    On success: stores the (encrypted) refresh token and redirects the user
    back to the frontend with `?connected=microsoft`. On failure: redirects
    with `?oauth_error=<msg>`.
    """
    # Nginx proxies / to the frontend container and /api to this backend,
    # so a relative redirect lands on the SPA root.
    frontend_url = "/"

    if error:
        msg = error_description or error
        return RedirectResponse(url=f"{frontend_url}?oauth_error={msg}")
    if not code or not state:
        return RedirectResponse(url=f"{frontend_url}?oauth_error=Missing+code+or+state")

    try:
        user_id = _decode_state_token(state)
    except HTTPException as e:
        return RedirectResponse(url=f"{frontend_url}?oauth_error={e.detail}")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse(url=f"{frontend_url}?oauth_error=Unknown+user")

    try:
        tokens = await anyio.to_thread.run_sync(oauth_microsoft.exchange_code_for_tokens, code)
    except ValueError as e:
        logger.warning("OAuth exchange failed: %s", e)
        return RedirectResponse(url=f"{frontend_url}?oauth_error={e}")
    except Exception:
        logger.exception("Unexpected error during OAuth exchange")
        return RedirectResponse(url=f"{frontend_url}?oauth_error=Unexpected+server+error")

    # Replace any existing connection for this user+provider (re-connect flow)
    existing = (
        db.query(OAuthAccount)
        .filter(OAuthAccount.user_id == user.id, OAuthAccount.provider == "microsoft")
        .first()
    )
    if existing:
        db.delete(existing)

    account = OAuthAccount(
        user_id=user.id,
        provider="microsoft",
        email_address=tokens["email_address"],
        refresh_token_encrypted=crypto.encrypt(tokens["refresh_token"]),
        scopes=tokens["scopes"],
    )
    db.add(account)
    db.commit()

    return RedirectResponse(url=f"{frontend_url}?connected=microsoft")


@app.get("/api/oauth/status")
def oauth_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Returns which email providers the current user has connected."""
    accounts = (
        db.query(OAuthAccount).filter(OAuthAccount.user_id == user.id).all()
    )
    by_provider = {
        a.provider: {
            "connected": True,
            "email": a.email_address,
            "connected_at": a.connected_at,
        }
        for a in accounts
    }
    return {
        "microsoft": by_provider.get("microsoft", {"connected": False, "configured": oauth_microsoft.is_configured()}),
    }


@app.post("/api/oauth/microsoft/disconnect")
def oauth_microsoft_disconnect(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Removes the stored Microsoft OAuth connection for the current user."""
    account = (
        db.query(OAuthAccount)
        .filter(OAuthAccount.user_id == user.id, OAuthAccount.provider == "microsoft")
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="No Microsoft account connected")
    db.delete(account)
    db.commit()
    return {"status": "disconnected"}


