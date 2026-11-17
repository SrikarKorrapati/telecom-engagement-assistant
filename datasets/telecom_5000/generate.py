"""Reproducible fictional fixtures; no API, environment-file, or network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Kolkata")
SEGMENTS = ["consistent", "loyalty_candidate", "declining_activity", "dormant", "payment_reminder"]
# Manually authored expectations; the generator never imports the application evaluator.
# M=match, N=no_match, U=insufficient_evidence. Every scenario has 200 records.
SCENARIOS = [
    ("loyalty_offer", "MMNNN", "offer_or_reward", "eligible_approved_offer", None),
    ("appreciation", "MNNNN", "appreciation_without_offer", "consistent_customer_appreciation", None),
    ("decline_offer", "MNMNN", "offer_or_reward", "eligible_approved_offer", None),
    ("dormant_offer", "MNNMN", "offer_or_reward", "eligible_approved_offer", None),
    ("loyalty_decline_overlap", "MMMNN", "offer_or_reward", "eligible_approved_offer", None),
    ("billing_first", "NNNNM", "billing_reminder", "verified_overdue_invoice", 1),
    ("billing_decline_overlap", "NNMNM", "billing_reminder", "verified_overdue_invoice", 1),
    ("billing_stage2", "NNNNM", "billing_reminder", "verified_overdue_invoice", 2),
    ("billing_stage3", "NNNNM", "billing_reminder", "verified_overdue_invoice", 3),
    ("billing_recent_hold", "NNNNM", "hold", "reminder_progression_hold", None),
    ("billing_permission_denied", "NNNNM", "hold", "contact_permission_not_allowed", None),
    ("marketing_permission_denied", "MMNNN", "hold", "contact_permission_not_allowed", None),
    ("marketing_permission_missing", "MMNNN", "hold", "contact_permission_not_allowed", None),
    ("source_stale", "UUUUU", "hold", "evidence_or_policy_hold", None),
    ("invoices_missing", "UUNNU", "hold", "evidence_or_policy_hold", None),
    ("usage_incomplete", "MNUUN", "hold", "segment_evidence_incomplete", None),
    ("open_dispute", "NNNNM", "hold", "evidence_or_policy_hold", None),
    ("known_outage", "MMUUN", "hold", "known_outage_hold", None),
    ("recent_redemption_appreciation", "MMNNN", "appreciation_without_offer", "consistent_customer_appreciation", None),
    ("expired_offer_no_outreach", "MNMNN", "no_outreach", "no_eligible_outreach", None),
    ("inactive_no_outreach", "MNNNN", "no_outreach", "no_eligible_outreach", None),
    ("missing_redemption_hold", "MMNNN", "hold", "offer_eligibility_unknown", None),
    ("frequency_cap_hold", "MMNNN", "hold", "contact_frequency_limit", None),
    ("duplicate_action_hold", "MMNNN", "hold", "duplicate_outreach", None),
    ("not_consistent_no_outreach", "NNNNN", "no_outreach", "no_eligible_outreach", None),
]


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def stable_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, "synthetic-telecom-5000:" + value))


def month_start(today: date, offset: int) -> date:
    index = today.year * 12 + today.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def midnight(day: date) -> str:
    return stamp(datetime.combine(day, time(0), ZONE))


def catalogue(now: datetime) -> list[dict]:
    versions = [
        (1, True, ["POSTPAID_EXPIRED_499"], now - timedelta(days=1)),
        (2, True, ["POSTPAID_499", "POSTPAID_799"], now + timedelta(days=90)),
        (3, False, ["POSTPAID_499", "POSTPAID_799"], now + timedelta(days=120)),
    ]
    return [dict(
        id=stable_id(f"offer:LOYALTY_2GB:{version}"), code="LOYALTY_2GB", version=version, active=active,
        details={"synthetic": True, "title": f"Synthetic 2GB offer version {version}",
                 "eligible_plans": plans, "value_text": "2GB at no additional cost",
                 "terms": "Illustrative offer: 2GB for 30 days from activation, once per 90 days. No actual provisioning.",
                 "expires_at": stamp(expiry), "redemption_days": 90, "channel": "email"},
    ) for version, active, plans, expiry in versions]


def history(now: datetime, number: int, amount: str, late: bool) -> list[dict]:
    today = now.astimezone(ZONE).date()
    result = []
    for previous in range(1, 4):
        end = month_start(today, 1 - previous) - timedelta(days=1)
        due = end + timedelta(days=10)
        paid = due + timedelta(days=2) if late and previous >= 2 else min(due - timedelta(days=1), today - timedelta(days=1))
        result.append({"id": f"INV-SYN-{number:05d}-H{previous}", "period_end": end.isoformat(),
                       "due_date": due.isoformat(), "amount": amount, "outstanding": "0.00",
                       "paid_full_at": midnight(paid), "status": "paid", "disputed": False})
    return result


def build_customer(number: int, spec: tuple, now: datetime, rng: random.Random, seed: int):
    scenario, statuses, objective, reason, stage = spec
    today = now.astimezone(ZONE).date()
    cid = stable_id(f"seed:{seed}:customer:{number}")
    current_offer_id = stable_id("offer:LOYALTY_2GB:2")
    plan = "POSTPAID_499" if scenario == "expired_offer_no_outreach" else rng.choice(["POSTPAID_499", "POSTPAID_799"])
    amount = "499.00" if plan == "POSTPAID_499" else "799.00"
    prior = rng.randint(20, 30)
    current = rng.randint(20, 30)
    tenure = rng.randint(12, 72)
    if scenario in {"appreciation", "decline_offer", "dormant_offer", "usage_incomplete", "expired_offer_no_outreach", "not_consistent_no_outreach"}:
        tenure = rng.randint(3, 11)
    if scenario in {"decline_offer", "loyalty_decline_overlap", "billing_decline_overlap", "expired_offer_no_outreach"}:
        current = rng.randint(1, int(prior * 0.60))
    if scenario == "dormant_offer":
        current = 0
    billing = scenario.startswith("billing_") or scenario == "open_dispute"
    local_midnight = datetime.combine(today, time(0), ZONE).astimezone(timezone.utc)
    fresh_minutes = min(120, int((now - local_midnight).total_seconds() // 60))
    context = {
        "synthetic": True, "account_id": f"ACC-SYN-{number:05d}", "subscription_id": f"SUB-SYN-{number:05d}",
        "billing_timezone": "Asia/Kolkata", "active": scenario != "inactive_no_outreach",
        "plan_code": plan, "tenure_months": tenure, "currency": "INR",
        "source_as_of": stamp(now - timedelta(minutes=rng.randint(0, fresh_minutes))),
        "usage": {"prior_active_days": prior, "current_active_days": current,
                  "prior_complete": True, "current_complete": True, "prior_days": 30, "current_days": 30,
                  "known_outage": scenario == "known_outage",
                  "prior_start": (today - timedelta(days=60)).isoformat(), "prior_end": (today - timedelta(days=31)).isoformat(),
                  "current_start": (today - timedelta(days=30)).isoformat(), "current_end": (today - timedelta(days=1)).isoformat()},
        "invoices": history(now, number, amount, late=billing or scenario == "not_consistent_no_outreach"),
        "permissions": {"billing": "allowed", "marketing": "allowed"},
        "open_dispute": scenario == "open_dispute", "reminder_history": [], "redemptions": [],
        "feedback": [],
    }
    texts = {
        "support_chat": ["Please explain the data allowance in my plan.", "How can I view my previous invoice?", "Please confirm my current plan name."],
        "complaint": ["My earlier coverage complaint is resolved.", "The previously reported data issue is resolved.", "The earlier service ticket has been closed."],
        "survey": ["The service usually meets my needs.", "The billing explanation was clear.", "I found the support reply useful."],
        "email": ["Thank you for explaining the plan.", "I would like a copy of the plan details.", "Please keep my contact preference on record."],
    }
    for offset, (channel, choices) in enumerate(texts.items(), start=1):
        context["feedback"].append({"channel": channel, "text": "Synthetic feedback: " + rng.choice(choices),
                                    "occurred_at": stamp(now - timedelta(days=offset + 1))})
    if billing:
        overdue_days = {"billing_stage2": 12, "billing_stage3": 22, "billing_recent_hold": 10}.get(scenario, 5)
        due = today - timedelta(days=overdue_days)
        period_end = due - timedelta(days=10)
        historical_ends = {i["period_end"] for i in context["invoices"]}
        while period_end.isoformat() in historical_ends:
            period_end -= timedelta(days=1)
        invoice_id = f"INV-SYN-{number:05d}-CURRENT"
        billed = rng.choice(["700.00", "900.00", "1200.00"])
        outstanding = billed if number % 2 else {"700.00": "350.00", "900.00": "450.00", "1200.00": "600.00"}[billed]
        context["invoices"].append({"id": invoice_id, "period_end": period_end.isoformat(), "due_date": due.isoformat(),
                                    "amount": billed, "outstanding": outstanding, "paid_full_at": None,
                                    "status": "overdue" if outstanding == billed else "partially_paid", "disputed": scenario == "open_dispute"})
        if scenario == "billing_stage2":
            context["reminder_history"] = [{"invoice_id": invoice_id, "stage": 1, "sent_at": stamp(now - timedelta(days=8))}]
        elif scenario == "billing_stage3":
            context["reminder_history"] = [
                {"invoice_id": invoice_id, "stage": 1, "sent_at": stamp(now - timedelta(days=16))},
                {"invoice_id": invoice_id, "stage": 2, "sent_at": stamp(now - timedelta(days=8))},
            ]
        elif scenario == "billing_recent_hold":
            context["reminder_history"] = [{"invoice_id": invoice_id, "stage": 1, "sent_at": stamp(now - timedelta(days=3))}]
        if scenario == "billing_permission_denied":
            context["permissions"]["billing"] = "denied"
    if scenario == "marketing_permission_denied":
        context["permissions"]["marketing"] = "denied"
    elif scenario == "marketing_permission_missing":
        context["permissions"]["marketing"] = None
    elif scenario == "source_stale":
        context["source_as_of"] = stamp(now - timedelta(hours=rng.randint(49, 120)))
    elif scenario == "invoices_missing":
        context["invoices"] = None
    elif scenario == "usage_incomplete":
        context["usage"].update(current_complete=False, current_active_days=None)
    elif scenario == "open_dispute":
        context["feedback"][1]["text"] = "Synthetic feedback: I dispute the roaming charge on the current invoice."
    elif scenario == "recent_redemption_appreciation":
        context["redemptions"] = [{"offer_code": "LOYALTY_2GB", "redeemed_at": stamp(now - timedelta(days=rng.randint(1, 89)))}]
    elif scenario == "expired_offer_no_outreach":
        context["plan_code"] = "POSTPAID_EXPIRED_499"
    elif scenario == "missing_redemption_hold":
        context["redemptions"] = None
    observed = datetime.fromisoformat(context["source_as_of"])
    window_anchor = observed.astimezone(ZONE).date()
    context["usage"].update(
        prior_start=(window_anchor - timedelta(days=60)).isoformat(),
        prior_end=(window_anchor - timedelta(days=31)).isoformat(),
        current_start=(window_anchor - timedelta(days=30)).isoformat(),
        current_end=(window_anchor - timedelta(days=1)).isoformat(),
    )
    for offset, feedback in enumerate(context["feedback"], start=1):
        feedback["occurred_at"] = stamp(observed - timedelta(days=offset))
    if scenario == "source_stale":
        context["invoices"] = history(observed, number, amount, late=False)
    deliveries = [{"created_at": item["sent_at"], "purpose": "billing",
                   "idempotency_key": f"{cid}:billing:{item['invoice_id']}:{item['stage']}"}
                  for item in context["reminder_history"]]
    if scenario == "frequency_cap_hold":
        deliveries = [{"created_at": stamp(now - timedelta(days=2)), "purpose": "marketing", "idempotency_key": f"{cid}:marketing:synthetic-previous-campaign"}]
    elif scenario == "duplicate_action_hold":
        deliveries = [{"created_at": stamp(now - timedelta(days=10)), "purpose": "marketing", "idempotency_key": f"{cid}:marketing:{current_offer_id}"}]
    customer = {"id": cid, "name": f"Synthetic Customer {number:05d}", "email": f"synthetic.telecom.{number:05d}@example.com",
                "scenario": scenario, "context": context, "version": 1}
    evaluation_context = {"customer_id": cid, "as_of": stamp(now), "recent_deliveries": deliveries}
    label = {"customer_id": cid, "scenario": scenario, "as_of": stamp(now),
             "segments": dict(zip(SEGMENTS, [{"M": "match", "N": "no_match", "U": "insufficient_evidence"}[s] for s in statuses])),
             "strategy_objective": objective, "reason_code": reason, "reminder_stage": stage,
             "offer_id": current_offer_id if objective == "offer_or_reward" else None}
    return customer, evaluation_context, label


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def generate(output: Path, now: datetime, seed: int) -> dict:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("--as-of must include a timezone")
    now = now.astimezone(timezone.utc)
    if now.year < 2000:
        raise ValueError("--as-of must be in or after 2000")
    output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    files = [output / name for name in ("customers.jsonl", "evaluation_contexts.jsonl", "expected_labels.jsonl")]
    streams = [p.open("w", encoding="utf-8", newline="\n") for p in files]
    try:
        for index in range(5000):
            values = build_customer(index + 1, SCENARIOS[index % len(SCENARIOS)], now, rng, seed)
            for stream, value in zip(streams, values):
                stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    finally:
        for stream in streams:
            stream.close()
    write_json(output / "offers.json", catalogue(now))
    files.append(output / "offers.json")
    manifest = {"dataset": "telecom_5000", "synthetic": True, "count": 5000, "seed": seed, "as_of": stamp(now),
                "generator_version": 1, "policy_version": "prototype-v1", "billing_timezone": "Asia/Kolkata",
                "scenario_counts": {spec[0]: 200 for spec in SCENARIOS},
                "purpose": "Balanced synthetic policy demonstration and regression fixtures; not representative of any real population.",
                "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    write_json(output / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="Timezone-aware ISO timestamp; becomes the evaluation reference time.")
    parser.add_argument("--seed", type=int, default=20261117)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        reference = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
        result = generate(args.output_dir, reference, args.seed)
    except ValueError as error:
        parser.error(str(error))
    print(f"Generated {result['count']} synthetic customers at {args.output_dir.resolve()}")
