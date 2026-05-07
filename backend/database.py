from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import os

# Use an environment variable for the DB path, default to local file inside 'data' folder
DB_PATH = os.getenv("DATABASE_URL", "sqlite:///./backend/data/qcv_app.db")
DATABASE_URL = DB_PATH

# Ensure the directory exists so SQLite doesn't fail on first run
db_dir = os.path.dirname(DATABASE_URL.replace("sqlite:///", ""))
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
