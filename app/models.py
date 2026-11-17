"""Seven-table prototype model; PostgreSQL is the intended backend."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Reject ambiguous naive writes and preserve UTC on SQLite readback."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must be timezone-aware")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def json_document():
    # Replace an entire dict/list on update: in-place nested mutation is not tracked.
    return JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (CheckConstraint("version >= 1", name="ck_customer_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(320))
    scenario: Mapped[str] = mapped_column(String(240))
    context: Mapped[dict] = mapped_column(json_document())
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Offer(Base):
    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_offer_code_version"),
        CheckConstraint("version >= 1", name="ck_offer_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)
    details: Mapped[dict] = mapped_column(json_document())
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class EngagementRun(Base):
    __tablename__ = "engagement_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('evaluated','held','no_outreach','drafted','validation_failed',"
            "'approved','rejected','delivered')", name="ck_run_state",
        ),
        Index("ix_run_customer_created", "customer_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    state: Mapped[str] = mapped_column(String(32), default="evaluated")
    snapshot: Mapped[dict] = mapped_column(json_document())
    policy_snapshot: Mapped[dict] = mapped_column(json_document())
    policy_hash: Mapped[str] = mapped_column(String(64))
    segments: Mapped[list] = mapped_column(json_document(), default=list)
    strategy: Mapped[dict] = mapped_column(json_document(), default=dict)
    # Application checks ownership; no circular insert dependency on revisions.
    latest_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    blocked_reasons: Mapped[list] = mapped_column(json_document(), default=list)


class MessageRevision(Base):
    __tablename__ = "message_revisions"
    __table_args__ = (
        UniqueConstraint("run_id", "revision_no", name="uq_revision_number"),
        UniqueConstraint("id", "run_id", name="uq_revision_run_identity"),
        CheckConstraint("revision_no >= 1", name="ck_revision_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("engagement_runs.id", ondelete="RESTRICT"))
    revision_no: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(250))
    body: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    validation: Mapped[dict] = mapped_column(json_document(), default=dict)
    provider: Mapped[str] = mapped_column(String(40))
    model_version: Mapped[str] = mapped_column(String(120), default="none")
    prompt_version: Mapped[str] = mapped_column(String(120), default="none")
    template_version: Mapped[str] = mapped_column(String(120), default="unspecified")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("id", "message_id", name="uq_review_message_identity"),
        CheckConstraint("action IN ('approve','reject','edit')", name="ck_review_action"),
        Index("ix_review_message_created", "message_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(ForeignKey("message_revisions.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(16))
    feedback: Mapped[str] = mapped_column(Text, default="")
    scores: Mapped[dict] = mapped_column(json_document(), default=dict)
    review_source: Mapped[str] = mapped_column(String(20), default="unspecified")
    actor: Mapped[str] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class DeliveryJob(Base):
    __tablename__ = "delivery_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "run_id"], ["message_revisions.id", "message_revisions.run_id"],
            name="fk_delivery_message_run", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["approval_review_id", "message_id"], ["reviews.id", "reviews.message_id"],
            name="fk_delivery_review_message", ondelete="RESTRICT",
        ),
        UniqueConstraint("idempotency_key", name="uq_delivery_logical_action"),
        Index("ix_delivery_run_created", "run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("engagement_runs.id", ondelete="RESTRICT"))
    message_id: Mapped[str] = mapped_column(String(36))
    approval_review_id: Mapped[str] = mapped_column(String(36))
    idempotency_key: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    result: Mapped[dict] = mapped_column(json_document(), default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_run_created", "run_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagement_runs.id", ondelete="RESTRICT"), nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(100))
    actor: Mapped[str] = mapped_column(String(160))
    details: Mapped[dict] = mapped_column(json_document(), default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
