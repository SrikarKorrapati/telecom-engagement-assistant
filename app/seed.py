"""Idempotent, synthetic-only fixtures. Call once at demo initialization."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, Offer, utcnow

BILLING_ZONE = ZoneInfo("Asia/Kolkata")


def demo_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, "telecom-engagement-demo:" + value))


def _month_start(today: date, offset: int) -> date:
    total = today.year * 12 + today.month - 1 + offset
    return date(total // 12, total % 12 + 1, 1)


def _paid_history(now: datetime, key: str) -> list[dict]:
    today = now.astimezone(BILLING_ZONE).date()
    history = []
    for previous in (1,):
        period_end = _month_start(today, 1 - previous) - timedelta(days=1)
        due = period_end + timedelta(days=10)
        paid_day = min(due - timedelta(days=1), today - timedelta(days=1))
        paid_at = datetime.combine(paid_day, datetime.min.time(), tzinfo=timezone.utc)
        history.append({
            "id": f"INV-{key.upper()}-{period_end:%Y%m}",
            "period_end": period_end.isoformat(), "due_date": due.isoformat(),
            "amount": "499.00", "outstanding": "0.00",
            "paid_full_at": paid_at.isoformat(), "status": "paid", "disputed": False,
        })
    return history


def _context(now: datetime, key: str) -> dict:
    return {
        "synthetic": True,
        "account_id": f"ACC-{key.upper()}", "subscription_id": f"SUB-{key.upper()}",
        "billing_timezone": "Asia/Kolkata", "active": True,
        "plan_code": "POSTPAID_499", "tenure_months": 18, "currency": "INR",
        "source_as_of": now.isoformat(),
        "usage": {
            "prior_active_days": 20, "current_active_days": 21,
            "prior_complete": True, "current_complete": True,
            "prior_days": 30, "current_days": 30, "known_outage": False,
        },
        "invoices": _paid_history(now, key),
        "permissions": {"billing": "allowed", "marketing": "allowed"},
        "open_dispute": False, "reminder_history": [], "redemptions": [],
        "feedback": [
            {"channel": "support_chat", "text": "Synthetic: please explain my current plan.",
             "occurred_at": (now - timedelta(days=4)).isoformat()},
            {"channel": "complaint", "text": "Synthetic: my earlier coverage query has been resolved.",
             "occurred_at": (now - timedelta(days=3)).isoformat()},
            {"channel": "survey", "text": "Synthetic: the service meets my needs.",
             "occurred_at": (now - timedelta(days=2)).isoformat()},
            {"channel": "email", "text": "Synthetic: thank you for explaining the plan.",
             "occurred_at": (now - timedelta(days=1)).isoformat()},
        ],
    }


def _add_overdue(context: dict, now: datetime, key: str, *, disputed: bool = False) -> None:
    today = now.astimezone(BILLING_ZONE).date()
    due = today - timedelta(days=5)
    # The invoice's period cannot end after its due date, including early-month runs.
    period_end = min(today.replace(day=1), due - timedelta(days=1))
    context["invoices"].append({
        "id": f"INV-{key.upper()}-CURRENT", "period_end": period_end.isoformat(),
        "due_date": due.isoformat(), "amount": "700.00", "outstanding": "700.00",
        "paid_full_at": None, "status": "open", "disputed": disputed,
    })


def seed_demo(session: Session, now: datetime | None = None) -> int:
    """Add absent fixtures, preserve prior edits, and leave transaction control to caller."""
    now = now or utcnow()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Seed time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    existing_offer = session.scalar(select(Offer).where(Offer.code == "LOYALTY_2GB", Offer.version == 1))
    if existing_offer is None:
        session.add(Offer(
            id=demo_id("offer:LOYALTY_2GB:1"), code="LOYALTY_2GB", version=1, active=True,
            details={
                "synthetic": True, "title": "Demo loyalty 2GB add-on",
                "eligible_plans": ["POSTPAID_499", "POSTPAID_799"],
                "value_text": "2GB at no additional cost",
                "terms": "2GB add-on valid for 30 days from activation. Once per 90 days. Synthetic prototype offer.",
                "expires_at": (now + timedelta(days=90)).isoformat(),
                "redemption_days": 90, "channel": "email",
            },
        ))

    scenarios = [
        ("billing", "Demo Asha", "Overdue invoice with declining activity"),
        ("loyalty", "Demo Bala", "Eligible loyalty offer"),
        ("appreciation", "Demo Charu", "Consistent customer without an offer trigger"),
        ("decline", "Demo Dev", "Declining activity"),
        ("dormant", "Demo Esha", "Dormant subscription"),
        ("missing", "Demo Farah", "Missing invoice and usage evidence"),
        ("dispute", "Demo Gita", "Disputed overdue invoice"),
        ("opt_out", "Demo Hari", "Marketing opt-out"),
        ("stale", "Demo Isha", "Stale source evidence"),
        ("redeemed", "Demo Jai", "Offer already redeemed recently"),
    ]
    inserted = 0
    for key, name, scenario in scenarios:
        customer_id = demo_id("customer:" + key)
        if session.get(Customer, customer_id) is not None:
            continue
        context = _context(now, key)
        if key == "billing":
            context["usage"]["current_active_days"] = 10
            _add_overdue(context, now, key)
        elif key == "loyalty":
            context["tenure_months"] = 24
        elif key == "appreciation":
            context["tenure_months"] = 6
        elif key == "decline":
            context["tenure_months"] = 6
            context["usage"].update(prior_active_days=25, current_active_days=10)
        elif key == "dormant":
            context["usage"]["current_active_days"] = 0
        elif key == "missing":
            context["invoices"] = None
            context["usage"].update(current_active_days=None, current_complete=False)
        elif key == "dispute":
            _add_overdue(context, now, key, disputed=True)
            context["open_dispute"] = True
            context["feedback"][1]["text"] = "Synthetic: I dispute the roaming charge on my latest invoice."
        elif key == "opt_out":
            context["permissions"]["marketing"] = "denied"
        elif key == "stale":
            context["source_as_of"] = (now - timedelta(hours=72)).isoformat()
        elif key == "redeemed":
            context["redemptions"] = [{"offer_code": "LOYALTY_2GB", "redeemed_at": (now - timedelta(days=20)).isoformat()}]
        session.add(Customer(
            id=customer_id, name=name, email=f"{key}@example.test", scenario=scenario,
            context=deepcopy(context), version=1, updated_at=now,
        ))
        inserted += 1
    session.flush()
    return inserted
