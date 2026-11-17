"""Conservative prototype checks; semantic completeness still requires human review."""

import re

from app.messages import message_spec


def validate_message(subject, body, snapshot, strategy):
    checks = []

    def check(code, passed, detail):
        checks.append({"code": code, "passed": bool(passed), "detail": detail})

    valid_text = isinstance(subject, str) and isinstance(body, str)
    check("message_shape", valid_text and 1 <= len(subject.strip()) <= 160 and 1 <= len(body.strip()) <= 6000,
          "Subject must be 1–160 characters and body 1–6000 characters.")
    if not valid_text:
        return {"passed": False, "checks": checks}
    check("plain_text", not re.search(r"[\r\n\x00-\x1f]", subject) and not re.search(r"[\x00-\x08\x0b-\x1f]|<[^>]+>", body),
          "Use plain text, a single-line subject, and no control characters or HTML.")
    try:
        spec = message_spec(snapshot, strategy)
    except (ValueError, TypeError, KeyError):
        check("verified_context", False, "The selected strategy or its required facts cannot be verified.")
        return {"passed": False, "checks": checks}
    check("verified_context", True, "Required facts are available from the selected snapshot.")
    patterns = spec["content_policy"]["validation_patterns"]

    def matches(group, value):
        return any(re.search(pattern, value, re.I | re.S) for pattern in patterns[group])
    lines = body.splitlines()
    required = [spec["greeting"], *spec["facts"], spec["action"], spec["signature"]]
    check("required_facts", all(lines.count(line) == 1 for line in required),
          "Keep the verified greeting, fact lines, support/activation statement and signature exactly once, unchanged.")
    # Restrict all additional numeric/date/currency claims, including a changed subject.
    free_text = "\n".join(line for line in lines if line not in required)
    free_text += "\n" + ("" if subject == spec["subject"] else subject)
    check("unsupported_numbers", not re.search(r"\d", free_text),
          "Numbers may appear only in the exact verified fact lines.")
    check("unsupported_amounts", not re.search(r"[$₹€£]|\b(?:INR|USD|EUR|GBP|Rs\.?|rupees?|dollars?|pounds?)\b", free_text, re.I),
          "Currency claims may appear only in the exact verified fact lines.")
    check("unsupported_dates", not matches("unsupported_dates", free_text),
          "Dates and deadlines may appear only in the exact verified fact lines.")
    whole = subject + "\n" + body
    check("unapproved_links", not re.search(r"https?\s*:|www\.|mailto\s*:|[\w.+-]+@[\w.-]+|\b[a-z0-9-]+\.[a-z]{2,}(?:\b|/)", whole, re.I),
          "This prototype has no approved links or email addresses in message content.")
    check("prohibited_language", not matches("prohibited_language", whole),
          "Threats, shaming, unapproved penalties and service-disconnection claims are prohibited.")
    check("unsupported_claims", not matches("unsupported_claims", free_text),
          "Additional unverified benefits, promises, payment-status claims or charges are not permitted.")
    sensitive_values = []
    context = snapshot.get("context", {})
    for value in (snapshot.get("customer_id"), snapshot.get("customer_email"), context.get("account_id"), context.get("subscription_id"), context.get("phone")):
        if isinstance(value, str) and len(value) > 3:
            sensitive_values.append(value)
    sensitive = any(value.lower() in whole.lower() for value in sensitive_values)
    sensitive = sensitive or matches("sensitive_data", whole)
    check("sensitive_data", not sensitive,
          "Do not include account/subscription identifiers, contact details, secrets or payment credentials.")
    check("human_review_required", True,
          "These are conservative heuristic checks, not proof of semantic correctness; a human must review every exact revision.")
    return {"passed": all(item["passed"] for item in checks), "checks": checks}
