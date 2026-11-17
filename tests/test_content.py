"""Versioned configuration, frozen-content behavior and fail-closed boundaries."""

from copy import deepcopy
import json

import pytest

import app.content as content_module
from app.content import load_content, validate_content
from app.messages import generate_message
from app.policy import load_policy
from app.validation import validate_message


@pytest.fixture
def snapshot():
    return {"customer_id": "CUSTOMER-1", "customer_name": "Demo Asha", "customer_email": "asha@example.test",
            "context": {"account_id": "ACC-ASHA", "subscription_id": "SUB-ASHA", "currency": "INR",
                        "invoices": [{"id": "INV-ASHA", "outstanding": "700.00", "due_date": "2026-11-12"}]},
            "content_policy": load_content()}


@pytest.fixture
def strategy():
    return {"objective": "billing_reminder", "purpose": "billing", "tone": "courteous",
            "invoice_id": "INV-ASHA", "reminder_stage": 1}


def validate(message, snapshot, strategy):
    return validate_message(message["subject"], message["body"], snapshot, strategy)


def test_load_policy_bundles_fresh_valid_content():
    policy = load_policy()
    assert policy["content"] == load_content()
    assert policy["content"]["example_policy"] is True
    policy["content"]["tones"]["offer_or_reward"] = "supportive"
    assert load_policy()["content"]["tones"]["offer_or_reward"] == "warm"


def test_configurable_subject_courtesy_and_required_statement(snapshot, strategy):
    content = snapshot["content_policy"]
    content["version"] = "telecom-facts-v2-test"
    content["greeting"] = "Dear {customer_name},"
    content["signature"] = "Demo Support"
    template = content["templates"]["billing_reminder"]
    template["stages"]["1"] = {"subject": "Please review your mobile account", "opening": "We would like to help you review your bill."}
    template["action"] = "Contact your usual support channel to discuss or dispute this balance."
    template["closing"] = "We appreciate your attention."
    before = deepcopy(snapshot)
    message = generate_message(snapshot, strategy)
    assert message["subject"] == "Please review your mobile account"
    for text in ("Dear Demo Asha,", "Demo Support", template["action"], template["closing"]):
        assert text in message["body"]
    assert message["template_version"] == message["content_version"] == "telecom-facts-v2-test"
    assert validate(message, snapshot, strategy)["passed"]
    changed = {**message, "body": message["body"].replace(template["action"], "")}
    assert not validate(changed, snapshot, strategy)["passed"]
    assert snapshot == before


def test_frozen_content_ignores_later_file_configuration(snapshot, strategy, monkeypatch):
    before = generate_message(snapshot, strategy)
    changed = load_content()
    changed["version"] = "new-current-content"
    changed["templates"]["billing_reminder"]["stages"]["1"]["subject"] = "A newly configured subject"
    monkeypatch.setattr(content_module, "load_content", lambda: deepcopy(changed))
    assert generate_message(snapshot, strategy) == before
    assert validate(before, snapshot, strategy)["passed"]
    legacy = deepcopy(snapshot)
    del legacy["content_policy"]
    current = generate_message(legacy, strategy)
    assert current["subject"] == "A newly configured subject"
    assert current["content_version"] == "new-current-content"


def test_configured_blocked_language_is_enforced(snapshot, strategy):
    message = generate_message(snapshot, strategy)
    message["body"] += "\nThis is unhelpful."
    assert validate(message, snapshot, strategy)["passed"]
    snapshot["content_policy"]["validation_patterns"]["prohibited_language"].append(r"\bunhelpful\b")
    result = validate(message, snapshot, strategy)
    assert not result["passed"]
    assert any(item["code"] == "prohibited_language" and not item["passed"] for item in result["checks"])


def test_frozen_blocked_language_is_not_replaced_by_current_patterns(snapshot, strategy, monkeypatch):
    message = generate_message(snapshot, strategy)
    message["body"] += "\nThis is unhelpful."
    changed = load_content()
    changed["validation_patterns"]["prohibited_language"].append(r"\bunhelpful\b")
    monkeypatch.setattr(content_module, "load_content", lambda: deepcopy(changed))
    assert validate(message, snapshot, strategy)["passed"]
    current = deepcopy(snapshot)
    current.pop("content_policy")
    assert not validate(message, current, strategy)["passed"]


@pytest.mark.parametrize("content", [None, {}, [], {"version": "partial"}])
def test_invalid_frozen_content_never_falls_back(snapshot, strategy, monkeypatch, content):
    snapshot["content_policy"] = content

    def unexpected_fallback():
        raise AssertionError("Invalid frozen policy must not use current content")

    monkeypatch.setattr(content_module, "load_content", unexpected_fallback)
    with pytest.raises(ValueError):
        generate_message(snapshot, strategy)
    assert not validate_message("Subject", "Body", snapshot, strategy)["passed"]


@pytest.mark.parametrize("mutation", ["empty_patterns", "bad_regex", "unsafe_tone", "no_placeholder", "extra_placeholder", "format_placeholder", "missing_action", "bad_stage", "too_long_subject", "control_character"])
def test_malformed_configuration_is_rejected(mutation):
    content = load_content()
    if mutation == "empty_patterns":
        content["validation_patterns"]["prohibited_language"] = []
    elif mutation == "bad_regex":
        content["validation_patterns"]["prohibited_language"] = ["("]
    elif mutation == "unsafe_tone":
        content["tones"]["offer_or_reward"] = "threatening"
    elif mutation == "no_placeholder":
        content["greeting"] = "Hello,"
    elif mutation == "extra_placeholder":
        content["greeting"] = "Hello {customer_name} {postcode},"
    elif mutation == "format_placeholder":
        content["greeting"] = "Hello {customer_name:>9999},"
    elif mutation == "missing_action":
        del content["templates"]["billing_reminder"]["action"]
    elif mutation == "bad_stage":
        content["templates"]["billing_reminder"]["stages"]["03"] = {"subject": "Subject", "opening": "Opening"}
    elif mutation == "too_long_subject":
        content["templates"]["billing_reminder"]["stages"]["1"]["subject"] = "x" * 161
    else:
        content["signature"] = "Signature\nInjected line"
    with pytest.raises(ValueError):
        validate_content(content)


def test_additional_reminder_stage_can_render_without_code_changes(snapshot, strategy):
    snapshot["content_policy"]["templates"]["billing_reminder"]["stages"]["4"] = {
        "subject": "Let us help review your bill", "opening": "Please contact us if you need help reviewing your bill.",
    }
    strategy["reminder_stage"] = 4
    message = generate_message(snapshot, strategy)
    assert message["subject"] == "Let us help review your bill"
    assert validate(message, snapshot, strategy)["passed"]


def test_required_configuration_cannot_generate_prohibited_threat(snapshot, strategy):
    snapshot["content_policy"]["templates"]["billing_reminder"]["action"] = "Pay or we will disconnect your service."
    with pytest.raises(ValueError, match="failed validation"):
        generate_message(snapshot, strategy)


def test_bundled_content_is_json_serializable():
    policy = load_policy()
    assert json.loads(json.dumps(policy)) == policy
