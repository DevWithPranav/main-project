# Backend API contract (Build Plan M5)

The contract the backend (`backend/`), the dashboard (`frontend/`), the twin (`twin/`) and the live
pipeline (`services/live/`) all build against. Change it only together with every user of it.

- Base URL: `http://localhost:8000`, all routes under `/api`. JSON everywhere; times ISO-8601 UTC.
- CORS open to `http://localhost:5173` (dashboard) and `http://localhost:5174` (twin).
- Auth: `POST /api/auth/login` → `{access_token, refresh_token, token_type, role, expires_in}`: a
  short-lived access JWT (HS256, 15 min, env `ACCESS_TOKEN_MINUTES`) and a single-use refresh token
  (12 h, env `REFRESH_TOKEN_HOURS`). Send `Authorization: Bearer <access_token>`; renew with
  `POST /api/auth/refresh` before `expires_in` runs out or on a 401. Roles: `OFFICER`, `OPERATOR`, `PLANNER`, `MAINTENANCE`, `ADMIN`.
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
| `POST /api/auth/login` | none | `{username, password}` → tokens; 401 wrong password, 403 account disabled |
| `POST /api/auth/refresh` | none | `{refresh_token}` → a new `{access_token, refresh_token, ...}` (the old refresh token is spent); 401 if spent, expired, revoked or the user is disabled |
| `POST /api/auth/logout` | bearer optional | body `{refresh_token?}` → 204; revokes the bearer access token and the refresh token |
| `GET /api/users` | ADMIN | `[{id, username, role, disabled}]` |
| `POST /api/users` | ADMIN | `{username, password (≥ 8), role}` → 201 user; 409 if the name exists |
| `PATCH /api/users/{username}` | ADMIN | `{role?, disabled?}` change role / disable / enable; 409 for the last active ADMIN |
| `POST /api/users/{username}/password` | ADMIN | `{password}` reset; the user's current tokens stop working |
| `GET /api/me` | any | `{username, role}` |
| `GET /api/health` | none | `{db, redis, s3}` each `"ok"` or an error string; `clips` = background WebM transcodes `{queued, done, skipped, failed, pending}` |
| `GET /api/sessions` | any | list of sessions |
| `POST /api/sessions/import` | OPERATOR, ADMIN | `{flight, violations_dir?, scene?, profile?}`: loads `violations.json` (+ clips, trajectories) of a processed flight; returns the session |
| `GET /api/sessions/{id}` | any | one session |
| `GET /api/sessions/{id}/trajectories` | any | `{track_id: [[t_s, x, y, speed_kmh], ...]}` (downsampled ≤ 5 Hz) |
| `GET /api/events` | any | filters: `session_id, type, condition, status, review (none/confirmed/dismissed), kind, since, until, bbox=x0,y0,x1,y1, limit (default 200), offset`; returns `{total, items}` |
| `GET /api/events/{id}` | any | one event |
| `POST /api/events` | OPERATOR, ADMIN, service token | create one event (live pipeline); broadcast on the WebSocket |
| `POST /api/events/{id}/review` | OFFICER, ADMIN (anomalies: MAINTENANCE) | `{outcome: confirmed|dismissed, note?}` |
| `GET /api/stats` | any | `{by_type, by_condition, by_status, by_hour, hotspots}`; same filters as `/events` |
| `GET /api/export?format=pdf|xlsx|geojson|csv` | any | same filters as `/events`; file download (target ≤ 10 s); also recorded in the report history |
| `POST /api/reports/generate?format=…` | any | same query as `/export`; makes and records the report, returns its record (201) instead of the file |
| `GET /api/reports` | any | report history, newest first: `{total, items}`; filters `format, by, session_id, limit (≤ 500, default 50), offset` |
| `GET /api/reports/{id}` | any | download a recorded report again (from S3) |
| `GET /api/profiles` · `GET /api/profiles/{name}` | any | configuration profiles (`schemas/profile.schema.json`; seeded from `ml/violation_engine/configs/profiles/`) |
| `PUT /api/profiles/{name}` | ADMIN, PLANNER | validate against the schema, store a new version (who, when, why in `{profile, note}`) |
| `GET /api/profiles/{name}/history` | any | versions with who/when/note |
| `GET /api/conditions` | any | `schemas/conditions.json` |
| `GET /api/scenes` · `GET /api/scenes/{town}?profile=` | any | lane maps (`ml/violation_engine/configs/scenes/*.json`); with `profile`, its `road.lane_overrides` applied |
| `GET /api/road/attributes` | any | the lane attributes `road.lane_overrides` may set, with type, allowed values and the conditions each drives |
| `GET /api/zones?scene=&type=&active=true|false|all&include_static=&format=geojson|engine` | any | zones as a GeoJSON FeatureCollection; `format=engine` (needs `scene`): the engine's `--zones` document |
| `POST /api/zones` | PLANNER, ADMIN | `{scene, name, type, polygon | geometry, grace_s?, limit_kmh?, note?}` → 201 the zone (Feature) + `file` |
| `GET /api/zones/{id}` · `GET /api/zones/{id}/history` | any | one zone · its versions, newest first |
| `PUT /api/zones/{id}` | PLANNER, ADMIN | change geometry / metadata (fields sent only) → a new version |
| `DELETE /api/zones/{id}` | ADMIN | deactivate (kept with its history; `PUT {active: true}` by ADMIN reactivates) |
| `GET /api/recommendations` | any | from `ml/planning` (M8): list of recommendation objects |
| `POST /api/recommendations/{id}/decision` | PLANNER, ADMIN | `{decision: accepted|rejected|modified, rationale}` → planner history |
| `GET /api/planner/history` | any | decisions + validation results |
| `GET /api/scenarios/countermeasures` | any | the M8 catalogue: key, name, violation types it addresses, whether a sourced factor exists |
| `POST /api/scenarios` | PLANNER, ADMIN | what-if (PRD 21.3): `{session_id, name, changes: {lane_overrides, zones}, countermeasure?, area?}` → 202 `{id, status: running}`; runs `ml/planning/whatif.py` in the background |
| `GET /api/scenarios?session_id=` · `GET /api/scenarios/{id}` | any | saved scenarios (summary) · one with `result.replay` (rule replay on the recorded traffic, measured) and `result.projection` (countermeasure × sourced factor, projected estimate) |
| `POST /api/live/state` | service token | list of vehicle states → Redis (TTL 2 s) + WebSocket |
| `WS /api/ws/live?session_id=` | token as `?token=` | server pushes `{"type":"vehicles","items":[...]}` (≤ 10 Hz) and `{"type":"event","event":{...}}` |
| `POST /api/live/frame?session_id=&kind=raw|annotated&t_s=` | service token | body: one JPEG (≤ 2 MB); only the newest frame per session and kind is kept, in memory → 204 |
| `GET /api/live/video?session_id=&kind=raw|annotated` | token (header or `?token=`) | MJPEG (`multipart/x-mixed-replace`) of those frames, for an `<img>`; the newest frame first |
| `GET /api/live/sources` | any | sessions that sent a frame in the last 10 s: `[{session_id, kinds, t_s, age_s}]`, newest first |
| `GET /api/sessions/{id}/video?kind=annotated|raw` | any | browser video (VP8 WebM) of the model output or of the footage the model was given: `{status: none|encoding|ready|failed, url, fps, frame_t, source, kind}`; `none` adds `available` |
| `POST /api/sessions/{id}/video?kind=annotated|raw` | OPERATOR, ADMIN | start making it in the background (raw: a flight's `flight.mp4`, a real clip's source video) |

Service token for the live pipeline: env `SERVICE_TOKEN` (default `dev-service-token`), sent as
`Authorization: Bearer <SERVICE_TOKEN>`.

## Where things live

- PostGIS (`postgresql+asyncpg://aerial:aerial_dev@localhost:5432/aerial`): tables `sessions`,
  `events` (geometry Point, SRID 0: map metres), `reviews`, `profiles` (versioned), `recommendations`,
  `planner_history`, `users`, `zones` (geometry Polygon, SRID 0) + `zone_versions`, `reports`.
- Redis `localhost:6379`: `live:{session}:{track}` keys, channel `live:{session}` and `events`;
  auth: `auth:refresh:{sha256(token)}` (refresh tokens), `auth:deny:{jti}` (logged-out access tokens,
  TTL = their remaining life), `auth:cutoff:{username}` (tokens issued before it are refused: set when
  a user is disabled, changes role or gets a new password).
- S3 `http://localhost:9000` (SeaweedFS, path-style, key `aerial` / `aerial_dev_secret`), bucket
  `evidence`.

Run: `docker compose up -d`, then `venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8000`.

## Details fixed by the implementation (2026-10-10, additions only)

- **Event** objects also carry `occurred_at` (ISO UTC: session start + event time; this is what
  `since` / `until` / `by_hour` use). Validate an event against the schema without it. Evidence keeps
  the engine's `clip` / `snapshot` paths and gains `clip_key` / `clip_url` (and `snapshot_*`).
- **`GET /api/files/{key}`** (PRD 28.5): the URLs the API hands out (`evidence.*_url`, session
  video `url`, anomaly `snapshot_url`) are signed, `?exp=<unix s>&sig=<HMAC-SHA256>`, and work without
  a token until `exp` (60-65 min: rounded up to 5 min so the URL is stable; env `FILE_URL_MINUTES`;
  key env `FILE_URL_SECRET`, default derived from `JWT_SECRET`). An expired or altered link: 403.
  Without a valid link a token is needed (bearer header or `?token=`), else 401. Supports `Range` (206). Evidence clips are uploaded as mp4 at import and transcoded to WebM in the background; a `.mp4` key is served as its `.webm` once that exists.
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
- **Reports** (PRD 14.1 / 13.1 `reports`): every `GET /api/export` and `POST /api/reports/generate` stores the
  file in S3 under `reports/YYYY/MM/{id}_{filename}` and a `reports` row. Record: `{id, format, filename,
  content_type, filters, session_id, n_events, n_total, size_bytes, seconds, by, role, at, stored, download_url}`.
  `/export` returns its id in the `X-Report-Id` header (exposed to CORS); an S3 failure never fails the export
  (`stored: false`, download 410). Audit actions: `export.<format>`, `report.generate.<format>`, `reports.list`,
  `report.download`.
- **Zones** (PRD 14.1, 18.2): one scene (`Town03`, `Town05`, … lane maps) or site (`configs/sites/*.json`) each,
  case-insensitive; coordinates are that map's metres (SRID 0, like event `x, y`). Types are the engine's
  (`lane_map.py`): `no_parking`, `crosswalk`, `highway` (stopping rules; `grace_s` 0-3600 overrides the rule's
  wait), `speed` (`limit_kmh` 1-200, required), `no_u_turn`; a no-stopping zone is `no_parking` with a short
  `grace_s`. Geometry: a simple polygon (no self-intersection, no holes; open or closed ring), 3-500 vertices,
  1 m² to 1 km², span ≤ 2 km; else 422. A zone Feature's `properties`: `{id, scene, name, type, grace_s?,
  limit_kmh?, active, version, area_m2, created_by, created_at, updated_by, updated_at, source: "api"}`;
  `include_static=true` adds the scene / site file's own zones (`source: "scene_file"`, `editable: false`).
  Every create / update / deactivate bumps `version` and appends a `zone_versions` row (history items:
  `{version, action, by, at, note, zone}`); changing `type` resets `grace_s` / `limit_kmh`; `grace_s: null` clears it.
  **To the engine:** each change rewrites `backend/data/zones/<scene>.json` (env `ZONE_OUT_DIR`; returned as
  `file`) with the scene's active zones as `{"zones": [{id, type, polygon, grace_s?, limit_kmh?, name, source,
  version}]}`, the `--zones` shape: run `run_violations.py … --zones backend/data/zones/Town05.json`, or save a
  profile with `road.zones: "backend/data/zones/Town05.json"` (run_violations.py and the live pipeline then
  load it; a `--zones` given on the command line replaces it). Events in an API zone carry its `id` as `zone_id`.
  Audit actions: `zones.list`, `zone.read`, `zone.create`, `zone.update`, `zone.deactivate`, `zone.history`.
- **Profiles**: `GET /api/profiles` returns the latest profile documents; `GET /api/profiles/{name}`
  the latest document itself. `PUT` body `{profile, note}` (the profile's `name` must match the
  URL; checked with `profiles.profile_errors`, so unknown thresholds are 422) returns
  `{name, version, by, at, note}`. History: newest first, `{name, version, by, at, note, profile}`.
- **Scenes**: `GET /api/scenes` returns summaries `{town, scene, coords, source, n_lanes, n_zones}`;
  `GET /api/scenes/{town}` (case-insensitive) the full lane map. `?profile=<name>`: the latest version of that
  profile's `road.lane_overrides` applied with the engine's own `road_features.apply_overrides` (what
  `run_violations.py --profile` runs on); each lane whose values changed gets `"overridden": [keys]`, and the
  map adds `"profile": name, "overrides_applied": <lanes changed>`. 404 unknown profile; 422 (the engine's
  message) if an override's `lane_id` matches no lane of this map.
- **Lane overrides** (`schemas/profile.schema.json` `road.lane_overrides`): items `{lane_id, speed_limit_kmh?,
  restricted?, road_class?, lane_type?, lane_change?, one_way?, bridge?, tunnel?, ramp?, median_left?, note?}`;
  `lane_id` an exact id or fnmatch glob (`r46_s0_l2`, `r46_*`); later items win; `null` clears `restricted` /
  `ramp`; `note` is kept but not read. `PUT /api/profiles/{name}` validates them against the schema; whether an
  override matches a lane is only checked when the map is applied (`/scenes/{town}?profile=`, the engine run).
  `GET /api/road/attributes` → `[{key, label, type: "enum"|"bool"|"number", values? (null = clear), min_exclusive?,
  how, drives: [{condition, label}]}]` (`road_features.ROAD_ATTRIBUTES`; labels from `conditions.json`).
- **Recommendations**: `GET /api/recommendations?session_id=` calls
  `ml.planning.recommend.recommend(events, scene)` (scene = the session's lane map); `[]` until that
  module exists. Each item gets an `id` if it has none; decisions are stored with a copy of it.
  History items: `{id, recommendation_id, decision, rationale, by, at, recommendation, validation}`.
- **Live**: `POST /api/live/state` returns `{received}`. A new WebSocket first gets the vehicles
  still in Redis (TTL 2 s). Without `session_id` a socket gets every session. Bad token: closed
  with code 1008.
