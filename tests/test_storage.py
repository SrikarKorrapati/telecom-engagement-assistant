from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, StatementError

from app.db import initialize_database, make_engine, make_session_factory
from app.models import (
    AuditEvent, Base, Customer, DeliveryJob, EngagementRun, MessageRevision,
    Offer, Review, new_id,
)
from app.seed import BILLING_ZONE, demo_id, seed_demo

NOW = datetime(2026, 11, 17, 7, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    initialize_database(engine)
    with make_session_factory(engine)() as value:
        yield value
    engine.dispose()


def make_chain(session, *, customer=None, revision_no=1):
    if customer is None:
        customer = Customer(name="Synthetic", email="synthetic@example.test", scenario="test", context={})
        session.add(customer)
        session.flush()
    run = EngagementRun(customer_id=customer.id, snapshot={}, policy_snapshot={}, policy_hash="a" * 64)
    session.add(run)
    session.flush()
    revision = MessageRevision(
        run_id=run.id, revision_no=revision_no, subject="Synthetic", body="Demo message",
        content_hash="b" * 64, provider="template",
    )
    session.add(revision)
    session.flush()
    review = Review(message_id=revision.id, action="approve", actor="test-reviewer", content_hash=revision.content_hash)
    session.add(review)
    session.flush()
    return customer, run, revision, review


def test_exactly_seven_tables_with_jsonb_and_sqlite_foreign_keys(session):
    assert set(inspect(session.bind).get_table_names()) == {
        "customers", "offers", "engagement_runs", "message_revisions",
        "reviews", "delivery_jobs", "audit_events",
    }
    assert session.scalar(text("PRAGMA foreign_keys")) == 1
    assert Customer.__table__.c.context.type.compile(dialect=postgresql.dialect()) == "JSONB"
    assert Customer.__table__.c.updated_at.type.compile(dialect=postgresql.dialect()) == "TIMESTAMP WITH TIME ZONE"
    UUID(new_id())


def test_seed_is_idempotent_and_preserves_customer_and_offer_edits(session):
    assert seed_demo(session, NOW) == 10
    session.commit()
    customer = session.get(Customer, demo_id("customer:loyalty"))
    context = deepcopy(customer.context)
    context["permissions"]["marketing"] = "denied"
    customer.context = context
    customer.name = "Edited by reviewer"
    customer.version = 2
    offer = session.scalar(select(Offer))
    original_expiry = offer.details["expires_at"]
    offer.active = False
    session.commit()
    assert seed_demo(session, NOW + timedelta(days=1)) == 0
    session.commit()
    session.expire_all()
    assert len(session.scalars(select(Customer)).all()) == 10
    assert len(session.scalars(select(Offer)).all()) == 1
    assert customer.name == "Edited by reviewer"
    assert customer.version == 2
    assert customer.context["permissions"]["marketing"] == "denied"
    assert offer.active is False
    assert offer.details["expires_at"] == original_expiry


def test_seed_leaves_commit_to_caller(session):
    seed_demo(session, NOW)
    session.rollback()
    assert session.scalars(select(Customer)).all() == []
    assert session.scalars(select(Offer)).all() == []


@pytest.mark.parametrize("now", [NOW, datetime(2026, 12, 1, tzinfo=timezone.utc)])
def test_synthetic_fixtures_have_coherent_cycles_and_scenarios(session, now):
    seed_demo(session, now)
    today = now.astimezone(BILLING_ZONE).date()
    current_month = today.replace(day=1)
    for customer in session.scalars(select(Customer)):
        UUID(customer.id)
        assert customer.context["synthetic"] is True
        assert customer.email.endswith("@example.test")
        assert {f["channel"] for f in customer.context["feedback"]} == {"support_chat", "complaint", "survey", "email"}
        invoices = customer.context["invoices"]
        if invoices is None:
            continue
        paid = [item for item in invoices if item["status"] == "paid"]
        assert len(paid) == 1
        assert len({i["period_end"] for i in paid}) == 1
        for invoice in invoices:
            assert date.fromisoformat(invoice["period_end"]) <= date.fromisoformat(invoice["due_date"])
            assert Decimal(invoice["amount"]) >= Decimal(invoice["outstanding"]) >= 0
            assert isinstance(invoice["disputed"], bool)
            if invoice["status"] == "paid":
                assert date.fromisoformat(invoice["period_end"]) < current_month
                paid_at = datetime.fromisoformat(invoice["paid_full_at"])
                assert paid_at.tzinfo is not None
                assert paid_at.astimezone(BILLING_ZONE).date() <= date.fromisoformat(invoice["due_date"])
                assert paid_at <= now
            else:
                assert date.fromisoformat(invoice["due_date"]) < today
    missing = session.get(Customer, demo_id("customer:missing"))
    assert missing.context["invoices"] is None
    assert missing.context["usage"]["current_active_days"] is None
    assert session.get(Customer, demo_id("customer:dispute")).context["open_dispute"] is True
    assert session.get(Customer, demo_id("customer:opt_out")).context["permissions"]["marketing"] == "denied"
    stale_time = session.get(Customer, demo_id("customer:stale")).context["source_as_of"]
    assert now - datetime.fromisoformat(stale_time) == timedelta(hours=72)


def test_timestamp_roundtrip_is_utc_and_naive_input_is_rejected(session):
    local_now = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    customer = Customer(name="Demo", email="demo@example.test", scenario="test", context={}, updated_at=local_now)
    session.add(customer)
    session.commit()
    session.expire_all()
    assert customer.updated_at == NOW
    assert customer.updated_at.utcoffset() == timedelta(0)
    customer.updated_at = datetime(2026, 11, 17)
    with pytest.raises(StatementError, match="timezone-aware"):
        session.flush()


def test_invalid_customer_reference_is_rejected(session):
    session.add(EngagementRun(customer_id=new_id(), snapshot={}, policy_snapshot={}, policy_hash="a" * 64))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("invalid_kind", ["run", "message", "review"])
def test_delivery_rejects_mismatched_ownership(session, invalid_kind):
    _, run1, revision1, review1 = make_chain(session)
    _, run2, revision2, review2 = make_chain(session)
    ids = dict(run_id=run1.id, message_id=revision1.id, approval_review_id=review1.id)
    if invalid_kind == "run":
        ids["run_id"] = run2.id
    elif invalid_kind == "message":
        ids["message_id"] = revision2.id
    else:
        ids["approval_review_id"] = review2.id
    session.add(DeliveryJob(**ids, idempotency_key="test-action"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_delivery_allows_matching_chain_and_rejects_duplicate_logical_action(session):
    _, run, revision, review = make_chain(session)
    fields = dict(run_id=run.id, message_id=revision.id, approval_review_id=review.id, idempotency_key="one-action")
    session.add(DeliveryJob(**fields, status="delivered", delivered_at=NOW, result={"mode": "mock"}))
    session.commit()
    assert session.scalar(select(DeliveryJob)).delivered_at == NOW
    session.add(DeliveryJob(**fields))
    with pytest.raises(IntegrityError):
        session.flush()


def test_revision_numbers_and_offer_versions_are_unique(session):
    _, run, revision, _ = make_chain(session)
    session.commit()
    session.add(MessageRevision(run_id=run.id, revision_no=revision.revision_no, subject="Copy", body="Copy", content_hash="c" * 64, provider="template"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add_all([Offer(code="SYNTHETIC", version=1, details={}), Offer(code="SYNTHETIC", version=1, details={})])
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("model,fields", [
    (Customer, dict(name="Demo", email="d@example.test", scenario="test", context={}, version=0)),
    (Offer, dict(code="SYNTHETIC", version=0, details={})),
])
def test_invalid_versions_are_rejected(session, model, fields):
    session.add(model(**fields))
    with pytest.raises(IntegrityError):
        session.flush()


def test_evidence_rows_cannot_be_deleted_beneath_delivery(session):
    _, run, revision, review = make_chain(session)
    session.add(DeliveryJob(run_id=run.id, message_id=revision.id, approval_review_id=review.id, idempotency_key="retained"))
    session.add(AuditEvent(run_id=run.id, event_type="mock_delivery", actor="test", details={"synthetic": True}))
    session.commit()
    session.delete(review)
    with pytest.raises(IntegrityError):
        session.flush()
