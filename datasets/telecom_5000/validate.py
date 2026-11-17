"""Independent fixture integrity/oracle checks plus application-policy regression."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from uuid import UUID
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from generate import SEGMENTS, SCENARIOS, generate, write_json

ZONE = ZoneInfo("Asia/Kolkata")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    assert result.tzinfo is not None and result.utcoffset() is not None, "naive timestamp"
    return result


def independent_segments(context: dict, as_of: datetime) -> dict:
    """Small independent numerical oracle, using the published illustrative thresholds."""
    if as_of - timestamp(context["source_as_of"]) > timedelta(hours=48):
        return {key: "insufficient_evidence" for key in SEGMENTS}
    result = {}
    invoices = context["invoices"]
    if invoices is None:
        result.update(consistent="insufficient_evidence", loyalty_candidate="insufficient_evidence", payment_reminder="insufficient_evidence")
    else:
        month_start = as_of.astimezone(ZONE).date().replace(day=1)
        previous = sorted((i for i in invoices if date.fromisoformat(i["period_end"]) < month_start), key=lambda i: i["period_end"], reverse=True)[:3]
        if len(previous) < 3 or len({i["period_end"] for i in previous}) != 3:
            result.update(consistent="insufficient_evidence", loyalty_candidate="insufficient_evidence")
        else:
            paid_on_time = all(Decimal(i["outstanding"]) == 0 and i["paid_full_at"] is not None
                               and timestamp(i["paid_full_at"]).astimezone(ZONE).date() <= date.fromisoformat(i["due_date"]) for i in previous)
            result["consistent"] = "match" if paid_on_time else "no_match"
            result["loyalty_candidate"] = "match" if paid_on_time and context["active"] and context["tenure_months"] >= 12 else "no_match"
        overdue = any(Decimal(i["outstanding"]) > 0 and date.fromisoformat(i["due_date"]) < as_of.astimezone(ZONE).date() for i in invoices)
        result["payment_reminder"] = "match" if overdue else "no_match"
    usage = context["usage"]
    prior_valid = usage["prior_complete"] and usage["prior_days"] == 30 and usage["prior_active_days"] is not None
    current_valid = usage["current_complete"] and usage["current_days"] == 30 and usage["current_active_days"] is not None
    if usage["known_outage"] or not (prior_valid and current_valid):
        result["declining_activity"] = "insufficient_evidence"
    else:
        prior, current = usage["prior_active_days"], usage["current_active_days"]
        result["declining_activity"] = "match" if prior >= 10 and 0 < current and current * 100 <= prior * 60 else "no_match"
    if usage["known_outage"] or not current_valid:
        result["dormant"] = "insufficient_evidence"
    else:
        result["dormant"] = "match" if context["active"] and usage["current_active_days"] == 0 else "no_match"
    return result


def validate_record(customer: dict, evaluation: dict, label: dict, offers: list[dict]) -> None:
    assert set(customer) == {"id", "name", "email", "scenario", "context", "version"}
    UUID(customer["id"])
    assert customer["id"] == evaluation["customer_id"] == label["customer_id"]
    assert customer["scenario"] == label["scenario"]
    assert customer["email"].endswith("@example.com") and customer["name"].startswith("Synthetic Customer ")
    assert customer["version"] >= 1
    assert not ({"segments", "strategy", "expected", "label"} & customer.keys())
    context = customer["context"]
    assert context["synthetic"] is True
    assert context["currency"] == "INR" and context["billing_timezone"] == "Asia/Kolkata"
    as_of = timestamp(evaluation["as_of"])
    assert evaluation["as_of"] == label["as_of"]
    source = timestamp(context["source_as_of"])
    assert source <= as_of
    usage = context["usage"]
    for window in ("prior", "current"):
        start, end = date.fromisoformat(usage[f"{window}_start"]), date.fromisoformat(usage[f"{window}_end"])
        assert (end - start).days + 1 == usage[f"{window}_days"] == 30
        assert end < source.astimezone(ZONE).date()
        active = usage[f"{window}_active_days"]
        if active is not None:
            assert 0 <= active <= 30
        else:
            assert usage[f"{window}_complete"] is False
    assert date.fromisoformat(usage["prior_end"]) + timedelta(days=1) == date.fromisoformat(usage["current_start"])
    assert len(context["feedback"]) == 4
    assert {f["channel"] for f in context["feedback"]} == {"support_chat", "complaint", "survey", "email"}
    assert all(timestamp(f["occurred_at"]) <= source and f["text"].startswith("Synthetic feedback:") for f in context["feedback"])
    invoices = context["invoices"]
    invoice_by_id = {i["id"]: i for i in invoices or []}
    assert len(invoice_by_id) == len(invoices or [])
    for invoice in invoices or []:
        due, end = date.fromisoformat(invoice["due_date"]), date.fromisoformat(invoice["period_end"])
        assert end <= due and end <= source.astimezone(ZONE).date()
        amount, balance = Decimal(invoice["amount"]), Decimal(invoice["outstanding"])
        assert isinstance(invoice["amount"], str) and isinstance(invoice["outstanding"], str)
        assert amount.is_finite() and balance.is_finite() and 0 <= balance <= amount
        assert amount.as_tuple().exponent >= -2 and balance.as_tuple().exponent >= -2
        paid_amount = amount - balance
        assert paid_amount + balance == amount
        assert isinstance(invoice["disputed"], bool)
        if invoice["paid_full_at"] is not None:
            paid = timestamp(invoice["paid_full_at"])
            assert balance == 0 and invoice["status"] == "paid" and paid <= source
            assert paid.astimezone(ZONE).date() >= end
        else:
            assert balance > 0 and invoice["status"] in {"overdue", "partially_paid"}
    for reminder in context["reminder_history"]:
        assert reminder["invoice_id"] in invoice_by_id
        sent = timestamp(reminder["sent_at"])
        assert sent <= source
        overdue = (sent.astimezone(ZONE).date() - date.fromisoformat(invoice_by_id[reminder["invoice_id"]]["due_date"])).days
        assert overdue > 0
        assert 1 <= reminder["stage"] <= (1 if overdue <= 7 else 2 if overdue <= 14 else 3)
        key = f"{customer['id']}:billing:{reminder['invoice_id']}:{reminder['stage']}"
        assert any(e["idempotency_key"] == key and e["created_at"] == reminder["sent_at"] for e in evaluation["recent_deliveries"])
    for redemption in context["redemptions"] or []:
        assert redemption["offer_code"] in {o["code"] for o in offers}
        assert timestamp(redemption["redeemed_at"]) <= source
    for event in evaluation["recent_deliveries"]:
        assert timestamp(event["created_at"]) <= as_of and event["idempotency_key"].startswith(customer["id"] + ":")
    assert independent_segments(context, as_of) == label["segments"], "independent segment oracle disagrees with authored labels"
    # Independently check the preconditions for each authored strategy family.
    objective = label["strategy_objective"]
    if objective == "billing_reminder":
        assert label["segments"]["payment_reminder"] == "match" and context["permissions"]["billing"] == "allowed"
        relevant = sorted((i for i in invoices if Decimal(i["outstanding"]) > 0 and date.fromisoformat(i["due_date"]) < as_of.astimezone(ZONE).date()), key=lambda i: (i["due_date"], i["id"]))[0]
        prior = sorted((r for r in context["reminder_history"] if r["invoice_id"] == relevant["id"]), key=lambda r: r["sent_at"])
        if prior:
            assert as_of - timestamp(prior[-1]["sent_at"]) >= timedelta(days=7)
            assert label["reminder_stage"] == prior[-1]["stage"] + 1
        else:
            assert label["reminder_stage"] == 1
    elif objective == "offer_or_reward":
        offer = next(o for o in offers if o["id"] == label["offer_id"])
        assert offer["active"] and context["plan_code"] in offer["details"]["eligible_plans"]
        assert timestamp(offer["details"]["expires_at"]) > as_of and context["permissions"]["marketing"] == "allowed"
        assert any(label["segments"][s] == "match" for s in ("loyalty_candidate", "declining_activity", "dormant"))
    elif objective == "appreciation_without_offer":
        assert context["active"] and label["segments"]["consistent"] == "match"
        assert label["segments"]["declining_activity"] == label["segments"]["dormant"] == "no_match"
    if customer["scenario"] == "expired_offer_no_outreach":
        matching = [o for o in offers if context["plan_code"] in o["details"]["eligible_plans"] and o["active"]]
        assert matching and all(timestamp(o["details"]["expires_at"]) <= as_of for o in matching)


def validate(directory: Path, *, app_check: bool = True, reproducibility: bool = False) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    customers = read_jsonl(directory / "customers.jsonl")
    contexts = {r["customer_id"]: r for r in read_jsonl(directory / "evaluation_contexts.jsonl")}
    labels = {r["customer_id"]: r for r in read_jsonl(directory / "expected_labels.jsonl")}
    offers = json.loads((directory / "offers.json").read_text())
    errors = []
    scenarios, objectives, segment_counts, overlap = Counter(), Counter(), Counter(), Counter()
    assert len(customers) == len(contexts) == len(labels) == manifest["count"] == 5000
    assert len({c["id"] for c in customers}) == len({c["email"] for c in customers}) == 5000
    assert len({c["context"]["account_id"] for c in customers}) == len({c["context"]["subscription_id"] for c in customers}) == 5000
    assert len({o["id"] for o in offers}) == len({(o["code"], o["version"]) for o in offers}) == len(offers)
    for offer in offers:
        UUID(offer["id"])
        assert offer["details"]["synthetic"] is True and offer["details"]["redemption_days"] == 90
        timestamp(offer["details"]["expires_at"])
    for filename, expected_hash in manifest["sha256"].items():
        assert hashlib.sha256((directory / filename).read_bytes()).hexdigest() == expected_hash, f"Hash mismatch: {filename}"
    if app_check:
        sys.path.insert(0, str(HERE.parents[1]))
        from app.policy import evaluate
    for customer in customers:
        label, context = labels[customer["id"]], contexts[customer["id"]]
        try:
            validate_record(customer, context, label, offers)
            if app_check:
                snapshot = {"customer_id": customer["id"], "customer_name": customer["name"], "customer_email": customer["email"],
                            "customer_version": customer["version"], "context": customer["context"], "offers": offers, **context}
                actual = evaluate(snapshot)
                assert {s["segment"]: s["status"] for s in actual["segments"]} == label["segments"], "application segment mismatch"
                assert actual["strategy"]["objective"] == label["strategy_objective"], "application strategy mismatch"
                assert actual["strategy"]["reason_code"] == label["reason_code"], "application reason mismatch"
                assert actual["strategy"]["reminder_stage"] == label["reminder_stage"], "application stage mismatch"
                assert actual["strategy"]["offer_id"] == label["offer_id"], "application offer mismatch"
        except (AssertionError, ValueError, KeyError, TypeError) as error:
            errors.append({"customer_id": customer["id"], "scenario": customer["scenario"], "error": str(error) or "integrity assertion failed"})
        scenarios[customer["scenario"]] += 1
        objectives[label["strategy_objective"]] += 1
        for segment, status in label["segments"].items():
            segment_counts[f"{segment}:{status}"] += 1
        overlap[sum(status == "match" for status in label["segments"].values())] += 1
    assert dict(scenarios) == manifest["scenario_counts"]
    reproduction = "not run"
    if reproducibility:
        with tempfile.TemporaryDirectory(prefix=".reproduce-", dir=directory) as tmp:
            regenerated = generate(Path(tmp), timestamp(manifest["as_of"]), manifest["seed"])
            assert regenerated["sha256"] == manifest["sha256"], "Seeded generation was not byte-reproducible"
        reproduction = "passed: all four input/label payload files byte-identical"
    report = {"status": "passed" if not errors else "failed", "as_of": manifest["as_of"], "records_checked": len(customers),
              "scenario_counts": dict(scenarios), "strategy_counts": dict(objectives), "segment_status_counts": dict(segment_counts),
              "matched_segment_count_distribution": dict(sorted(overlap.items())), "independent_integrity_checks": "completed for every record",
              "independent_segment_oracle": "hand-implemented published thresholds; no application evaluator calls",
              "strategy_labels": "manually authored scenario outcomes, with independent strategy precondition checks",
              "application_regression": "all records compared" if app_check else "not run", "reproducibility": reproduction,
              "errors": errors, "limitations": ["Synthetic scenario-balanced coverage is not a real customer population.",
              "Expected decisions apply only to prototype-v1 policies at the manifest as_of time.",
              "Feedback is templated synthetic text, not sentiment training or evaluation ground truth.",
              "Missing evidence cases are intentional nulls, not corrupted records."]}
    write_json(directory / "validation_report.json", report)
    lines = ["# Validation and coverage report", "", f"**{report['status'].upper()} — {len(customers):,} synthetic records; {len(errors)} errors.**", "",
             f"Reference time: `{manifest['as_of']}`. Seed: `{manifest['seed']}`. Policy: `prototype-v1`.", "",
             "Independent checks cover UUID/key uniqueness, relationships, exact money arithmetic, paid dates, usage windows, source timestamps, reminders, offer references, reserved contacts, and all four feedback channels.", "",
             "Segment results are checked with a separate numerical implementation. Strategy expectations are manually authored per scenario and checked for relevant preconditions, then compared with the application evaluator. Expected labels are never generated by the application evaluator.", "",
             f"Reproducibility: {reproduction}.", "", "## Strategy coverage", "", "| Strategy | Records |", "|---|---:|"]
    lines.extend(f"| {key} | {value:,} |" for key, value in sorted(objectives.items()))
    lines += ["", "## Scenario coverage", "", "| Scenario | Records |", "|---|---:|"]
    lines.extend(f"| {key} | {value:,} |" for key, value in scenarios.items())
    lines += ["", "## Independent segment coverage", "", "| Segment / status | Records |", "|---|---:|"]
    lines.extend(f"| {key} | {value:,} |" for key, value in sorted(segment_counts.items()))
    lines += ["", "## Limits", ""] + ["- " + item for item in report["limitations"]]
    (directory / "VALIDATION_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=HERE)
    parser.add_argument("--skip-app-check", action="store_true", help="Run standalone integrity/oracle checks without importing app.policy.")
    parser.add_argument("--check-reproducibility", action="store_true")
    args = parser.parse_args()
    result = validate(args.directory, app_check=not args.skip_app_check, reproducibility=args.check_reproducibility)
    print(json.dumps({key: result[key] for key in ("status", "records_checked", "strategy_counts", "reproducibility")}))
    if result["errors"]:
        print(json.dumps(result["errors"][:10], indent=2))
        raise SystemExit(1)
