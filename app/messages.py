"""Fixed verified facts with optional, bounded OpenAI courtesy wording."""

from datetime import datetime, timezone
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import re

from pydantic import BaseModel, ConfigDict, Field

from app.content import content_for

OBJECTIVES = {"billing_reminder", "offer_or_reward", "appreciation_without_offer"}


class CourtesyCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opening: str = Field(min_length=1, max_length=240)
    closing: str = Field(min_length=1, max_length=240)


def _line(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000 or any(ord(c) < 32 for c in value):
        raise ValueError(f"Verified {label} is missing or invalid.")
    return value.strip()


def message_spec(snapshot, strategy):
    """The same fact strings are used by rendering and edit validation."""
    if not isinstance(snapshot, dict) or not isinstance(strategy, dict) or strategy.get("objective") not in OBJECTIVES:
        raise ValueError("This strategy does not permit message generation.")
    context = snapshot.get("context")
    if not isinstance(context, dict):
        raise ValueError("Verified customer context is missing.")
    content = content_for(snapshot)
    name = _line(snapshot.get("customer_name"), "customer name")
    greeting = content["greeting"].format(customer_name=name)
    signature = content["signature"]
    objective = strategy["objective"]
    template = content["templates"][objective]
    action, closing = template["action"], template["closing"]
    facts = []
    if objective == "billing_reminder":
        invoices = context.get("invoices")
        matches = [i for i in invoices if isinstance(i, dict) and i.get("id") == strategy.get("invoice_id")] if isinstance(invoices, list) else []
        if len(matches) != 1:
            raise ValueError("The selected invoice cannot be verified.")
        invoice = matches[0]
        currency = _line(context.get("currency"), "currency")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("Verified currency is invalid.")
        try:
            if not isinstance(invoice.get("outstanding"), str):
                raise ValueError()
            amount = Decimal(invoice["outstanding"])
            if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
                raise ValueError()
            due = _line(invoice.get("due_date"), "invoice due date")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
                raise ValueError()
            datetime.strptime(due, "%Y-%m-%d")
        except (ValueError, InvalidOperation):
            raise ValueError("The selected invoice amount or due date is invalid.") from None
        stage = strategy.get("reminder_stage")
        if type(stage) is not int or stage < 1 or str(stage) not in template["stages"]:
            raise ValueError("The selected reminder stage is invalid.")
        stage_template = template["stages"][str(stage)]
        subject, opening = stage_template["subject"], stage_template["opening"]
        facts = [f"Invoice: {_line(invoice.get('id'), 'invoice identifier')}",
                 f"Outstanding amount: {currency} {amount:.2f}", f"Due date: {due}"]
    elif objective == "offer_or_reward":
        offers = snapshot.get("offers")
        matches = [o for o in offers if isinstance(o, dict) and o.get("id") == strategy.get("offer_id")] if isinstance(offers, list) else []
        if len(matches) != 1 or matches[0].get("active") is not True:
            raise ValueError("The selected approved offer cannot be verified.")
        offer = matches[0]
        details = offer.get("details")
        if not isinstance(details, dict):
            raise ValueError("The selected offer terms are missing.")
        try:
            expires = datetime.fromisoformat(_line(details.get("expires_at"), "offer expiry").replace("Z", "+00:00"))
            if expires.tzinfo is None or expires.utcoffset() is None:
                raise ValueError()
            expiry = expires.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except (ValueError, OverflowError):
            raise ValueError("The selected offer expiry is invalid.") from None
        subject, opening = template["subject"], template["opening"]
        facts = [f"Offer: {_line(details.get('title'), 'offer title')}",
                 f"Offer code: {_line(offer.get('code'), 'offer code')}",
                 f"Benefit: {_line(details.get('value_text'), 'offer benefit')}",
                 f"Terms: {_line(details.get('terms'), 'offer terms')}", f"Offer expires: {expiry}"]
    else:
        subject, opening = template["subject"], template["opening"]
    return {"subject": subject, "greeting": greeting, "facts": facts, "action": action,
            "signature": signature, "opening": opening, "closing": closing, "content_policy": content}


def _model_copy(strategy, settings, content, feedback=None):
    if settings is None:
        from app.config import Settings
        settings = Settings.from_env()
    if not settings.openai_configured:
        raise ValueError("OpenAI generation requires a server-side API key and an explicitly configured model.")
    request = {"purpose": strategy["objective"], "tone": strategy.get("tone")}
    if feedback:
        request["revision_feedback"] = feedback
    try:
        from openai import OpenAI
        with OpenAI(api_key=settings.openai_api_key, timeout=45.0, max_retries=0) as client:
            response = client.responses.parse(
                model=settings.openai_model,
                store=False,
                max_output_tokens=4096,
                reasoning={"effort": settings.openai_reasoning_effort},
                text_format=CourtesyCopy,
                input=[
                    {"role": "system", "content": content["generation"]["system_prompt"]},
                    {"role": "user", "content": json.dumps(request)},
                ],
            )
    except Exception:
        # Do not expose SDK request bodies, credentials, or customer information.
        raise ValueError("OpenAI generation failed. Check the server configuration or retry; no fallback draft was created.") from None
    for output in getattr(response, "output", []) or []:
        for item in getattr(output, "content", []) or []:
            if getattr(item, "type", None) == "refusal":
                raise ValueError("OpenAI declined this generation request; no draft was created.")
    if getattr(response, "status", None) != "completed":
        raise ValueError("OpenAI generation was incomplete; no draft was created.")
    try:
        copy = CourtesyCopy.model_validate(response.output_parsed)
        if any("\n" in value or "\r" in value for value in (copy.opening, copy.closing)):
            raise ValueError()
    except Exception:
        raise ValueError("OpenAI did not return valid bounded courtesy wording; no draft was created.") from None
    return copy.opening.strip(), copy.closing.strip(), getattr(response, "model", None) or settings.openai_model


def generate_message(snapshot, strategy, provider="template", settings=None, *, feedback=None, allow_invalid=False):
    if provider not in ("template", "openai"):
        raise ValueError("Unknown generation provider. Choose template or openai.")
    if not isinstance(snapshot, dict):
        raise ValueError("Verified customer context is missing.")
    snapshot = {**snapshot, "content_policy": deepcopy(content_for(snapshot))}
    spec = message_spec(snapshot, strategy)
    content = spec["content_policy"]
    opening, closing, model = spec["opening"], spec["closing"], "none"
    if provider == "openai":
        opening, closing, model = _model_copy(strategy, settings, content, feedback)
    paragraphs = [spec["greeting"], opening]
    if spec["facts"]:
        paragraphs.append("\n".join(spec["facts"]))
    paragraphs.extend([spec["action"], closing, spec["signature"]])
    body = "\n\n".join(paragraphs)
    from app.validation import validate_message
    findings = validate_message(spec["subject"], body, snapshot, strategy)
    if not findings["passed"] and not allow_invalid:
        raise ValueError("Generated wording failed validation; no draft was created. Choose template generation or retry.")
    return {"subject": spec["subject"], "body": body, "provider": provider, "model_version": model,
            "prompt_version": content["generation"]["prompt_version"] if provider == "openai" else "none",
            "template_version": content["version"], "content_version": content["version"],
            "courtesy_copy": {"opening": opening, "closing": closing}}
