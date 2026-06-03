"""
Löscht alle gespeicherten Bewerbungen (CVProfile, Skills, Projects, Languages,
CVVersions) und alle Fotos unter backend/data/photos.
Benutzer und Job-Requirements bleiben erhalten.

Das ist der EINZIGE unterstützte Weg, die CV-Daten zurückzusetzen — bitte nicht
ad-hoc per SQL löschen: Wer eine Tabelle oder die Fotos vergisst, hinterlässt
verwaiste Dateien (z. B. "1-v0.jpg" des alten Kandidaten 1, das der nächste
Kandidat mit ID 1 erben würde).

IDs werden NICHT wiederverwendet: cv_profiles und cv_versions haben
AUTOINCREMENT (siehe backend/models.py), neue Kandidaten bekommen also auch
nach einem Reset frische IDs. Alte Browser-Tabs/Links zeigen damit ins Leere
(404) statt auf die falsche Person.

Aufruf: python -m scripts.clear_data [--yes]
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from pathlib import Path

from sqlalchemy import text
from backend.database import engine

_PHOTO_DIR = Path(__file__).resolve().parent.parent / "backend" / "data" / "photos"

# Reihenfolge: erst Kind-Tabellen, dann cv_profiles.
_CV_TABLES = ["skills", "projects", "languages", "cv_versions", "cv_profiles"]


def clear_data(assume_yes: bool = False):
    with engine.begin() as conn:
        counts = {
            t: conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            for t in _CV_TABLES
        }
        photos = sorted(_PHOTO_DIR.glob("*.jpg")) if _PHOTO_DIR.exists() else []

        summary = ", ".join(f"{n} {t}" for t, n in counts.items())
        print(f"Gefunden: {summary}, {len(photos)} Fotos")

        if not assume_yes:
            confirm = input("Wirklich alles löschen? (ja/nein): ").strip().lower()
            if confirm != "ja":
                print("Abgebrochen.")
                return

        for t in _CV_TABLES:
            conn.execute(text(f"DELETE FROM {t}"))

    for photo in photos:
        photo.unlink()

    print("Fertig — alle Bewerbungen, Versionen und Fotos wurden gelöscht.")
    print("Hinweis: Neue Uploads bekommen frische IDs (AUTOINCREMENT, keine Wiederverwendung).")


if __name__ == "__main__":
    clear_data(assume_yes="--yes" in sys.argv)
