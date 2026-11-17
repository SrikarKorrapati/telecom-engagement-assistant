"""Deterministic example policies. No model output can select customer treatment."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.content import load_content, validate_content
from app.paths import policy_directory
from app import configuration_store

SEGMENTS = ("consistent", "loyalty_candidate", "declining_activity", "dormant", "payment_reminder")

# Only these context service facts may participate in treatment decisions.
# Nested objects are read through explicit field lookups below. Names, gender,
# ethnicity, religion, address, postcode and feedback are not decision features.
# IDs are used only for evidence identity/deduplication, never demographic inference.
# This documents input use; it is not a proof of population-level fairness.
DECISION_FEATURE_ALLOWLIST = frozenset({
    "account_id", "subscription_id", "source_as_of", "currency", "active", "plan_code",
    "tenure_months", "usage", "invoices", "permissions", "open_dispute",
    "reminder_history", "redemptions",
})
DECISION_SNAPSHOT_FEATURE_ALLOWLIST = frozenset({"customer_id", "as_of", "context", "offers", "recent_deliveries"})


class EvidenceError(ValueError):
    """Missing or inconsistent evidence is an explicit unknown, never false."""


def load_policy() -> dict:
    try:
        if configuration_store.enabled():
            policy, content = configuration_store.read_documents()
        else:
            policy = json.loads((policy_directory() / "policy.json").read_text())
            content = load_content()
        if not isinstance(policy, dict):
            raise ValueError("Policy configuration must be an object.")
        policy["content"] = validate_content(content)
        _policy(policy)
        return policy
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Policy configuration could not be loaded.") from error


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{field} is missing or invalid")
    return value


def _integer(value: Any, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise EvidenceError(f"{field} is missing or invalid")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise EvidenceError(f"{field} is missing or invalid")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(_text(value, field).replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise EvidenceError(f"{field} requires a timezone")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise EvidenceError(f"{field} is missing or invalid") from exc


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise EvidenceError(f"{field} is missing or invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceError(f"{field} is invalid") from exc


def _money(value: Any, field: str) -> Decimal:
    # The input contract uses decimal strings, avoiding binary-float balances.
    if not isinstance(value, str):
        raise EvidenceError(f"{field} requires an exact decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise EvidenceError(f"{field} is invalid") from exc
    if not result.is_finite() or result < 0 or result.as_tuple().exponent < -2:
        raise EvidenceError(f"{field} must be a nonnegative amount with at most two decimal places")
    return result


def _policy(policy: dict) -> ZoneInfo:
    try:
        _text(policy["version"], "policy version")
        zone = ZoneInfo(policy["billing_timezone"])
        _integer(policy["max_source_age_hours"], "max_source_age_hours", 1)
        _integer(policy["clock_skew_seconds"], "clock_skew_seconds")
        if set(policy["segments"]) != {"loyalty_tenure_months", "usage_window_days"}:
            raise EvidenceError("Use only the current simple rule settings")
        for key in ("loyalty_tenure_months", "usage_window_days"):
            _integer(policy["segments"][key], key, 1)
        _integer(policy["outreach"]["frequency_days"], "frequency_days", 1)
        content = validate_content(policy["content"])
        reminders = policy["reminders"]
        if not isinstance(reminders, list) or not 1 <= len(reminders) <= 3:
            raise EvidenceError("Configure one to three reminder stages")
        previous_day = 0
        for stage, rule in enumerate(reminders, 1):
            if not isinstance(rule, dict) or set(rule) != {"min_overdue_days", "min_gap_days", "tone"}:
                raise EvidenceError("Reminder stages require overdue days, gap days and tone")
            day = _integer(rule["min_overdue_days"], "minimum overdue days", 1)
            _integer(rule["min_gap_days"], "minimum reminder gap days", 1)
            if day <= previous_day or rule["tone"] not in {"courteous", "direct", "firm"}:
                raise EvidenceError("Reminder thresholds must increase and tones must be respectful")
            if stage == 1 and rule["tone"] != "courteous":
                raise EvidenceError("The first reminder must be courteous")
            if str(stage) not in content["templates"]["billing_reminder"]["stages"]:
                raise EvidenceError("A template is required for every configured reminder stage")
            previous_day = day
        if policy["offer"]["channel"] != "email":
            raise EvidenceError("prototype supports only email")
        return zone
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError, InvalidOperation) as exc:
        raise EvidenceError(f"Invalid policy: {exc}") from exc


def _result(segment: str, status: str, code: str, reason: str, evidence: dict | None = None) -> dict:
    return {"segment": segment, "status": status, "reason_code": code, "reason": reason,
            "evidence": evidence or {}, "evidence_quality": "insufficient" if status == "insufficient_evidence" else "verified"}


def _strategy(objective: str, code: str, reason: str, **values: Any) -> dict:
    result = {"objective": objective, "purpose": None, "tone": None, "reason_code": code,
              "reason": reason, "invoice_id": None, "offer_id": None, "reminder_stage": None,
              "blocked_reasons": [], "idempotency_key": None}
    result.update(values)
    return result


def _hold(reasons: list[str], code: str = "evidence_or_policy_hold") -> dict:
    unique = list(dict.fromkeys(reasons))
    return _strategy("hold", code, " ".join(unique), blocked_reasons=unique)


def _finish(segments: list[dict], strategy: dict) -> dict:
    return {"segments": segments, "strategy": strategy, "blocked_reasons": strategy["blocked_reasons"]}


def _invoices(context: dict, now: datetime, zone: ZoneInfo) -> list[dict]:
    raw = context.get("invoices")
    if not isinstance(raw, list):
        raise EvidenceError("Invoice evidence is missing")
    result, identifiers = [], set()
    for invoice in raw:
        if not isinstance(invoice, dict):
            raise EvidenceError("Invoice evidence is malformed")
        identifier = _text(invoice.get("id"), "invoice id")
        if identifier in identifiers:
            raise EvidenceError("Conflicting duplicate invoice identifiers")
        identifiers.add(identifier)
        amount = _money(invoice.get("amount"), "invoice amount")
        balance = _money(invoice.get("outstanding"), "invoice outstanding")
        due = _date(invoice.get("due_date"), "invoice due date")
        period_end = _date(invoice.get("period_end"), "billing period end")
        disputed = _boolean(invoice.get("disputed"), "invoice disputed status")
        status = invoice.get("status")
        if status not in ("open", "paid", "overdue", "partially_paid", "disputed"):
            raise EvidenceError("Invoice status is missing or invalid")
        if balance > amount or (status == "paid" and balance > 0):
            raise EvidenceError("Invoice balance conflicts with amount or paid status")
        paid_raw = invoice.get("paid_full_at")
        paid_date = None
        if paid_raw is not None:
            if isinstance(paid_raw, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", paid_raw):
                paid_date = _date(paid_raw, "fully paid date")
                if paid_date > now.astimezone(zone).date():
                    raise EvidenceError("Fully paid date is in the future")
            else:
                paid_at = _timestamp(paid_raw, "fully paid timestamp")
                if paid_at > now:
                    raise EvidenceError("Fully paid timestamp is in the future")
                paid_date = paid_at.astimezone(zone).date()
            if balance > 0:
                raise EvidenceError("Fully paid timestamp conflicts with outstanding balance")
        if status == "paid" and paid_date is None:
            raise EvidenceError("Paid invoice lacks a fully paid date")
        result.append({"id": identifier, "amount": amount, "outstanding": balance, "due": due,
                       "period_end": period_end, "paid_date": paid_date, "disputed": disputed or status == "disputed"})
    return result


def _latest_bill_paid_on_time(invoices: list[dict], today: date) -> tuple[bool, dict]:
    month_start = today.replace(day=1)
    completed = sorted((i for i in invoices if i["period_end"] < month_start), key=lambda i: i["period_end"], reverse=True)
    if not completed:
        raise EvidenceError("The latest completed bill is missing")
    latest = completed[0]
    if len(completed) > 1 and completed[1]["period_end"] == latest["period_end"]:
        raise EvidenceError("The latest completed bill is ambiguous")
    if latest["outstanding"] == 0 and latest["paid_date"] is None:
        raise EvidenceError("The latest settled bill lacks its fully paid date")
    paid_on_time = (latest["outstanding"] == 0 and latest["paid_date"] is not None
                    and latest["paid_date"] <= latest["due"])
    return paid_on_time, {"invoice_ids": [latest["id"]], "completed_cycles": 1,
                         "fully_paid_on_time": paid_on_time}


def _window(context: dict, which: str, days: int) -> int:
    usage = context.get("usage")
    if not isinstance(usage, dict):
        raise EvidenceError("Usage evidence is missing")
    if _boolean(usage.get("known_outage"), "known outage"):
        raise EvidenceError("Known outage requires review of usage evidence")
    if not _boolean(usage.get(f"{which}_complete"), f"{which} window complete"):
        raise EvidenceError(f"{which.capitalize()} usage window is incomplete")
    observed_days = _integer(usage.get(f"{which}_days"), f"{which} window days", 1)
    if observed_days != days:
        raise EvidenceError(f"{which.capitalize()} usage window must cover {days} days")
    active = _integer(usage.get(f"{which}_active_days"), f"{which} active days")
    if active > observed_days:
        raise EvidenceError("Active days exceed the observation window")
    return active


def _segments(context: dict, policy: dict, now: datetime, zone: ZoneInfo, invoices: list[dict] | None, invoice_error: str | None, source_error: str | None) -> list[dict]:
    today, rules = now.astimezone(zone).date(), policy["segments"]
    results = []
    for segment in SEGMENTS:
        try:
            if source_error:
                raise EvidenceError(source_error)
            if segment in ("consistent", "payment_reminder") and invoice_error:
                raise EvidenceError(invoice_error)
            if segment == "consistent":
                matches, evidence = _latest_bill_paid_on_time(invoices or [], today)
                reason = "Latest completed bill paid on time." if matches else "Latest completed bill not paid on time."
            elif segment == "loyalty_candidate":
                active = _boolean(context.get("active"), "subscription active status")
                tenure = _integer(context.get("tenure_months"), "tenure months")
                matches = active and tenure >= rules["loyalty_tenure_months"]
                evidence = {"active": active, "tenure_months": tenure}
                reason = "Active customer with qualifying tenure." if matches else "Inactive or below the loyalty tenure."
            elif segment == "declining_activity":
                prior = _window(context, "prior", rules["usage_window_days"])
                current = _window(context, "current", rules["usage_window_days"])
                matches = 0 < current < prior
                evidence = {"prior_active_days": prior, "current_active_days": current, "window_days": rules["usage_window_days"]}
                reason = "Usage is lower than before." if matches else "Usage has not declined, or is zero."
            elif segment == "dormant":
                current = _window(context, "current", rules["usage_window_days"])
                active = _boolean(context.get("active"), "subscription active status")
                matches = active and current == 0
                evidence = {"current_active_days": current, "active": active, "window_days": rules["usage_window_days"]}
                reason = "Active subscription has zero usage in a complete window." if matches else "The subscription is inactive or has current usage."
            else:
                overdue = [i for i in invoices or [] if i["outstanding"] > 0 and i["due"] < today]
                matches = bool(overdue)
                evidence = {"overdue_invoice_ids": [i["id"] for i in overdue], "as_of_date": today.isoformat()}
                reason = "Verified outstanding balance exists past its due date." if matches else "No verified invoice is both outstanding and overdue."
            results.append(_result(segment, "match" if matches else "no_match", f"{segment}_{'matched' if matches else 'not_matched'}", reason, evidence))
        except EvidenceError as exc:
            results.append(_result(segment, "insufficient_evidence", f"{segment}_insufficient_evidence", str(exc)))
    return results


def _offer(snapshot: dict, policy: dict, now: datetime) -> tuple[dict | None, list[str]]:
    context = snapshot["context"]
    catalogue = snapshot.get("offers")
    if not isinstance(catalogue, list):
        return None, ["Approved offer catalogue is missing"]
    try:
        plan = _text(context.get("plan_code"), "plan code")
        active_subscription = _boolean(context.get("active"), "subscription active status")
    except EvidenceError as exc:
        return None, [str(exc)]
    eligible, errors = [], []
    for offer in catalogue:
        try:
            if not isinstance(offer, dict):
                raise EvidenceError("Approved offer is malformed")
            if not _boolean(offer.get("active"), "offer active status"):
                continue
            _text(offer.get("id"), "offer version identifier")
            code = _text(offer.get("code"), "offer code")
            _integer(offer.get("version"), "offer version", 1)
            details = offer.get("details")
            if not isinstance(details, dict):
                raise EvidenceError("Approved offer details are missing")
            for key in ("title", "value_text", "terms"):
                _text(details.get(key), f"offer {key}")
            plans = details.get("eligible_plans")
            if not isinstance(plans, list) or not all(isinstance(p, str) and p for p in plans):
                raise EvidenceError("Offer plan eligibility is unknown")
            expires = _timestamp(details.get("expires_at"), "offer expiry")
            channel = _text(details.get("channel"), "offer channel")
            limit = _integer(details.get("redemption_days"), "offer redemption period", 1)
            if not active_subscription or plan not in plans or expires <= now or channel != policy["offer"]["channel"]:
                continue
            redemptions = context.get("redemptions")
            if not isinstance(redemptions, list):
                raise EvidenceError("Offer redemption history is unknown")
            redeemed = False
            for item in redemptions:
                if not isinstance(item, dict):
                    raise EvidenceError("Offer redemption history is malformed")
                redemption_code = _text(item.get("offer_code"), "redemption offer code")
                when = _timestamp(item.get("redeemed_at"), "redemption timestamp")
                if when > now:
                    raise EvidenceError("Offer redemption timestamp is in the future")
                if redemption_code == code and now - when < timedelta(days=limit):
                    redeemed = True
            if not redeemed:
                eligible.append(offer)
        except EvidenceError as exc:
            errors.append(str(exc))
    if eligible:
        return sorted(eligible, key=lambda o: (o["code"], -o["version"], o["id"]))[0], []
    return None, errors


def _delivery_history(snapshot: dict, now: datetime) -> list[tuple[str, datetime]]:
    history = snapshot.get("recent_deliveries")
    if not isinstance(history, list):
        raise EvidenceError("Delivery history is missing")
    result = []
    for event in history:
        if not isinstance(event, dict):
            raise EvidenceError("Delivery history is malformed")
        when = _timestamp(event.get("created_at"), "delivery timestamp")
        key = _text(event.get("idempotency_key"), "delivery idempotency key")
        if when > now:
            raise EvidenceError("Delivery history has a future timestamp")
        result.append((key, when))
    return result


def _completed(key: str, when: datetime, **values) -> dict:
    return _strategy("no_outreach", "outreach_completed",
                     "This outreach was already delivered. No repeat message is needed.",
                     completed_key=key, completed_at=when.isoformat(), **values)


def _reminder(snapshot: dict, invoice: dict, now: datetime, policy: dict, zone: ZoneInfo) -> dict:
    """Advance one stage only, backed by matching, chronological send evidence."""
    history = snapshot["context"].get("reminder_history")
    if not isinstance(history, list):
        return _hold(["Reminder history is missing"], "reminder_history_unknown")
    prefix = f"{snapshot['customer_id']}:billing:{invoice['id']}:"
    rules = policy["reminders"]
    try:
        deliveries = _delivery_history(snapshot, now)
        recorded = {}
        for item in history:
            if not isinstance(item, dict):
                raise EvidenceError("Reminder history is malformed")
            identifier = _text(item.get("invoice_id"), "reminder invoice identifier")
            stage = _integer(item.get("stage"), "recorded reminder stage", 1)
            if stage > 3:
                raise EvidenceError("Reminder history contains an unsupported stage")
            when = _timestamp(item.get("sent_at"), "reminder sent timestamp")
            if when > now:
                raise EvidenceError("Reminder history has a future send")
            if identifier == invoice["id"]:
                if stage in recorded:
                    raise EvidenceError("Reminder history contains a duplicate stage")
                recorded[stage] = when
        stages = sorted(recorded)
        if stages != list(range(1, len(stages) + 1)):
            raise EvidenceError("Reminder history must contain consecutive stages starting at one")
        times = [recorded[stage] for stage in stages]
        if any(a >= b for a, b in zip(times, times[1:])):
            raise EvidenceError("Reminder history is not in chronological stage order")
        if any(when.astimezone(zone).date() <= invoice["due"] for when in times):
            raise EvidenceError("Reminder history predates the overdue invoice")
        for stage in stages:
            if stage <= len(rules):
                rule = rules[stage - 1]
                if (recorded[stage].astimezone(zone).date() - invoice["due"]).days < rule["min_overdue_days"]:
                    raise EvidenceError("A recorded reminder predates its configured overdue threshold")
                if stage > 1 and recorded[stage] - recorded[stage - 1] < timedelta(days=rule["min_gap_days"]):
                    raise EvidenceError("Recorded reminders violate the configured minimum interval")
        expected = {(prefix + str(stage), when) for stage, when in recorded.items()}
        actual = {(key, when) for key, when in deliveries if key.startswith(prefix)}
        if expected != actual:
            raise EvidenceError("Reminder history does not match verified delivery history")
    except EvidenceError as error:
        return _hold([str(error)], "reminder_history_unknown")
    stage = len(stages) + 1
    previous_key = prefix + str(stage - 1) if stages else None
    if stage > len(rules):
        return _completed(previous_key, times[-1], invoice_id=invoice["id"], reminder_stage=stage - 1)
    rule = rules[stage - 1]
    earliest = datetime.combine(invoice["due"] + timedelta(days=rule["min_overdue_days"]),
                                datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
    if stages:
        earliest = max(earliest, times[-1] + timedelta(days=rule["min_gap_days"]))
    if now < earliest:
        result = _hold([f"Reminder stage {stage} is not due until {earliest.astimezone(zone).isoformat()}"],
                       "reminder_not_due")
        result.update(next_eligible_at=earliest.isoformat(), previous_delivery_key=previous_key,
                      invoice_id=invoice["id"], reminder_stage=stage)
        return result
    return _strategy("billing_reminder", "verified_overdue_invoice",
                     "A verified overdue invoice takes precedence over discretionary offers.",
                     purpose="billing", tone=rule["tone"], invoice_id=invoice["id"], reminder_stage=stage,
                     idempotency_key=prefix + str(stage))


def _contact_gate(snapshot: dict, strategy: dict, now: datetime, policy: dict) -> dict:
    permissions = snapshot["context"].get("permissions")
    purpose = strategy["purpose"]
    if not isinstance(permissions, dict) or permissions.get(purpose) != "allowed":
        return _hold([f"Contact permission for {purpose} is not explicitly allowed"], "contact_permission_not_allowed")
    try:
        history = _delivery_history(snapshot, now)
    except EvidenceError as exc:
        return _hold([str(exc)], "delivery_history_unknown")
    for key, when in history:
        if _same_outreach(key, strategy["idempotency_key"], snapshot["customer_id"]):
            return _completed(key, when, invoice_id=strategy["invoice_id"], reminder_stage=strategy["reminder_stage"])
    if history:
        earliest = max(when for _, when in history) + timedelta(days=policy["outreach"]["frequency_days"])
        if now < earliest:
            result = _hold(["The customer contact-frequency limit has not elapsed"], "contact_frequency_limit")
            result["next_eligible_at"] = earliest.isoformat()
            return result
    return strategy


def _same_outreach(recorded_key: str, current_key: str, customer_id: str) -> bool:
    if recorded_key == current_key:
        return True
    # Older appreciation keys included the policy version. A settings change
    # must not permit another appreciation for the same customer and month.
    prefix = f"{customer_id}:marketing:appreciation:"
    return (recorded_key.startswith(prefix) and current_key.startswith(prefix)
            and recorded_key.rsplit(":", 1)[-1] == current_key.rsplit(":", 1)[-1])


def evaluate(snapshot: dict, policy: dict | None = None) -> dict:
    """Return every rule result and one policy-selected action from a frozen snapshot."""
    try:
        policy = load_policy() if policy is None else policy
        zone = _policy(policy)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("context"), dict):
            raise EvidenceError("Customer context is missing")
        now = _timestamp(snapshot.get("as_of"), "evaluation timestamp")
        customer_id = _text(snapshot.get("customer_id"), "customer identifier")
    except ValueError as exc:
        return _finish([_result(s, "insufficient_evidence", "invalid_snapshot_or_policy", str(exc)) for s in SEGMENTS], _hold([str(exc)]))
    snapshot = {key: value for key, value in snapshot.items() if key in DECISION_SNAPSHOT_FEATURE_ALLOWLIST}
    snapshot["context"] = {key: value for key, value in snapshot["context"].items() if key in DECISION_FEATURE_ALLOWLIST}
    context, source_error = snapshot["context"], None
    try:
        observed = _timestamp(context.get("source_as_of"), "source timestamp")
        if now - observed > timedelta(hours=policy["max_source_age_hours"]):
            raise EvidenceError("Source evidence is stale")
        if observed - now > timedelta(seconds=policy["clock_skew_seconds"]):
            raise EvidenceError("Source timestamp is unexpectedly in the future")
        _text(context.get("account_id"), "account identifier")
        _text(context.get("subscription_id"), "subscription identifier")
        if not isinstance(context.get("currency"), str) or not re.fullmatch(r"[A-Z]{3}", context["currency"]):
            raise EvidenceError("Invoice currency is missing or invalid")
    except EvidenceError as exc:
        source_error = str(exc)
    invoices, invoice_error = None, None
    try:
        invoices = _invoices(context, now, zone)
    except EvidenceError as exc:
        invoice_error = str(exc)
    segments = _segments(context, policy, now, zone, invoices, invoice_error, source_error)
    guards = [error for error in (source_error, invoice_error) if error]
    try:
        if _boolean(context.get("open_dispute"), "open dispute status") or any(i["disputed"] for i in invoices or []):
            guards.append("An open dispute requires human resolution before outreach")
    except EvidenceError as exc:
        guards.append(str(exc))
    if guards:
        return _finish(segments, _hold(guards))
    statuses = {item["segment"]: item["status"] for item in segments}
    today = now.astimezone(zone).date()
    if statuses["payment_reminder"] == "match":
        invoice = sorted((i for i in invoices or [] if i["outstanding"] > 0 and i["due"] < today), key=lambda i: (i["due"], i["id"]))[0]
        strategy = _reminder(snapshot, invoice, now, policy, zone)
        if strategy["objective"] == "billing_reminder":
            strategy = _contact_gate(snapshot, strategy, now, policy)
        return _finish(segments, strategy)
    usage = context.get("usage")
    try:
        if not isinstance(usage, dict):
            raise EvidenceError("Outage evidence is missing for discretionary outreach")
        known_outage = _boolean(usage.get("known_outage"), "known outage")
    except EvidenceError as exc:
        return _finish(segments, _hold([str(exc)], "outage_evidence_unknown"))
    if known_outage:
        return _finish(segments, _hold(["A known outage requires review before discretionary outreach"], "known_outage_hold"))
    if any(statuses[s] == "match" for s in ("loyalty_candidate", "dormant", "declining_activity")):
        offer, errors = _offer(snapshot, policy, now)
        if errors:
            return _finish(segments, _hold(errors, "offer_eligibility_unknown"))
        if offer:
            strategy = _strategy("offer_or_reward", "eligible_approved_offer", "A qualifying segment and all approved offer controls match.",
                                 purpose="marketing", tone=policy["content"]["tones"]["offer_or_reward"], offer_id=offer["id"], idempotency_key=f"{customer_id}:marketing:{offer['id']}")
            return _finish(segments, _contact_gate(snapshot, strategy, now, policy))
    if context.get("active") is True and statuses["consistent"] == "match" and statuses["dormant"] == "no_match" and statuses["declining_activity"] == "no_match":
        strategy = _strategy("appreciation_without_offer", "consistent_customer_appreciation", "An active consistent customer qualifies for appreciation without an offer.",
                             purpose="marketing", tone=policy["content"]["tones"]["appreciation_without_offer"], idempotency_key=f"{customer_id}:marketing:appreciation:{today:%Y-%m}")
        return _finish(segments, _contact_gate(snapshot, strategy, now, policy))
    if any(v == "insufficient_evidence" for v in statuses.values()):
        return _finish(segments, _hold(["Required segment evidence is incomplete for selecting outreach"], "segment_evidence_incomplete"))
    return _finish(segments, _strategy("no_outreach", "no_eligible_outreach", "No approved outreach objective matches the verified evidence."))
