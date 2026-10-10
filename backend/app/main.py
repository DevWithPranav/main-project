r"""Aerial surveillance backend (Build Plan M5): FastAPI app, contract in backend/API.md.

Usage:
    docker compose up -d
    venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config, storage
from .audit import AuditMiddleware
from .auth import seed_users
from .db import SessionLocal, init_db
from .live import hub
from .routers import anomalies, audit, configs, core, events, live, planning, scenarios, sessions

log = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        await seed_users(db)
        await configs.seed_profiles(db)
    try:
        await asyncio.to_thread(storage.ensure_bucket)
    except Exception as e:  # noqa: BLE001 - /api/health reports it; the rest of the API still works
        log.warning("S3 bucket %s not ready: %s", config.S3_BUCKET, e)
    await hub.start()
    yield
    await hub.stop()


app = FastAPI(title="Aerial traffic surveillance API", version="0.1.0", lifespan=lifespan)
app.add_middleware(AuditMiddleware)  # PRD 28.4; added first, so it runs inside CORS
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["Content-Disposition", "X-Event-Count", "X-Event-Total", "X-Export-Seconds"])
for r in (core, sessions, events, anomalies, configs, planning, scenarios, audit, live):
    app.include_router(r.router, prefix="/api")
