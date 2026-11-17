"""Update the current policy/content in place; preserve audit and offer history.

File replacement is atomic and the shared lock serializes dashboard edits.
Failed database commits restore the previous file. Production deployment needs
an authenticated configuration store and a distributed transaction/outbox.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID

from sqlalchemy import select

from app.content import validate_content
from app.models import Offer
from app.policy import _policy
from app.paths import policy_directory
from app import configuration_store
from app.workflow import audit, digest

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = policy_directory() / "policy.json"
CONTENT_PATH = policy_directory() / "content.json"
LOCK_PATH = ROOT / ".local/policy-dashboard.lock"


class PolicyAdminError(ValueError):
    def __init__(self, message, code="invalid_input"):
        super().__init__(message)
        self.code = code


def _keys(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise PolicyAdminError(f"{name} has missing or unsupported fields.")


def _text(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise PolicyAdminError(f"{name} must be nonempty plain text, at most {limit} characters.")


def _integer(value, name, minimum=1, maximum=10000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PolicyAdminError(f"{name} must be a whole number between {minimum} and {maximum}.")


def validate_configuration(policy, content):
    """Validate both documents together so reminder rules and copy stay compatible."""
    _keys(policy, ("version", "label", "example_policy", "billing_timezone", "max_source_age_hours",
                   "clock_skew_seconds", "segments", "outreach", "offer", "reminders"), "Policy")
    for value, name, limit in ((policy["version"], "Policy version", 80), (policy["label"], "Policy label", 200)):
        _text(value, name, limit)
    if policy["example_policy"] is not True:
        raise PolicyAdminError("The policy must remain explicitly labelled as an example.")
    _integer(policy["max_source_age_hours"], "Evidence age", maximum=8760)
    _integer(policy["clock_skew_seconds"], "Clock tolerance", minimum=0, maximum=3600)
    rules = policy["segments"]
    _keys(rules, ("loyalty_tenure_months", "usage_window_days"), "Segment rules")
    for key in ("loyalty_tenure_months", "usage_window_days"):
        _integer(rules[key], key, maximum=1200)
    _keys(policy["outreach"], ("frequency_days",), "Contact rules")
    _keys(policy["offer"], ("channel",), "Offer rules")
    _integer(policy["outreach"]["frequency_days"], "Contact interval")

    _keys(content, ("version", "label", "example_policy", "greeting", "signature", "tones", "templates",
                    "generation", "validation_patterns", "critique"), "Content")
    _keys(content["tones"], ("offer_or_reward", "appreciation_without_offer"), "Content tones")
    _keys(content["templates"], ("offer_or_reward", "appreciation_without_offer", "billing_reminder"), "Templates")
    for objective in ("offer_or_reward", "appreciation_without_offer"):
        _keys(content["templates"][objective], ("subject", "opening", "closing", "action"), objective)
    billing = content["templates"]["billing_reminder"]
    _keys(billing, ("closing", "action", "stages"), "Billing content")
    if not isinstance(billing["stages"], dict):
        raise PolicyAdminError("Billing templates must be keyed by stage.")
    for stage in billing["stages"].values():
        _keys(stage, ("subject", "opening"), "Billing stage content")
    _keys(content["generation"], ("prompt_version", "system_prompt"), "Generator configuration")
    _keys(content["critique"], ("prompt_version", "system_prompt", "max_generation_attempts"), "Critic configuration")
    _keys(content["validation_patterns"], ("prohibited_language", "unsupported_claims", "unsupported_dates", "sensitive_data"), "Language checks")
    try:
        validate_content(content)
        _policy({**policy, "content": content})
    except (ValueError, TypeError) as error:
        raise PolicyAdminError(str(error)) from None


def _documents(session=None, *, for_update=False):
    try:
        if configuration_store.enabled():
            policy, content = configuration_store.read_documents(session, for_update=for_update)
        else:
            policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
            content = json.loads(CONTENT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise PolicyAdminError("Policy files could not be loaded.", "unavailable") from None
    validate_configuration(policy, content)
    return policy, content


@contextmanager
def _lock(*, shared=False):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _catalog(session):
    offers = session.scalars(select(Offer).order_by(Offer.code, Offer.version, Offer.id)).all()
    return [{"id": offer.id, "code": offer.code, "version": offer.version,
             "active": offer.active, "details": deepcopy(offer.details)} for offer in offers]


def _view(session, policy, content):
    offers = _catalog(session)
    return {"policy": policy, "content": content, "policy_hash": digest(policy),
            "content_hash": digest(content), "offers": offers, "catalog_hash": digest(offers)}


def configuration(sessions):
    with _lock(shared=True), sessions() as session:
        return _view(session, *_documents(session))


def _replace(path, data):
    """Readers see complete JSON, never a partially written file."""
    descriptor, temporary = tempfile.mkstemp(prefix=".policy-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_configuration(sessions, actor, *, section, document, expected_hash):
    if not isinstance(section, str) or section not in {"policy", "content"}:
        raise PolicyAdminError("Choose policy or content configuration.")
    if not isinstance(document, dict):
        raise PolicyAdminError("Configuration must be an object.")
    # Copy caller-owned input before validation and publication.
    document = deepcopy(document)
    with _lock(), sessions() as session:
        policy, content = _documents(session, for_update=True)
        current = policy if section == "policy" else content
        if not isinstance(expected_hash, str) or digest(current) != expected_hash:
            raise PolicyAdminError("Configuration changed since this page loaded. Refresh before saving.", "conflict")
        if document == current:
            return _view(session, policy, content)
        if document.get("version") != current["version"]:
            raise PolicyAdminError("Keep the current configuration name. Update its settings in place.")
        if section == "policy":
            policy = document
        else:
            content = document
        validate_configuration(policy, content)
        audit(session, None, "configuration_saved", {"section": section, "previous_version": current["version"],
              "version": document["version"], "previous_hash": expected_hash,
              "configuration_hash": digest(document), "document": document}, actor)
        session.flush()
        if configuration_store.enabled():
            configuration_store.write_documents(session, policy, content)
            session.commit()
            return _view(session, policy, content)
        path = POLICY_PATH if section == "policy" else CONTENT_PATH
        previous = path.read_bytes()
        payload = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        _replace(path, payload)
        try:
            session.commit()
        except Exception:
            _replace(path, previous)
            raise
        return _view(session, policy, content)


def validate_offer(details, active):
    _keys(details, ("channel", "eligible_plans", "expires_at", "redemption_days", "synthetic", "terms", "title", "value_text"), "Offer details")
    if type(active) is not bool:
        raise PolicyAdminError("Offer status must be a boolean.")
    if details["synthetic"] is not True or details["channel"] != "email":
        raise PolicyAdminError("Prototype offers must be synthetic and use email.")
    for field, limit in (("title", 160), ("value_text", 300), ("terms", 1500)):
        _text(details[field], field, limit)
    plans = details["eligible_plans"]
    if not isinstance(plans, list) or not plans or len(plans) > 100 or len(set(p for p in plans if isinstance(p, str))) != len(plans):
        raise PolicyAdminError("Provide one to one hundred distinct eligible plan codes.")
    for plan in plans:
        _text(plan, "Plan code", 80)
    _integer(details["redemption_days"], "Redemption interval")
    try:
        _text(details["expires_at"], "Expiry timestamp", 80)
        expiry = datetime.fromisoformat(details["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise PolicyAdminError("Offer expiry must be an ISO date/time with a timezone.") from None


def save_offer(sessions, actor, *, offer_id, details, active, expected_hash):
    validate_offer(details, active)
    try:
        offer_id = str(UUID(str(offer_id)))
    except (ValueError, TypeError, AttributeError):
        raise PolicyAdminError("Select an existing offer to publish a new version.") from None
    with _lock(), sessions() as session:
        policy, content = _documents()
        catalogue = _catalog(session)
        if not isinstance(expected_hash, str) or digest(catalogue) != expected_hash:
            raise PolicyAdminError("Offer catalogue changed since this page loaded. Refresh before saving.", "conflict")
        source = session.get(Offer, offer_id)
        if source is None:
            raise PolicyAdminError("Offer not found.", "not_found")
        if source.details == details and source.active == active:
            return _view(session, policy, content)
        matching = session.scalars(select(Offer).where(Offer.code == source.code).with_for_update()).all()
        version = max(offer.version for offer in matching) + 1
        retired = [offer.id for offer in matching if offer.active]
        for offer in matching:
            offer.active = False
        offer = Offer(code=source.code, version=version, details=deepcopy(details), active=active)
        session.add(offer)
        session.flush()
        audit(session, None, "offer_version_published", {"offer_id": offer.id, "source_offer_id": source.id,
              "code": source.code, "version": version, "active": active, "retired_offer_ids": retired,
              "details": deepcopy(details)}, actor)
        session.commit()
        return _view(session, policy, content)
