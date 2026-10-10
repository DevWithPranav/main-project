r"""Test fixtures: a real uvicorn server on :8011 against the docker services, with its own
database `aerial_test` (created if missing, tables dropped at the start) so dev data is untouched.

Usage:
    docker compose up -d
    venv\Scripts\python.exe -m pytest backend/tests -v
"""

import asyncio
import os
import threading
import time

import httpx
import pytest

PORT = 8011
TEST_DB = "aerial_test"
os.environ["DATABASE_URL"] = f"postgresql+asyncpg://aerial:aerial_dev@localhost:5432/{TEST_DB}"
BASE = f"http://127.0.0.1:{PORT}"
TEST_FLIGHT = os.getenv("TEST_FLIGHT", "20261009_201727")  # a processed flight with violations/ + clips
TEST_VIOLATIONS_DIR = os.getenv("TEST_VIOLATIONS_DIR", "violations")  # its folder with >= 3 events and clips


async def _reset_db() -> None:
    import asyncpg
    conn = await asyncpg.connect("postgresql://aerial:aerial_dev@localhost:5432/postgres")
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", TEST_DB):
            await conn.execute(f"CREATE DATABASE {TEST_DB}")
    finally:
        await conn.close()
    conn = await asyncpg.connect(f"postgresql://aerial:aerial_dev@localhost:5432/{TEST_DB}")
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        for t in ("audit_log", "scenarios", "anomaly_status", "planner_history", "recommendations", "reviews", "profiles", "events", "tracks", "sessions", "users"):
            await conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def server():
    asyncio.run(_reset_db())
    import uvicorn
    from backend.app.main import app
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", ws="websockets-sansio"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "uvicorn did not start"
    yield BASE
    srv.should_exit = True
    th.join(timeout=10)


@pytest.fixture(scope="session")
def client(server):
    with httpx.Client(base_url=server, timeout=60) as c:
        yield c


def _token(client, user):
    r = client.post("/api/auth/login", json={"username": user, "password": f"{user}123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def auth(client):
    """auth('officer') -> headers with that dev user's bearer token."""
    cache = {}

    def get(user: str) -> dict:
        if user not in cache:
            cache[user] = {"Authorization": f"Bearer {_token(client, user)}"}
        return cache[user]
    return get


@pytest.fixture(scope="session")
def imported(client, auth):
    """Flight 20261009_201727 (env TEST_FLIGHT overrides) imported from its `violations` folder (env
    TEST_VIOLATIONS_DIR overrides; the one with evidence clips); anomalies left out so the violation tests see violations only."""
    r = client.post("/api/sessions/import", headers=auth("operator"),
                    json={"flight": TEST_FLIGHT, "violations_dir": TEST_VIOLATIONS_DIR, "include_anomalies": False})
    assert r.status_code == 200, r.text
    return r.json()
