"""PRD 28.4 audit log: actions are logged with user, role, action, resource, IP, result; the log is append-only."""

import asyncio
import os

import asyncpg
import pytest

FLIGHT = os.getenv("TEST_FLIGHT", "20261009_201727")
TEST_DB = "postgresql://aerial:aerial_dev@localhost:5432/aerial_test"


def test_actions_are_logged(client, auth, imported):
    eid = client.get("/api/events", headers=auth("officer"), params={"session_id": FLIGHT}).json()["items"][0]["event_id"]
    client.get(f"/api/events/{eid}", headers=auth("officer"))
    client.get("/api/export", headers=auth("officer"), params={"format": "csv", "session_id": FLIGHT})
    client.post(f"/api/events/{eid}/review", headers=auth("operator"), json={"outcome": "confirmed"})  # 403: not a reviewer
    assert client.get("/api/audit", headers=auth("officer")).status_code == 403
    items = client.get("/api/audit", headers=auth("admin"), params={"resource_id": eid}).json()["items"]
    got = {(i["action"], i["user_id"], i["result"]) for i in items}
    assert ("event.read", "officer", 200) in got
    assert ("event.review", "operator", 403) in got  # refused attempts are logged too
    exp = client.get("/api/audit", headers=auth("admin"), params={"action": "export.*"}).json()["items"]
    assert any(i["resource_id"] == FLIGHT and i["role"] == "OFFICER" and i["ip_address"] for i in exp)
    assert not client.get("/api/audit", headers=auth("admin"), params={"action": "get health"}).json()["items"]


def test_audit_log_is_append_only(client, auth):
    client.get("/api/events", headers=auth("officer"), params={"limit": 1})

    async def tamper(sql):
        c = await asyncpg.connect(TEST_DB)
        try:
            await c.execute(sql)
        finally:
            await c.close()
    for sql in ("UPDATE audit_log SET user_id = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            asyncio.run(tamper(sql))
