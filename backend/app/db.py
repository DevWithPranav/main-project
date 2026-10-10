"""Async SQLAlchemy engine and session (PostGIS through asyncpg).

Tables come from models.metadata.create_all on startup; switch to Alembic migrations once the
schema settles.
"""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from . import config
from .models import Base

engine = create_async_engine(config.DATABASE_URL, pool_pre_ping=True, pool_size=10)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)
        # PRD 28.4: the audit log is immutable for every role, ADMIN included
        await conn.execute(text(
            "CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$ LANGUAGE plpgsql"))
        await conn.execute(text("DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log"))
        await conn.execute(text(
            "CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log "
            "FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()"))


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        yield s
