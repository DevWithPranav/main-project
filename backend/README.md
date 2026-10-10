# backend/

FastAPI service (Build Plan **M5**, PRD Sections 13–14, 28). The contract is [API.md](API.md).

```powershell
docker compose up -d                                   # PostGIS :5432, Redis :6379, SeaweedFS S3 :9000
venv\Scripts\python.exe -m pip install -r backend/requirements.txt
venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8000
# docs: http://localhost:8000/docs
venv\Scripts\python.exe -m pytest backend/tests -v -s   # uses database aerial_test, not aerial
```

Import a processed flight (operator / admin token):
`POST /api/sessions/import {"flight": "20261009_201727", "violations_dir": "violations"}`.

| File | What |
|---|---|
| `app/main.py` | app, CORS, lifespan (tables, dev users, seed profiles, S3 bucket, Redis listener) |
| `app/config.py` | env settings with dev defaults (`DATABASE_URL`, `REDIS_URL`, `S3_*`, `JWT_SECRET`, `SERVICE_TOKEN`) |
| `app/models.py`, `app/db.py` | SQLAlchemy 2 async + GeoAlchemy2 tables (Point, SRID 0 = map metres); `create_all` on startup, Alembic later |
| `app/auth.py` | JWT HS256 (12 h), bcrypt, role checks, service token |
| `app/events.py` | event rows <-> API objects, shared filters |
| `app/ingest.py` | flight import: validation (`schemas.event_errors`), evidence upload, trajectories at ≤ 5 Hz |
| `app/live.py` | Redis pub/sub -> WebSockets (events at once, vehicles ≤ 10 Hz) |
| `app/exports.py` | CSV, GeoJSON, XLSX (openpyxl), PDF (ReportLab) |
| `app/storage.py` | S3 (boto3, path-style) evidence store |
| `app/routers/` | `core` (login, me, health, files), `sessions`, `events` (+ review, stats, export), `configs` (profiles, conditions, scenes), `planning`, `live` |
