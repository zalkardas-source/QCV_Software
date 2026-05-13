"""
Löscht alle gespeicherten Bewerbungen (CVProfile, Skills, Projects) und den Parsing-Cache.
Benutzer und Job-Requirements bleiben erhalten.

Aufruf: python -m scripts.clear_data
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from backend.database import engine

def clear_data():
    with engine.begin() as conn:
        cache_rows = conn.execute(text("SELECT COUNT(*) FROM parsing_cache")).scalar()
        cv_rows    = conn.execute(text("SELECT COUNT(*) FROM cv_profiles")).scalar()
        skill_rows = conn.execute(text("SELECT COUNT(*) FROM skills")).scalar()
        proj_rows  = conn.execute(text("SELECT COUNT(*) FROM projects")).scalar()

        print(f"Gefunden: {cv_rows} Bewerbungen, {skill_rows} Skills, {proj_rows} Projekte, {cache_rows} Cache-Einträge")
        confirm = input("Wirklich alles löschen? (ja/nein): ").strip().lower()
        if confirm != "ja":
            print("Abgebrochen.")
            return

        conn.execute(text("DELETE FROM skills"))
        conn.execute(text("DELETE FROM projects"))
        conn.execute(text("DELETE FROM cv_profiles"))
        conn.execute(text("DELETE FROM parsing_cache"))

    print("Fertig — alle Bewerbungen und der Cache wurden gelöscht.")

if __name__ == "__main__":
    clear_data()
