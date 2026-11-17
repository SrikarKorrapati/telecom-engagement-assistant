"""Current message content and guardrail configuration, frozen with each run."""

import json
import re
from string import Formatter

from app.paths import policy_directory


RESPECTFUL_TONES = frozenset({"courteous", "warm", "supportive", "neutral", "direct", "firm"})
PATTERN_GROUPS = ("prohibited_language", "unsupported_claims", "unsupported_dates", "sensitive_data")


def _text(value, name, limit=1500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"Content configuration: {name} must be nonempty bounded plain text.")


def validate_content(content):
    """Reject an incomplete configuration before it can govern a draft."""
    try:
        if not isinstance(content, dict):
            raise ValueError("Content configuration must be an object.")
        _text(content["version"], "version", 80)
        _text(content["label"], "label", 200)
        if content["example_policy"] is not True:
            raise ValueError("Content configuration must be labelled as an example policy.")
        _text(content["greeting"], "greeting", 160)
        fields = [field for _, field, _, _ in Formatter().parse(content["greeting"]) if field is not None]
        if fields != ["customer_name"] or "{customer_name}" not in content["greeting"]:
            raise ValueError("Content greeting requires exactly one plain {customer_name} placeholder.")
        _text(content["signature"], "signature", 160)
        for objective in ("offer_or_reward", "appreciation_without_offer"):
            if content["tones"][objective] not in RESPECTFUL_TONES:
                raise ValueError("Content tones must be one of the supported respectful tones.")
        templates = content["templates"]
        for objective in ("billing_reminder", "offer_or_reward", "appreciation_without_offer"):
            _text(templates[objective]["action"], f"{objective} required statement")
            _text(templates[objective]["closing"], f"{objective} closing", 240)
            if objective != "billing_reminder":
                _text(templates[objective]["subject"], f"{objective} subject", 160)
                _text(templates[objective]["opening"], f"{objective} opening", 240)
        stages = templates["billing_reminder"]["stages"]
        if not isinstance(stages, dict) or not stages:
            raise ValueError("Content requires at least one reminder-stage template.")
        for key, values in stages.items():
            if not isinstance(key, str) or not re.fullmatch(r"[1-9]\d*", key) or not isinstance(values, dict):
                raise ValueError("Content reminder stages require positive integer string keys.")
            _text(values["subject"], f"stage {key} subject", 160)
            _text(values["opening"], f"stage {key} opening", 240)
        _text(content["generation"]["prompt_version"], "prompt version", 80)
        _text(content["generation"]["system_prompt"], "system prompt", 3000)
        _text(content["critique"]["prompt_version"], "critique prompt version", 80)
        _text(content["critique"]["system_prompt"], "critique system prompt", 5000)
        attempts = content["critique"]["max_generation_attempts"]
        if type(attempts) is not int or not 1 <= attempts <= 3:
            raise ValueError("Content requires between one and three generation attempts.")
        for group in PATTERN_GROUPS:
            patterns = content["validation_patterns"][group]
            if not isinstance(patterns, list) or not patterns:
                raise ValueError(f"Content requires a nonempty {group} pattern list.")
            for pattern in patterns:
                _text(pattern, f"{group} pattern", 1500)
                re.compile(pattern, re.I | re.S)
    except (KeyError, TypeError, re.error) as error:
        raise ValueError("Content configuration is missing required fields or contains invalid patterns.") from error
    return content


def load_content():
    path = policy_directory() / "content.json"
    try:
        return validate_content(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Content configuration could not be loaded.") from error


def content_for(snapshot):
    # An explicitly present invalid snapshot is never replaced with current policy.
    return validate_content(snapshot["content_policy"]) if "content_policy" in snapshot else load_content()
