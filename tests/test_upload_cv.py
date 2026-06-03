"""Tests for the auto-save upload flow.

Covers:
- review_cv_data fail-safe behavior (API down, invalid JSON, schema mismatch)
- review_cv_data happy-path (returns the corrected dict)
- /api/upload-cv end-to-end with extract + structure mocked
"""
import json
import sys
from unittest.mock import patch, MagicMock

# WeasyPrint is a runtime-only dep (lives in the backend container). Stub it
# here so `from backend.main import app` works in the local test env.
sys.modules.setdefault("weasyprint", MagicMock())

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.models import User, CVProfile, Skill, Project
from backend.auth import get_password_hash
from backend import services


# ── Minimal valid CV payload used in several tests ──────────────────────────
_VALID_CV = {
    "personal_information": {"full_name": "Jane Doe", "email": "jane@example.com"},
    "small_summary": "Short summary.",
    "total_experience_years": 3,
    "skill_matrix": [
        {"category": "Programming Languages", "skills": [{"skill": "Python", "rating": 8}]}
    ],
    "projects": [{"name": "Project A", "description": "Did work."}],
}


def _make_openai_response(content: str):
    """Build a mock OpenAI ChatCompletion response with the given message content."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── review_cv_data unit tests ──────────────────────────────────────────────

def test_review_returns_corrected_dict_on_happy_path():
    """Reviewer returns valid JSON → that JSON is parsed, validated, and returned."""
    corrected = dict(_VALID_CV)
    corrected["small_summary"] = "Corrected summary."

    with patch("backend.services.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(
            json.dumps(corrected)
        )

        result = services.review_cv_data("raw markdown", _VALID_CV)

    assert result["small_summary"] == "Corrected summary."


def test_review_fails_safe_when_api_raises():
    """API exception on both attempts → returns original parsed_data unchanged."""
    with patch("backend.services.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")

        result = services.review_cv_data("raw markdown", _VALID_CV)

    assert result == _VALID_CV


def test_review_fails_safe_on_invalid_json_after_retry():
    """Reviewer returns garbage twice → fail-safe back to parsed_data."""
    with patch("backend.services.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response("not json {{{")

        result = services.review_cv_data("raw markdown", _VALID_CV)

    assert result == _VALID_CV
    # Should have made the retry attempt too
    assert mock_client.chat.completions.create.call_count == 2


def test_review_fails_safe_on_schema_violation():
    """Reviewer returns JSON missing the required full_name → fail-safe."""
    bad_payload = json.dumps({"personal_information": {}, "skill_matrix": []})
    with patch("backend.services.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(bad_payload)

        result = services.review_cv_data("raw markdown", _VALID_CV)

    assert result == _VALID_CV


def test_review_strips_markdown_fences():
    """Reviewer wraps JSON in ```json ... ``` fences → we still parse it."""
    fenced = "```json\n" + json.dumps(_VALID_CV) + "\n```"
    with patch("backend.services.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(fenced)

        result = services.review_cv_data("raw markdown", _VALID_CV)

    assert result["personal_information"]["full_name"] == "Jane Doe"


# ── SAP normalization ──────────────────────────────────────────────────────

def test_sap_module_normalization_collapses_submodules():
    assert services._normalize_sap_skill("SAP MM") == "SAP MM"
    assert services._normalize_sap_skill("MM") == "SAP MM"
    assert services._normalize_sap_skill("SAP MM-IM") == "SAP MM"
    assert services._normalize_sap_skill("MM-IM") == "SAP MM"
    assert services._normalize_sap_skill("MM_MI") == "SAP MM"
    assert services._normalize_sap_skill("MM/IV") == "SAP MM"
    assert services._normalize_sap_skill("SAP FI-AP") == "SAP FI"
    assert services._normalize_sap_skill("CO-PA") == "SAP CO"
    assert services._normalize_sap_skill("HCM-PY") == "SAP HCM"


def test_sap_product_normalization():
    assert services._normalize_sap_skill("S/4HANA") == "SAP S/4HANA"
    assert services._normalize_sap_skill("SAP S/4HANA") == "SAP S/4HANA"
    assert services._normalize_sap_skill("Fiori") == "SAP Fiori"
    assert services._normalize_sap_skill("Ariba") == "SAP Ariba"


def test_sap_normalization_returns_none_for_non_sap():
    assert services._normalize_sap_skill("Python") is None
    assert services._normalize_sap_skill("React") is None
    assert services._normalize_sap_skill("PostgreSQL") is None


def test_apply_overrides_dedupes_sap_submodules():
    """Skills like SAP MM and SAP MM-IM should collapse into one."""
    matrix = [
        {"category": "SAP", "skills": [
            {"skill": "SAP MM", "rating": 7},
            {"skill": "SAP MM-IM", "rating": 8},
            {"skill": "MM-MI", "rating": 6},
        ]},
    ]
    out = services._apply_category_overrides(matrix)
    sap = next(g for g in out if g["category"] == "SAP")
    assert len(sap["skills"]) == 1
    assert sap["skills"][0]["skill"] == "SAP MM"
    assert sap["skills"][0]["rating"] == 8  # highest wins


def test_apply_overrides_sorts_within_category():
    """Stable ordering inside each category: rating desc, then name."""
    matrix = [
        {"category": "Programming Languages", "skills": [
            {"skill": "Python", "rating": 7},
            {"skill": "Java", "rating": 9},
            {"skill": "Go", "rating": 7},
        ]},
    ]
    out = services._apply_category_overrides(matrix)
    names = [s["skill"] for s in out[0]["skills"]]
    assert names == ["Java", "Go", "Python"]


# ── _fuse_skills unit tests ────────────────────────────────────────────────

def test_fuse_adds_mined_skill_not_in_initial():
    """Mining contributes a new skill the extractor missed."""
    initial = [{"category": "Programming Languages", "skills": [{"skill": "Python", "rating": 7}]}]
    mined = [{"category": "Tools & Platforms", "skills": [{"skill": "Kafka", "rating": 5}]}]
    fused = services._fuse_skills(initial, mined)

    flat = {name: (cat, rating) for name, cat, rating in services._flatten_skills(fused)}
    assert "Python" in flat
    assert "Kafka" in flat
    assert flat["Kafka"] == ("Tools & Platforms", 5)


def test_fuse_keeps_initial_category_and_higher_rating_on_duplicate():
    """Same skill in both lists → initial category wins, max rating wins."""
    initial = [{"category": "Programming Languages", "skills": [{"skill": "Python", "rating": 6}]}]
    mined = [{"category": "Tools & Platforms", "skills": [{"skill": "python", "rating": 8}]}]
    fused = services._fuse_skills(initial, mined)

    flat = {name: (cat, rating) for name, cat, rating in services._flatten_skills(fused)}
    # Initial categorization is preserved
    assert flat["Python"] == ("Programming Languages", 8)
    # Lowercase variant from mining was deduped
    assert "python" not in flat


def test_fuse_drops_unknown_categories():
    """Skills with categories outside the allow-list are silently dropped."""
    initial = [{"category": "Programming Languages", "skills": [{"skill": "Python", "rating": 7}]}]
    mined = [{"category": "Soft Skills", "skills": [{"skill": "Leadership", "rating": 9}]}]
    fused = services._fuse_skills(initial, mined)

    flat_names = [name for name, _, _ in services._flatten_skills(fused)]
    assert "Leadership" not in flat_names
    assert "Python" in flat_names


def test_fuse_empty_mined_returns_initial_unchanged():
    initial = [{"category": "Programming Languages", "skills": [{"skill": "Python", "rating": 7}]}]
    fused = services._fuse_skills(initial, [])
    flat = {name: (cat, rating) for name, cat, rating in services._flatten_skills(fused)}
    assert flat == {"Python": ("Programming Languages", 7)}


# ── _mine_project_skills fail-safe tests ───────────────────────────────────

def test_mine_returns_empty_list_when_api_fails():
    with patch("backend.services.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")

        result = services._mine_project_skills("markdown", _VALID_CV)

    assert result == []


def test_mine_returns_empty_list_when_response_is_garbage():
    with patch("backend.services.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response("not json")

        result = services._mine_project_skills("markdown", _VALID_CV)

    assert result == []


def test_mine_returns_parsed_list_on_happy_path():
    payload = {"skill_matrix": [
        {"category": "Tools & Platforms", "skills": [{"skill": "Kafka", "rating": 6}]}
    ]}
    with patch("backend.services.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_openai_response(json.dumps(payload))

        result = services._mine_project_skills("markdown", _VALID_CV)

    assert result == payload["skill_matrix"]


# ── /api/upload-cv end-to-end with isolated DB ─────────────────────────────

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(autouse=True, scope="module")
def _isolate_photo_dir(tmp_path_factory):
    """Redirect the photo storage dir to a temporary location so the test suite
    never writes into backend/data/photos (which would clobber real uploads)."""
    from backend import main as backend_main
    tmp_dir = tmp_path_factory.mktemp("photos")
    original = backend_main._PHOTO_DIR
    backend_main._PHOTO_DIR = tmp_dir
    yield
    backend_main._PHOTO_DIR = original


def _override_get_db():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=_engine)
    db = _SessionLocal()
    db.add(User(email="upload@example.com", hashed_password=get_password_hash("Pwd12345!")))
    db.commit()
    db.close()

    from backend.main import app
    app.dependency_overrides[get_db] = _override_get_db

    with patch("backend.services.warmup_docling"), TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture
def auth_token(client):
    response = client.post("/api/login", data={
        "username": "upload@example.com",
        "password": "Pwd12345!",
    })
    return response.json()["access_token"]


def test_upload_cv_persists_photo_bytes_when_extracted(client, auth_token):
    """When extract_text_and_photo returns photo bytes, they end up on disk and
    photo_path is set on the row."""
    fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
    cv = dict(_VALID_CV)
    cv["personal_information"] = {"full_name": "Photo Person", "email": "photo@example.com"}
    with patch("backend.main.extract_text_and_photo", return_value=("md", fake_jpeg)), \
         patch("backend.main.structure_cv_data", return_value=cv):
        r = client.post(
            "/api/upload-cv",
            files={"file": ("p.pdf", b"x", "application/pdf")},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
    assert r.status_code == 200
    assert r.json()["has_photo"] is True

    db = _SessionLocal()
    try:
        profile = db.query(CVProfile).filter(CVProfile.email == "photo@example.com").first()
        assert profile.photo_path is not None
        assert profile.photo_path.endswith(f"{profile.id}.jpg")
    finally:
        db.close()


def test_photo_endpoint_sends_no_store_cache_header(client, auth_token):
    """The photo behind /api/cvs/{id}/photo can change (re-upload, or ID reuse
    after a DB reset) while the URL stays the same. A cacheable response made
    the browser show the previous candidate's photo — regression guard for
    the stale-photo bug found 2026-06-03."""
    fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
    cv = dict(_VALID_CV)
    cv["personal_information"] = {"full_name": "Cache Person", "email": "cache@example.com"}
    with patch("backend.main.extract_text_and_photo", return_value=("md", fake_jpeg)), \
         patch("backend.main.structure_cv_data", return_value=cv):
        r = client.post(
            "/api/upload-cv",
            files={"file": ("c.pdf", b"x", "application/pdf")},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
    assert r.status_code == 200
    cv_id = r.json()["id"]

    r = client.get(
        f"/api/cvs/{cv_id}/photo",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.content == fake_jpeg


def test_upload_cv_runs_pipeline_and_persists(client, auth_token):
    """End-to-end: file upload → mocked parse → mocked review → row in DB."""
    with patch("backend.main.extract_text_and_photo", return_value=("markdown body", None)), \
         patch("backend.main.structure_cv_data", return_value=_VALID_CV):

        response = client.post(
            "/api/upload-cv",
            files={"file": ("jane.pdf", b"fake-pdf-bytes", "application/pdf")},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["name"] == "Jane Doe"
    assert isinstance(body["id"], int)

    # Verify the row landed in the DB with skills + projects
    db = _SessionLocal()
    try:
        profile = db.query(CVProfile).filter(CVProfile.id == body["id"]).first()
        assert profile is not None
        assert profile.name == "Jane Doe"
        assert profile.email == "jane@example.com"

        skills = db.query(Skill).filter(Skill.cv_profile_id == profile.id).all()
        assert [s.name for s in skills] == ["Python"]
        assert skills[0].category == "Programming Languages"
        assert skills[0].rating == 8

        projects = db.query(Project).filter(Project.cv_profile_id == profile.id).all()
        assert [p.name for p in projects] == ["Project A"]
    finally:
        db.close()


def test_upload_cv_requires_auth(client):
    response = client.post(
        "/api/upload-cv",
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 401


def test_reupload_with_same_email_updates_existing_row(client, auth_token):
    """Same email on second upload → existing CVProfile is overwritten, not duplicated."""
    initial = {
        "personal_information": {"full_name": "Repeat Candidate", "email": "repeat@example.com"},
        "small_summary": "v1",
        "total_experience_years": 2,
        "skill_matrix": [{"category": "Programming Languages", "skills": [{"skill": "Python", "rating": 7}]}],
        "projects": [{"name": "Old Project", "description": "old"}],
    }
    updated = {
        "personal_information": {"full_name": "Repeat Candidate Updated", "email": "repeat@example.com"},
        "small_summary": "v2",
        "total_experience_years": 4,
        "skill_matrix": [
            {"category": "Programming Languages", "skills": [{"skill": "Python", "rating": 9}]},
            {"category": "Tools & Platforms", "skills": [{"skill": "Docker", "rating": 7}]},
        ],
        "projects": [{"name": "New Project", "description": "new"}],
    }

    with patch("backend.main.extract_text_and_photo", return_value=("md", None)), \
         patch("backend.main.structure_cv_data", return_value=initial):
        r1 = client.post(
            "/api/upload-cv",
            files={"file": ("v1.pdf", b"x", "application/pdf")},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
    first_id = r1.json()["id"]

    with patch("backend.main.extract_text_and_photo", return_value=("md", None)), \
         patch("backend.main.structure_cv_data", return_value=updated):
        r2 = client.post(
            "/api/upload-cv",
            files={"file": ("v2.pdf", b"x", "application/pdf")},
            headers={"Authorization": f"Bearer {auth_token}"},
        )

    # Same row, same id
    assert r2.status_code == 200
    assert r2.json()["id"] == first_id

    # DB now reflects only the v2 content
    db = _SessionLocal()
    try:
        rows = db.query(CVProfile).filter(CVProfile.email == "repeat@example.com").all()
        assert len(rows) == 1
        profile = rows[0]
        assert profile.name == "Repeat Candidate Updated"
        assert profile.small_summary == "v2"
        assert profile.filename == "v2.pdf"

        # Old skills/projects are gone, new ones in
        skill_names = {s.name for s in db.query(Skill).filter(Skill.cv_profile_id == profile.id).all()}
        assert skill_names == {"Python", "Docker"}
        project_names = {p.name for p in db.query(Project).filter(Project.cv_profile_id == profile.id).all()}
        assert project_names == {"New Project"}
    finally:
        db.close()


def test_upload_without_email_always_inserts(client, auth_token):
    """No email → cannot match, must create a fresh row each time."""
    no_email = {
        "personal_information": {"full_name": "Anonymous"},
        "small_summary": "s",
        "skill_matrix": [],
        "projects": [],
    }
    with patch("backend.main.extract_text_and_photo", return_value=("md", None)), \
         patch("backend.main.structure_cv_data", return_value=no_email):
        r1 = client.post(
            "/api/upload-cv",
            files={"file": ("a.pdf", b"x", "application/pdf")},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        r2 = client.post(
            "/api/upload-cv",
            files={"file": ("a.pdf", b"x", "application/pdf")},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
    assert r1.json()["id"] != r2.json()["id"]
