"""Tests for SQLite AUTOINCREMENT on externally-referenced IDs.

Background (incident 2026-06-03): plain SQLite INTEGER PRIMARY KEY reuses IDs
after rows are deleted. After a test-data reset, a newly uploaded candidate
received the ID of a deleted one — and the browser served the old candidate's
cached photo under the same /api/cvs/{id}/photo URL. AUTOINCREMENT keeps a
high-water mark in sqlite_sequence so an ID is never handed out twice.

Covers:
- fresh tables created from the models never reuse a deleted ID
- _ensure_sqlite_autoincrement migrates a legacy DB in place (data preserved,
  AUTOINCREMENT added, idempotent, sequence seeded past existing rows)
"""
import sys
from unittest.mock import MagicMock

# WeasyPrint is a runtime-only dep (lives in the backend container). Stub it
# here so `from backend.main import ...` works in the local test env.
sys.modules.setdefault("weasyprint", MagicMock())

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models import CVProfile, CVVersion


def _memory_engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _table_ddl(engine, name):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": name},
        ).scalar()


# ── fresh schema (create_all) ───────────────────────────────────────────────

def test_fresh_schema_has_autoincrement():
    engine = _memory_engine()
    Base.metadata.create_all(bind=engine)
    for name in ("cv_profiles", "cv_versions"):
        assert "AUTOINCREMENT" in _table_ddl(engine, name).upper(), name


def test_deleted_profile_id_is_never_reused():
    """The actual regression: wipe all rows, insert again → fresh ID."""
    engine = _memory_engine()
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    db = Session()
    first = CVProfile(name="Old Candidate")
    db.add(first)
    db.commit()
    old_id = first.id

    db.query(CVProfile).delete()
    db.commit()

    second = CVProfile(name="New Candidate")
    db.add(second)
    db.commit()

    assert second.id > old_id  # without AUTOINCREMENT this would equal old_id
    db.close()


# ── migration of a legacy DB ────────────────────────────────────────────────

def _legacy_engine():
    """Build a DB the way it looked before this fix: same columns, but the
    primary key without AUTOINCREMENT."""
    engine = _memory_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE cv_profiles ("
            " id INTEGER NOT NULL, filename VARCHAR, name VARCHAR,"
            " email VARCHAR, location VARCHAR, small_summary TEXT,"
            " raw_json TEXT, photo_path VARCHAR, status VARCHAR,"
            " created_at DATETIME, PRIMARY KEY (id))"
        ))
        conn.execute(text(
            "CREATE TABLE cv_versions ("
            " id INTEGER NOT NULL, cv_profile_id INTEGER, version_number INTEGER,"
            " snapshot_json TEXT, source_filename VARCHAR, photo_path VARCHAR,"
            " created_at DATETIME, PRIMARY KEY (id),"
            " FOREIGN KEY(cv_profile_id) REFERENCES cv_profiles (id))"
        ))
        conn.execute(text(
            "INSERT INTO cv_profiles (id, name, status) VALUES (7, 'Existing Person', 'new')"
        ))
        conn.execute(text(
            "INSERT INTO cv_versions (id, cv_profile_id, version_number, photo_path)"
            " VALUES (3, 7, 1, '7.jpg')"
        ))
    return engine


def test_migration_adds_autoincrement_and_preserves_rows():
    from backend.main import _ensure_sqlite_autoincrement

    engine = _legacy_engine()
    assert "AUTOINCREMENT" not in _table_ddl(engine, "cv_profiles").upper()

    _ensure_sqlite_autoincrement(bind=engine)

    for name in ("cv_profiles", "cv_versions"):
        assert "AUTOINCREMENT" in _table_ddl(engine, name).upper(), name
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id, name FROM cv_profiles")).fetchone()
        assert row == (7, "Existing Person")
        version = conn.execute(
            text("SELECT id, cv_profile_id, photo_path FROM cv_versions")
        ).fetchone()
        assert version == (3, 7, "7.jpg")


def test_migration_seeds_sequence_so_old_ids_stay_retired():
    """After migrating, delete everything and re-insert: IDs continue past the
    pre-migration maximum instead of starting over."""
    from backend.main import _ensure_sqlite_autoincrement

    engine = _legacy_engine()
    _ensure_sqlite_autoincrement(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cv_versions"))
        conn.execute(text("DELETE FROM cv_profiles"))
        conn.execute(text("INSERT INTO cv_profiles (name, status) VALUES ('Next', 'new')"))
        new_id = conn.execute(text("SELECT id FROM cv_profiles")).scalar()
    assert new_id > 7


def test_migration_is_idempotent():
    """Running the migration twice must not error or duplicate data."""
    from backend.main import _ensure_sqlite_autoincrement

    engine = _legacy_engine()
    _ensure_sqlite_autoincrement(bind=engine)
    _ensure_sqlite_autoincrement(bind=engine)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM cv_profiles")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM cv_versions")).scalar() == 1
