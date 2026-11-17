"""Independent LLM critique of a grounded draft; never authorizes delivery."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.messages import message_spec


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["factuality", "consistency", "tone", "privacy", "policy", "clarity"]
    severity: Literal["blocking", "suggestion"]
    quote: str = Field(max_length=300)
    explanation: str = Field(min_length=1, max_length=600)
    suggested_fix: str = Field(min_length=1, max_length=500)


class CritiqueResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["pass", "revise"]
    summary: str = Field(min_length=1, max_length=800)
    findings: list[Finding] = Field(max_length=8)

    @model_validator(mode="after")
    def consistent_verdict(self):
        blocking = any(item.severity == "blocking" for item in self.findings)
        if (self.verdict == "revise") != blocking:
            raise ValueError("A revise verdict requires blocking findings; a pass cannot contain them.")
        return self


def fingerprint(subject, body, snapshot, strategy):
    # Bind the assessment to the exact text, verified facts, tone and frozen prompt.
    value = {"subject": subject, "body": body, "spec": message_spec(snapshot, strategy),
             "purpose": strategy["objective"], "tone": strategy.get("tone")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def critique_message(subject, body, snapshot, strategy, settings):
    """Return a bounded assessment. Call only after the deterministic checks pass."""
    from app.validation import validate_message

    if not validate_message(subject, body, snapshot, strategy)["passed"]:
        raise ValueError("Correct the deterministic validation failures before requesting AI critique.")
    if settings is None or not settings.openai_configured:
        raise ValueError("AI critique requires a server-side API key and configured model.")
    spec = message_spec(snapshot, strategy)
    config = spec["content_policy"]["critique"]
    # Only the draft and selected message facts cross this boundary. Account context,
    # feedback, contact information and the rest of the offer catalogue stay local.
    name = snapshot["customer_name"]

    def anonymize(value):
        return value.replace(name, "[customer]")

    payload = {
        "purpose": strategy["objective"], "tone": strategy.get("tone"),
        "verified_message": {
            "required_lines": [anonymize(line) for line in
                               [spec["greeting"], *spec["facts"], spec["action"], spec["signature"]]]},
        "draft": {"subject": anonymize(subject), "body": anonymize(body)},
    }
    try:
        from openai import OpenAI
        with OpenAI(api_key=settings.openai_api_key, timeout=45.0, max_retries=0) as client:
            response = client.responses.parse(
                model=settings.openai_model, store=False, max_output_tokens=4096,
                reasoning={"effort": settings.openai_reasoning_effort},
                text_format=CritiqueResult,
                input=[{"role": "system", "content": config["system_prompt"]},
                       {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
    except Exception:
        raise ValueError("AI critique failed. Retry critique after checking the backend configuration; approval remains blocked.") from None
    if any(getattr(item, "type", None) == "refusal"
           for output in getattr(response, "output", []) or []
           for item in getattr(output, "content", []) or []):
        raise ValueError("AI critique was declined; approval remains blocked.")
    if getattr(response, "status", None) != "completed":
        raise ValueError("AI critique was incomplete; approval remains blocked.")
    try:
        result = CritiqueResult.model_validate(response.output_parsed)
    except Exception:
        raise ValueError("AI critique returned an invalid assessment; approval remains blocked.") from None
    return {"status": "passed" if result.verdict == "pass" else "changes_requested",
            "provider": "openai", "model_version": getattr(response, "model", None) or settings.openai_model,
            "prompt_version": config["prompt_version"],
            "reasoning_effort": settings.openai_reasoning_effort,
            "input_hash": fingerprint(subject, body, snapshot, strategy), **result.model_dump()}
