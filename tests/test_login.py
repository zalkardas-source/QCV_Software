"""Integration tests for the /api/login endpoint."""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.models import User
from backend.auth import get_password_hash

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


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
    db.add(User(email="test@example.com", hashed_password=get_password_hash("SecurePass123!")))
    db.commit()
    db.close()

    from backend.main import app
    app.dependency_overrides[get_db] = _override_get_db

    with patch("backend.services.warmup_docling"), TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=_engine)


def test_login_correct_credentials(client):
    response = client.post("/api/login", data={
        "username": "test@example.com",
        "password": "SecurePass123!",
    })
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_wrong_password(client):
    response = client.post("/api/login", data={
        "username": "test@example.com",
        "password": "wrongpassword",
    })
    assert response.status_code == 401


def test_login_unknown_user(client):
    response = client.post("/api/login", data={
        "username": "nobody@example.com",
        "password": "anypassword",
    })
    assert response.status_code == 401
