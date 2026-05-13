"""
One-time migration: adds the 'status' column to cv_profiles.
Run from the project root: python scripts/migrate_add_status.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from backend.database import engine

with engine.connect() as conn:
    columns = [row[1] for row in conn.execute(text("PRAGMA table_info(cv_profiles)"))]
    if "status" in columns:
        print("Column 'status' already exists — nothing to do.")
    else:
        conn.execute(text("ALTER TABLE cv_profiles ADD COLUMN status VARCHAR DEFAULT 'new'"))
        conn.commit()
        print("Column 'status' added successfully.")
