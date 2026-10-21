"""Test configuration.

* Database: ``TEST_DATABASE_URL`` (e.g. PostgreSQL) or a temporary SQLite file.
* Queue: inline (tasks run synchronously inside the request).
* Tests that need the FreeCAD runtime are marked ``freecad`` and skipped when it is unavailable.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="cnc-tests-"))
os.environ.setdefault("CNC_ENV", "test")
os.environ["CNC_QUEUE_BACKEND"] = "inline"
os.environ["CNC_STORAGE_DIR"] = str(TMP / "storage")
os.environ["CNC_TEMP_DIR"] = str(TMP / "tmp")
os.environ["CNC_DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{TMP / 'test.db'}")
os.environ.setdefault("CNC_SIMULATION_MAX_CELLS", "150000")

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def engine():
    import app.models  # noqa: F401
    from app.db.session import Base, configure

    eng = configure(os.environ["CNC_DATABASE_URL"])
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    from app.db.session import SessionLocal
    from app.seed.seed import seed_all
    from app.seed.verify_posts import verify_bundled_postprocessors

    db = SessionLocal()
    seed_all(db)
    verify_bundled_postprocessors(db)
    db.commit()
    db.close()
    yield eng


@pytest.fixture(scope="session")
def client(engine):
    from fastapi.testclient import TestClient

    from app.core.ratelimit import limiter
    from app.main import create_app

    limiter.reset()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def db(engine):
    from app.db.session import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


def register(client, org: str | None = None, email: str | None = None) -> dict:
    from app.core.ratelimit import limiter

    limiter.reset()
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "correct horse battery",
                                                "full_name": "Test User", "organization_name": org or f"Org {uuid.uuid4().hex[:6]}"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=h).json()
    h["X-Organization-Id"] = me["memberships"][0]["organization_id"]
    return {"headers": h, "email": email, "me": me}


def freecad_available() -> bool:
    from app.services.cam.registry import get_engine

    return get_engine().available()[0]


requires_freecad = pytest.mark.skipif(not freecad_available(), reason="FreeCAD CAM runtime not available")
