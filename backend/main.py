from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
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
from backend.models import User, CVProfile, Skill, Project, JobRequirement
from backend.services import extract_text_from_file, structure_cv_data, warmup_docling, parse_job_email
from backend.export_pptx import create_pptx_summary

# Initialize DB tables
Base.metadata.create_all(bind=engine)


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

# Mount the static frontend
import os
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend')
app.mount("/frontend", StaticFiles(directory=frontend_dir), name="frontend")

from fastapi.responses import RedirectResponse

@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/frontend/index.html")

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

import hashlib
import anyio

def calculate_file_hash(file_content: bytes) -> str:
    return hashlib.sha256(file_content).hexdigest()

@app.post("/api/parse-cv")
async def parse_cv(
    file: UploadFile = File(...), 
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Uploads a CV, checks cache, then parses it if needed. Optimized with Async/Threading."""
    try:
        content = await file.read()
        file_hash = calculate_file_hash(content)
        
        # 1. Check Cache
        from backend.models import ParsingCache
        cached_item = db.query(ParsingCache).filter(ParsingCache.file_hash == file_hash).first()
        if cached_item:
            return {
                "status": "success",
                "filename": file.filename,
                "data": json.loads(cached_item.json_data),
                "cached": True
            }
        
        # 2. Extract markdown (CPU heavy -> Thread)
        raw_markdown = await anyio.to_thread.run_sync(
            extract_text_from_file, content, file.filename
        )
        
        # 3. LLM structure (Network bound -> Thread for consistency/timeout safety)
        structured_data = await anyio.to_thread.run_sync(
            structure_cv_data, raw_markdown
        )
        
        # 4. Save to Cache
        new_cache = ParsingCache(
            file_hash=file_hash,
            json_data=json.dumps(structured_data)
        )
        db.add(new_cache)
        db.commit()
        
        return {
            "status": "success",
            "filename": file.filename,
            "data": structured_data,
            "cached": False
        }
    except Exception as e:
        logger.error("Fatal error in parse_cv", exc_info=True)
        raise HTTPException(status_code=500, detail=f"{str(e)}")

@app.post("/api/save-cv")
def save_cv(payload: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Saves final reviewed CV data to the database."""
    try:
        data = payload.get("data", {})
        filename = payload.get("filename", "unknown.pdf")
        
        personal = data.get("personal_information", {})
        name = personal.get("full_name", "Unknown Name")
        email = personal.get("email", "")
        location = personal.get("location", "")
        small_summary = data.get("small_summary", "")
        
        # 1. Create the Profile
        profile = CVProfile(
            filename=filename,
            name=name,
            email=email,
            location=location,
            small_summary=small_summary,
            raw_json=json.dumps(data)
        )
        db.add(profile)
        db.flush() # Flush to get the profile.id
        
        # 2. Create Skills (Nested Category Structure)
        skill_matrix = data.get("skill_matrix", [])
        for group in skill_matrix:
            cat_name = group.get("category", "General")
            skills_list = group.get("skills", [])
            for s in skills_list:
                rating_val = None
                if s.get("rating"):
                    try:
                        rating_val = int(s.get("rating"))
                    except ValueError:
                        pass
                skill_obj = Skill(
                    cv_profile_id=profile.id,
                    name=s.get("skill", ""),
                    category=cat_name,
                    rating=rating_val
                )
                db.add(skill_obj)
            
        # 3. Create Projects
        projects_data = data.get("projects", [])
        for p in projects_data:
            proj_obj = Project(
                cv_profile_id=profile.id,
                name=p.get("name", ""),
                duration=p.get("duration", ""),
                description=p.get("description", "")
            )
            db.add(proj_obj)
            
        db.commit()
        db.refresh(profile)
        
        return {"status": "success", "id": profile.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/export-pptx")
async def export_pptx(payload: dict, user: User = Depends(get_current_user)):
    """Generates a PowerPoint presentation from JSON data (Optimized)."""
    try:
        data = payload.get("data", {})
        if not data: raise ValueError("No data provided")
            
        pptx_bytes = await anyio.to_thread.run_sync(create_pptx_summary, data)
        
        personal = data.get("personal_information", {})
        name = personal.get("full_name", "CV")
        filename = f"{name.replace(' ', '_')}_Summary.pptx"
        
        return Response(
            content=pptx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        logger.error("PPTX export error: %s", e)
        raise HTTPException(status_code=500, detail=f"{str(e)}")

@app.get("/api/cvs/{cv_id}/pptx")
def download_cv_pptx(cv_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):

    profile = db.query(CVProfile).filter(CVProfile.id == cv_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="CV not found")
    
    data = json.loads(profile.raw_json) if profile.raw_json else {}
    pptx_bytes = create_pptx_summary(data)
    
    name = (profile.name or "CV").replace(' ', '_')
    filename = f"{name}_Summary.pptx"
    
    return Response(
        content=pptx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
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
        "raw_json": p.raw_json
    } for p in profiles]

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

# Normalizes common German↔English skill name variations so matching works
# regardless of the CV's language.
_SKILL_ALIASES: dict[str, str] = {
    # Languages
    "englisch": "english", "deutsch": "german", "französisch": "french",
    "spanisch": "spanish", "italienisch": "italian", "russisch": "russian",
    "chinesisch": "chinese", "japanisch": "japanese", "arabisch": "arabic",
    "portugiesisch": "portuguese", "niederländisch": "dutch", "türkisch": "turkish",
    "polnisch": "polish", "schwedisch": "swedish", "dänisch": "danish",
    "norwegisch": "norwegian", "finnisch": "finnish", "koreanisch": "korean",
    # MS Office variants
    "microsoft excel": "excel", "ms excel": "excel",
    "microsoft word": "word", "ms word": "word",
    "microsoft powerpoint": "powerpoint", "ms powerpoint": "powerpoint",
    "microsoft office": "ms office", "microsoft 365": "ms office",
    # Common synonyms
    "javascript": "js", "js": "javascript",
    "typescript": "ts", "ts": "typescript",
    "node.js": "nodejs", "nodejs": "node.js",
    "react.js": "react", "vue.js": "vue", "angular.js": "angular",
    "postgresql": "postgres", "postgres": "postgresql",
}

def _normalize(name: str) -> str:
    n = name.lower().strip()
    return _SKILL_ALIASES.get(n, n)


def _match_score(candidate_skills: list, required: list, nice: list) -> dict:
    """Scores a candidate's skills against job requirements. No LLM needed."""
    # Build normalized skill map: both the original and normalized name point to the same rating
    skill_map: dict[str, int] = {}
    for s in candidate_skills:
        raw_name = s.get("skill", "").lower().strip()
        if not raw_name:
            continue
        rating = s.get("rating", 4)
        skill_map[raw_name] = rating
        norm = _normalize(raw_name)
        if norm != raw_name:
            skill_map[norm] = rating
        # Also add the reverse alias so "english" maps when CV says "englisch"
        for alias_from, alias_to in _SKILL_ALIASES.items():
            if raw_name == alias_to and alias_from not in skill_map:
                skill_map[alias_from] = rating

    def find(job_skill: str) -> int | None:
        needle = _normalize(job_skill.lower().strip())
        # 1. Exact match (after normalization)
        if needle in skill_map:
            return skill_map[needle]
        # 2. Substring match
        for k, v in skill_map.items():
            if needle in k or k in needle:
                return v
        return None

    total_weight = len(required) * 1.0 + len(nice) * 0.5
    if total_weight == 0:
        return {"score": 0, "matched_required": [], "missing_required": [], "matched_nice": []}

    score = 0.0
    matched_required, missing_required, matched_nice = [], [], []

    for skill in required:
        rating = find(skill)
        if rating is not None:
            score += (rating / 10) * 1.0
            matched_required.append(skill)
        else:
            missing_required.append(skill)

    for skill in nice:
        rating = find(skill)
        if rating is not None:
            score += (rating / 10) * 0.5
            matched_nice.append(skill)

    return {
        "score": round((score / total_weight) * 100),
        "matched_required": matched_required,
        "missing_required": missing_required,
        "matched_nice": matched_nice,
    }


@app.get("/api/jobs/{job_id}/matches")
def match_candidates(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Returns all candidates scored and sorted against a job requirement."""
    job = db.query(JobRequirement).filter(JobRequirement.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    required = json.loads(job.required_skills or "[]")
    nice = json.loads(job.nice_to_have_skills or "[]")

    profiles = db.query(CVProfile).all()
    results = []
    for p in profiles:
        raw = json.loads(p.raw_json or "{}") if p.raw_json else {}
        candidate_skills = [
            s for group in raw.get("skill_matrix", [])
            for s in group.get("skills", [])
        ]
        match = _match_score(candidate_skills, required, nice)
        results.append({
            "id": p.id,
            "name": p.name,
            "email": p.email,
            "status": p.status or "new",
            **match,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


