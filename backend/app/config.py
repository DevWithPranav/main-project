"""Backend settings (Build Plan M5), from environment variables with dev defaults.

Defaults match docker-compose.yml and infra/s3/s3.json, so a plain `docker compose up -d` works.
"""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENGINE_DIR = REPO / "ml" / "violation_engine"
SCHEMA_DIR = REPO / "schemas"
PROFILE_DIR = ENGINE_DIR / "configs" / "profiles"
SCENE_DIR = ENGINE_DIR / "configs" / "scenes"
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", REPO / "ml" / "data" / "results" / "recorded_flight_validation"))
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", REPO / "simulation" / "data_export" / "recorded_flights"))
TRACKER_RUN = os.getenv("TRACKER_RUN", "tracktrack_ours")  # results subfolder of a processed flight

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://aerial:aerial_dev@localhost:5432/aerial")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://localhost:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "aerial")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "aerial_dev_secret")
S3_BUCKET = os.getenv("S3_BUCKET", "evidence")

JWT_SECRET = os.getenv("JWT_SECRET", "dev-jwt-secret-change-me-in-production-0123456789")
JWT_HOURS = 12
SERVICE_TOKEN = os.getenv("SERVICE_TOKEN", "dev-service-token")
ROLES = ("OFFICER", "OPERATOR", "PLANNER", "MAINTENANCE", "ADMIN")
DEV_USERS = {"officer": "OFFICER", "operator": "OPERATOR", "planner": "PLANNER",
             "maintenance": "MAINTENANCE", "admin": "ADMIN"}  # password: <name>123 (API.md)

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174").split(",")
LIVE_TTL_MS = 2000      # API.md: vehicle state lives 2 s in Redis
WS_MIN_PERIOD_S = 0.1   # API.md: vehicles pushed at <= 10 Hz per socket
TRAJ_MIN_DT_S = 0.2     # API.md: trajectories downsampled to <= 5 Hz
EXPORT_MAX_ROWS = 10000  # PDF of 10 000 events: 6.97 s, XLSX 3.30 s (dev laptop, 2026-10-10; target <= 10 s)
