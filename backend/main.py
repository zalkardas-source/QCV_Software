from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import List
import json
import os
from dotenv import load_dotenv

from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from backend.auth import verify_password, create_access_token, get_password_hash
from jose import jwt, JWTError
import os

# Load environment variables from .env file
load_dotenv()

from backend.database import engine, Base, get_db, SessionLocal
from backend.models import User, CVProfile, Skill, Project
from backend.services import extract_text_from_file, structure_cv_data, warmup_docling
from backend.export_pptx import create_pptx_summary

# Initialize DB tables
Base.metadata.create_all(bind=engine)

# Create a default user if database is empty (for demo/initial setup)
def init_db():
    db = next(get_db())
    if db.query(User).count() == 0:
        default_user = User(
            email="admin@quatelio.com",
            hashed_password=get_password_hash("admin123")
        )
        db.add(default_user)
        db.commit()
init_db()

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
    allow_origins=["*"], # For dev only. Adjust in prod.
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
        import traceback
        error_trace = traceback.format_exc()
        print(f"FATAL ERROR: {error_trace}")
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
        print(f"PPTX EXPORT ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"{str(e)}")

@app.get("/api/cvs/{cv_id}/pptx")
def download_cv_pptx(cv_id: int, token: str = None, db: Session = Depends(get_db)):
    """Direct PPTX download by CV ID. Uses token query param for auth."""
    if not token:
        raise HTTPException(status_code=401, detail="Token required")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

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

@app.delete("/api/cvs/{cv_id}")
def delete_cv(cv_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Deletes a CV profile."""
    profile = db.query(CVProfile).filter(CVProfile.id == cv_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="CV not found")
    db.delete(profile)
    db.commit()
    return {"status": "deleted", "id": cv_id}


