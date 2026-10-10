"""PostGIS tables (API.md "Where things live").

events.data keeps the event exactly as schemas/event.schema.json has it (without session_id and
review, which come from their own columns / table); the other event columns are copies for
filtering. geom is a Point in map metres (SRID 0).
"""

from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))


class Session(Base):
    __tablename__ = "sessions"
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(16))  # carla | video | live
    town: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flight: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    t0_s: Mapped[float] = mapped_column(Float, default=0.0)  # sim / video time at started_at
    profile: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scene: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)  # import details


class Track(Base):
    __tablename__ = "tracks"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id", ondelete="CASCADE"), primary_key=True)
    track_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cls: Mapped[str | None] = mapped_column(String(32), nullable=True)
    points: Mapped[list] = mapped_column(JSONType)  # [[t_s, x, y, speed_kmh], ...] at <= 5 Hz


class Event(Base):
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    condition: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    cls: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    t_s: Mapped[float] = mapped_column(Float)  # flag_s (violation) or t_s (anomaly)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lane_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zone_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry("POINT", srid=0, spatial_index=True))
    review_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    review: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # latest review (schema "review")
    data: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), index=True)
    outcome: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    by: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONType)
    by: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlannerHistory(Base):
    __tablename__ = "planner_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(128), index=True)
    decision: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    by: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recommendation: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    validation: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # M8 CARLA validation result


class AnomalyStatus(Base):
    """Maintenance status changes of an anomaly event (PRD: flagged -> reviewed -> work_order_issued
    -> repaired, set by MAINTENANCE / ADMIN). The event row's `status` holds the current one."""
    __tablename__ = "anomaly_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    by: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
