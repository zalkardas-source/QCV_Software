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
from backend.models import User, CVProfile, Skill, Project, BatchJob, BatchJobItem
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

SECRET_KEY = os.getenv("JWT_SECRET", "super-secret-key-change-in-prod")
ALGORITHM = "HS256"

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
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

@app.post("/api/parse-cv")
def parse_cv(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """Uploads a CV, parses it with Docling, and structures it with Minimax."""
    try:
        content = file.file.read()
        
        # 1. Extract markdown
        raw_markdown = extract_text_from_file(content, file.filename)
        
        # 2. LLM structure
        structured_data = structure_cv_data(raw_markdown)
        
        return {
            "status": "success",
            "filename": file.filename,
            "data": structured_data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
        
        # 2. Create Skills
        skills_data = data.get("skill_matrix", [])
        for s in skills_data:
            rating_val = None
            if s.get("rating"):
                try:
                    rating_val = int(s.get("rating"))
                except ValueError:
                    pass
            skill_obj = Skill(
                cv_profile_id=profile.id,
                name=s.get("skill", ""),
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
def export_pptx(payload: dict, user: User = Depends(get_current_user)):
    """Generates a PowerPoint presentation from JSON data."""
    try:
        data = payload.get("data", {})
        if not data:
            raise ValueError("No data provided")
            
        pptx_bytes = create_pptx_summary(data)
        
        personal = data.get("personal_information", {})
        name = personal.get("full_name", "CV")
        filename = f"{name.replace(' ', '_')}_Summary.pptx"
        
        return Response(
            content=pptx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cvs/{cv_id}/pptx")
def download_cv_pptx(cv_id: int, token: str = None, db: Session = Depends(get_db)):
    """Direct PPTX download by CV ID. Uses token query param for auth."""
    if not token:
        raise HTTPException(status_code=401, detail="Token required")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
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


