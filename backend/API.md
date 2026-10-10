# Backend API contract (Build Plan M5)

The contract the backend (`backend/`), the dashboard (`frontend/`), the twin (`twin/`) and the live
pipeline (`services/live/`) all build against. Change it only together with every user of it.

- Base URL: `http://localhost:8000`, all routes under `/api`. JSON everywhere; times ISO-8601 UTC.
- CORS open to `http://localhost:5173` (dashboard) and `http://localhost:5174` (twin).
- Auth: `POST /api/auth/login` → `{access_token, role}` (JWT, HS256, 12 h). Send
  `Authorization: Bearer <token>`. Roles: `OFFICER`, `OPERATOR`, `PLANNER`, `MAINTENANCE`, `ADMIN`.
  Dev users are seeded on startup: `officer`, `operator`, `planner`, `maintenance`, `admin`, each with
  the password `<name>123`. Reads need any role; writes as noted.
- Errors: `{"detail": "..."}` with a 4xx code.

## Data

**Event**: exactly `schemas/event.schema.json` (violation or anomaly), plus `session_id` and
`review`. Evidence paths become URLs: `evidence.clip_url`, `evidence.snapshot_url` (served by
`GET /api/files/{key}` from the S3 store, or a presigned URL).

**Session**: `{session_id, name, source ("carla"|"video"|"live"), town, flight, started_at,
n_events, profile, scene}`. A recorded CARLA flight imports as one session; `session_id` = flight
folder name (e.g. `20261010_120643`).

**Vehicle state** (live): `{session_id, track_id, cls, x, y, speed_kmh, heading_deg, lane_id,
state ("ok"|"checking"|"flagged"), t_s}`.

## Routes

| Method & path | Role | What |
|---|---|---|
| `POST /api/auth/login` | none | `{username, password}` → token |
| `GET /api/me` | any | `{username, role}` |
| `GET /api/health` | none | `{db, redis, s3}` each `"ok"` or an error string |
| `GET /api/sessions` | any | list of sessions |
| `POST /api/sessions/import` | OPERATOR, ADMIN | `{flight, violations_dir?, scene?, profile?}`: loads `violations.json` (+ clips, trajectories) of a processed flight; returns the session |
| `GET /api/sessions/{id}` | any | one session |
| `GET /api/sessions/{id}/trajectories` | any | `{track_id: [[t_s, x, y, speed_kmh], ...]}` (downsampled ≤ 5 Hz) |
| `GET /api/events` | any | filters: `session_id, type, condition, status, review (none/confirmed/dismissed), kind, since, until, bbox=x0,y0,x1,y1, limit (default 200), offset`; returns `{total, items}` |
| `GET /api/events/{id}` | any | one event |
| `POST /api/events` | OPERATOR, ADMIN, service token | create one event (live pipeline); broadcast on the WebSocket |
| `POST /api/events/{id}/review` | OFFICER, ADMIN (anomalies: MAINTENANCE) | `{outcome: confirmed|dismissed, note?}` |
| `GET /api/stats` | any | `{by_type, by_condition, by_status, by_hour, hotspots}`; same filters as `/events` |
| `GET /api/export?format=pdf|xlsx|geojson|csv` | any | same filters as `/events`; file download (target ≤ 10 s) |
| `GET /api/profiles` · `GET /api/profiles/{name}` | any | configuration profiles (`schemas/profile.schema.json`; seeded from `ml/violation_engine/configs/profiles/`) |
| `PUT /api/profiles/{name}` | ADMIN, PLANNER | validate against the schema, store a new version (who, when, why in `{profile, note}`) |
| `GET /api/profiles/{name}/history` | any | versions with who/when/note |
| `GET /api/conditions` | any | `schemas/conditions.json` |
| `GET /api/scenes` · `GET /api/scenes/{town}` | any | lane maps (`ml/violation_engine/configs/scenes/*.json`) |
| `GET /api/recommendations` | any | from `ml/planning` (M8): list of recommendation objects |
| `POST /api/recommendations/{id}/decision` | PLANNER, ADMIN | `{decision: accepted|rejected|modified, rationale}` → planner history |
| `GET /api/planner/history` | any | decisions + validation results |
| `POST /api/live/state` | service token | list of vehicle states → Redis (TTL 2 s) + WebSocket |
| `WS /api/ws/live?session_id=` | token as `?token=` | server pushes `{"type":"vehicles","items":[...]}` (≤ 10 Hz) and `{"type":"event","event":{...}}` |

Service token for the live pipeline: env `SERVICE_TOKEN` (default `dev-service-token`), sent as
`Authorization: Bearer <SERVICE_TOKEN>`.

## Where things live

- PostGIS (`postgresql+asyncpg://aerial:aerial_dev@localhost:5432/aerial`): tables `sessions`,
  `events` (geometry Point, SRID 0: map metres), `reviews`, `profiles` (versioned), `recommendations`,
  `planner_history`, `users`.
- Redis `localhost:6379`: `live:{session}:{track}` keys, channel `live:{session}` and `events`.
- S3 `http://localhost:9000` (SeaweedFS, path-style, key `aerial` / `aerial_dev_secret`), bucket
  `evidence`.

Run: `docker compose up -d`, then `venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8000`.

## Details fixed by the implementation (2026-10-10, additions only)

- **Event** objects also carry `occurred_at` (ISO UTC: session start + event time; this is what
  `since` / `until` / `by_hour` use). Validate an event against the schema without it. Evidence keeps
  the engine's `clip` / `snapshot` paths and gains `clip_key` / `clip_url` (and `snapshot_*`).
- **`GET /api/files/{key}`**: no token needed (so `<video src>` works); supports `Range` (206).
- **`/api/events` filters** `type`, `condition`, `status`, `kind`, `review`, `session_id` take
  comma lists (`status=flagged,needs_review`). `limit` ≤ 5000. Order: `occurred_at`, then `event_id`.
  `GET /api/events/{id}/reviews` lists every review of an event (`{outcome, note, by, at}`).
- **`POST /api/events`**: body is the event incl. `session_id` (required); an unknown session is
  created with `source: "live"`. `condition` is filled in by the engine's rules if left out.
  422 if it fails the schema, 409 if the `event_id` exists. Returns 201 + the event.
- **Review** roles: violations OFFICER / ADMIN; anomalies MAINTENANCE / ADMIN. Returns the event.
- **`POST /api/sessions/import`** returns the session plus `import: {violations_dir, n_in_file,
  n_imported, n_rejected, errors, clips, clips_uploaded, n_tracks, imported_by, imported_at}`.
  `violations_dir` is a folder name inside `<flight>/tracktrack_ours/` (default: the newest
  `violations*` with a `violations.json`). Re-importing replaces the session's events and tracks
  but keeps reviews of events that are still there. `GET /api/sessions/{id}` also has `import`.
- **Trajectories**: `[t_s, x, y, speed_kmh]` with `t_s` in the engine's time base (sim seconds).
- **`GET /api/stats`** also returns `total` and `by_review`; `by_hour` has keys `"00"`..`"23"` (UTC);
  `hotspots` are the top 10 cells of a 25 m grid: `{x, y, cell_m, count, by_type, lane_ids}`.
- **Exports** stop at 10 000 events (PDF 6.97 s, XLSX 3.30 s for 10 000 on the dev laptop).
  Headers: `X-Event-Count` (rows written), `X-Event-Total` (rows matching), `X-Export-Seconds`.
  GeoJSON coordinates are map metres, not WGS84 (`crs_note` says so).
- **Profiles**: `GET /api/profiles` returns the latest profile documents; `GET /api/profiles/{name}`
  the latest document itself. `PUT` body `{profile, note}` (the profile's `name` must match the
  URL; checked with `profiles.profile_errors`, so unknown thresholds are 422) returns
  `{name, version, by, at, note}`. History: newest first, `{name, version, by, at, note, profile}`.
- **Scenes**: `GET /api/scenes` returns summaries `{town, scene, coords, source, n_lanes, n_zones}`;
  `GET /api/scenes/{town}` (case-insensitive) the full lane map.
- **Recommendations**: `GET /api/recommendations?session_id=` calls
  `ml.planning.recommend.recommend(events, scene)` (scene = the session's lane map); `[]` until that
  module exists. Each item gets an `id` if it has none; decisions are stored with a copy of it.
  History items: `{id, recommendation_id, decision, rationale, by, at, recommendation, validation}`.
- **Live**: `POST /api/live/state` returns `{received}`. A new WebSocket first gets the vehicles
  still in Redis (TTL 2 s). Without `session_id` a socket gets every session. Bad token: closed
  with code 1008.
