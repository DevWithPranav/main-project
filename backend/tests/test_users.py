"""Users admin API: ADMIN-only CRUD; role change / disable / password reset revoke tokens; the last active ADMIN stays."""

import asyncio
import os
import time

import asyncpg

PW = "s3cret-pass"


def login(client, user, pw=PW):
    return client.post("/api/auth/login", json={"username": user, "password": pw})


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def new_user(client, auth, role="OFFICER", prefix="u"):
    name = f"{prefix}{time.time_ns()}"
    r = client.post("/api/users", headers=auth("admin"), json={"username": name, "password": PW, "role": role})
    assert r.status_code == 201, r.text
    assert r.json() == {"id": r.json()["id"], "username": name, "role": role, "disabled": False}
    return name


def test_admin_only(client, auth):
    for user in ("officer", "operator", "planner", "maintenance"):
        assert client.get("/api/users", headers=auth(user)).status_code == 403, user
        assert client.post("/api/users", headers=auth(user),
                           json={"username": "x_nope", "password": PW, "role": "ADMIN"}).status_code == 403
        assert client.patch("/api/users/officer", headers=auth(user), json={"role": "ADMIN"}).status_code == 403
    assert client.get("/api/users").status_code == 401
    users = client.get("/api/users", headers=auth("admin")).json()
    assert {"officer", "operator", "planner", "maintenance", "admin"} <= {u["username"] for u in users}
    assert all("password_hash" not in u for u in users)


def test_create_and_validation(client, auth):
    name = new_user(client, auth, "PLANNER")
    r = login(client, name)
    assert r.status_code == 200 and r.json()["role"] == "PLANNER"
    h = auth("admin")
    assert client.post("/api/users", headers=h, json={"username": name, "password": PW, "role": "OFFICER"}).status_code == 409
    assert client.post("/api/users", headers=h, json={"username": "x_bad1", "password": PW, "role": "BOSS"}).status_code == 422
    assert client.post("/api/users", headers=h, json={"username": "x_bad2", "password": "short", "role": "OFFICER"}).status_code == 422
    assert client.post("/api/users", headers=h, json={"username": "a b", "password": PW, "role": "OFFICER"}).status_code == 422
    assert client.patch("/api/users/nobody_here", headers=h, json={"role": "OFFICER"}).status_code == 404
    assert client.patch(f"/api/users/{name}", headers=h, json={"role": "BOSS"}).status_code == 422
    # stored with bcrypt like the seeded users
    async def hash_of():
        c = await asyncpg.connect(f"postgresql://aerial:aerial_dev@localhost:5432/{os.getenv('TEST_DB_NAME', 'aerial_test')}")
        try:
            return await c.fetchval("SELECT password_hash FROM users WHERE username=$1", name)
        finally:
            await c.close()
    assert asyncio.run(hash_of()).startswith("$2")


def test_role_change_revokes_tokens(client, auth):
    name = new_user(client, auth, "OFFICER")
    old = login(client, name).json()
    r = client.patch(f"/api/users/{name}", headers=auth("admin"), json={"role": "MAINTENANCE"})
    assert r.status_code == 200 and r.json()["role"] == "MAINTENANCE"
    assert client.get("/api/me", headers=bearer(old["access_token"])).status_code == 401  # carries the old role
    assert client.post("/api/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code == 401
    new = login(client, name).json()
    assert new["role"] == "MAINTENANCE"
    assert client.get("/api/me", headers=bearer(new["access_token"])).json()["role"] == "MAINTENANCE"


def test_disable_and_enable(client, auth):
    name = new_user(client, auth)
    tok = login(client, name).json()
    r = client.patch(f"/api/users/{name}", headers=auth("admin"), json={"disabled": True})
    assert r.status_code == 200 and r.json()["disabled"] is True
    assert login(client, name).status_code == 403
    assert client.get("/api/me", headers=bearer(tok["access_token"])).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": tok["refresh_token"]}).status_code == 401
    assert client.patch(f"/api/users/{name}", headers=auth("admin"), json={"disabled": False}).json()["disabled"] is False
    again = login(client, name)
    assert again.status_code == 200
    assert client.get("/api/me", headers=bearer(again.json()["access_token"])).status_code == 200


def test_password_reset(client, auth):
    name = new_user(client, auth)
    tok = login(client, name).json()
    assert client.post(f"/api/users/{name}/password", headers=auth("officer"), json={"password": "another-pass"}).status_code == 403
    assert client.post(f"/api/users/{name}/password", headers=auth("admin"), json={"password": "short"}).status_code == 422
    assert client.post(f"/api/users/{name}/password", headers=auth("admin"), json={"password": "another-pass"}).status_code == 200
    assert login(client, name).status_code == 401
    assert login(client, name, "another-pass").status_code == 200
    assert client.get("/api/me", headers=bearer(tok["access_token"])).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": tok["refresh_token"]}).status_code == 401


def test_last_active_admin_kept(client, auth):
    h = auth("admin")
    second = new_user(client, auth, "ADMIN", "adm")
    # with two active admins one may go
    assert client.patch(f"/api/users/{second}", headers=h, json={"role": "PLANNER"}).status_code == 200
    # now `admin` is the only one: no demotion, no disabling (a disabled admin does not count)
    third = new_user(client, auth, "ADMIN", "adm")
    assert client.patch(f"/api/users/{third}", headers=h, json={"disabled": True}).status_code == 200
    r = client.patch("/api/users/admin", headers=h, json={"role": "OFFICER"})
    assert r.status_code == 409 and "last active ADMIN" in r.json()["detail"]
    assert client.patch("/api/users/admin", headers=h, json={"disabled": True}).status_code == 409
    # a no-op patch of the last admin is fine, and the admin still works
    assert client.patch("/api/users/admin", headers=h, json={"role": "ADMIN", "disabled": False}).status_code == 200
    assert client.get("/api/me", headers=h).json()["role"] == "ADMIN"
    acts = {i["action"] for i in client.get("/api/audit", headers=h, params={"action": "user.*"}).json()["items"]}
    assert {"user.create", "user.update"} <= acts
