"""PRD 14.1 / 28.5: short-lived access tokens, refresh (rotated), logout revocation, signed expiring evidence links."""

import time

import pytest
from websockets.sync.client import connect

KEY = "test/auth_evidence.txt"


def login(client, user="officer"):
    r = client.post("/api/auth/login", json={"username": user, "password": f"{user}123"})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def test_login_gives_short_access_and_refresh(client):
    from backend.app import config
    r = login(client)
    assert r["refresh_token"] and r["token_type"] == "bearer" and r["role"] == "OFFICER"
    assert r["expires_in"] == config.ACCESS_MINUTES * 60 <= 3600
    assert client.get("/api/me", headers=bearer(r["access_token"])).json()["username"] == "officer"


def test_refresh_rotates(client):
    r = login(client)
    n = client.post("/api/auth/refresh", json={"refresh_token": r["refresh_token"]})
    assert n.status_code == 200, n.text
    n = n.json()
    assert n["access_token"] != r["access_token"] and n["refresh_token"] != r["refresh_token"] and n["role"] == "OFFICER"
    assert client.get("/api/me", headers=bearer(n["access_token"])).status_code == 200
    # a refresh token is single-use; junk is refused
    assert client.post("/api/auth/refresh", json={"refresh_token": r["refresh_token"]}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": "junk"}).status_code == 401


def test_logout_revokes_both_tokens(client, server):
    r = login(client)
    h = bearer(r["access_token"])
    assert client.get("/api/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h, json={"refresh_token": r["refresh_token"]}).status_code == 204
    assert client.get("/api/me", headers=h).status_code == 401
    assert client.get("/api/sessions", headers=h).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": r["refresh_token"]}).status_code == 401
    with pytest.raises(Exception):  # the live socket refuses it too
        with connect(f"{server.replace('http', 'ws', 1)}/api/ws/live?token={r['access_token']}") as ws:
            ws.recv(timeout=2)
    # logging out again, or with nothing, is harmless; other logins are untouched
    assert client.post("/api/auth/logout", headers=h).status_code == 204
    assert client.post("/api/auth/logout").status_code == 204
    other = login(client)
    assert client.get("/api/me", headers=bearer(other["access_token"])).status_code == 200


def test_expired_and_old_style_tokens_refused(client):
    import jwt

    from backend.app import config
    from backend.app.auth import make_token
    assert client.get("/api/me", headers=bearer(make_token("officer", "OFFICER", minutes=-1))).status_code == 401
    old = jwt.encode({"sub": "officer", "role": "OFFICER", "exp": int(time.time()) + 600}, config.JWT_SECRET, algorithm="HS256")
    assert client.get("/api/me", headers=bearer(old)).status_code == 401  # pre-refresh tokens had no jti
    assert client.get("/api/me", headers=bearer(make_token("officer", "OFFICER"))).status_code == 200


@pytest.fixture
def evidence():
    from backend.app import config, storage
    storage.client().put_object(Bucket=config.S3_BUCKET, Key=KEY, Body=b"evidence", ContentType="text/plain")
    yield storage
    storage.client().delete_object(Bucket=config.S3_BUCKET, Key=KEY)


def test_signed_evidence_links(client, auth, evidence):
    url = evidence.file_url(KEY)
    assert "exp=" in url and "sig=" in url
    assert client.get(url).content == b"evidence"  # no token needed while the link is valid
    assert evidence.file_url(KEY) == url  # stable for a while (exp rounded up), so <video src> does not reload
    exp = int(url.split("exp=")[1].split("&")[0])
    assert 3600 <= exp - time.time() <= 3600 + 300
    assert client.get(url.replace("sig=", "sig=x")).status_code == 403  # tampered
    assert client.get(url.replace(KEY, "test/other.txt")).status_code == 403  # the signature is per file
    assert client.get(url.replace(f"exp={exp}", f"exp={exp + 300}")).status_code == 403  # extended
    assert client.get(evidence.file_url(KEY, minutes=-10)).status_code == 403  # expired
    assert client.get(f"/api/files/{KEY}").status_code == 401  # no link, no token
    # a token still works: bearer header or ?token= (revoked tokens do not)
    assert client.get(f"/api/files/{KEY}", headers=auth("officer")).status_code == 200
    r = login(client, "maintenance")
    assert client.get(f"/api/files/{KEY}", params={"token": r["access_token"]}).status_code == 200
    client.post("/api/auth/logout", headers=bearer(r["access_token"]))
    assert client.get(f"/api/files/{KEY}", params={"token": r["access_token"]}).status_code == 401


def test_event_evidence_urls_are_signed(client, auth, imported):
    items = client.get("/api/events", headers=auth("officer"), params={"session_id": imported["session_id"]}).json()["items"]
    urls = [e["evidence"][k] for e in items for k in ("clip_url", "snapshot_url") if e["evidence"].get(k)]
    assert urls and all("sig=" in u and "exp=" in u for u in urls)


def test_token_actions_audited(client, auth):
    r = login(client, "planner")
    n = client.post("/api/auth/refresh", json={"refresh_token": r["refresh_token"]}).json()
    client.post("/api/auth/logout", headers=bearer(n["access_token"]), json={"refresh_token": n["refresh_token"]})
    want = {("auth.refresh", None), ("auth.logout", "planner")}
    for _ in range(20):  # the middleware writes the row just after the response goes out
        acts = {(i["action"], i["user_id"]) for i in client.get("/api/audit", headers=auth("admin"),
                                                                 params={"action": "auth.*"}).json()["items"]}
        if want <= acts:
            break
        time.sleep(0.1)
    assert want <= acts
