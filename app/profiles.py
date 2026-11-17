"""Explicit synthetic profile input. Unknown evidence stays null; extra fields are rejected."""

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator


Identifier = Annotated[StrictStr, Field(min_length=1, max_length=120)]
Money = Annotated[StrictStr, Field(pattern=r"^\d{1,12}(?:\.\d{1,2})?$")]
Days = Annotated[StrictInt, Field(ge=0, le=366)]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(Evidence):
    prior_active_days: Days | None = None
    current_active_days: Days | None = None
    prior_complete: StrictBool | None = None
    current_complete: StrictBool | None = None
    prior_days: Days | None = None
    current_days: Days | None = None
    prior_start: date | None = None
    prior_end: date | None = None
    current_start: date | None = None
    current_end: date | None = None
    known_outage: StrictBool | None = None


class Invoice(Evidence):
    id: Identifier
    period_end: date | None = None
    due_date: date | None = None
    amount: Money | None = None
    outstanding: Money | None = None
    paid_full_at: AwareDatetime | date | None = None
    status: Literal["open", "paid", "overdue", "partially_paid", "disputed"] | None = None
    disputed: StrictBool | None = None


class Permissions(Evidence):
    billing: Literal["allowed", "denied"] | None = None
    marketing: Literal["allowed", "denied"] | None = None


class Reminder(Evidence):
    invoice_id: Identifier
    stage: Annotated[StrictInt, Field(ge=1, le=20)]
    sent_at: AwareDatetime


class Redemption(Evidence):
    offer_code: Identifier
    redeemed_at: AwareDatetime


class Feedback(Evidence):
    channel: Literal["support_chat", "complaint", "survey", "email"]
    text: Annotated[StrictStr, Field(max_length=4000)]
    occurred_at: AwareDatetime


class ProfileContext(Evidence):
    synthetic: Literal[True]
    account_id: Identifier | None = None
    subscription_id: Identifier | None = None
    billing_timezone: Literal["Asia/Kolkata"] = "Asia/Kolkata"
    active: StrictBool | None = None
    plan_code: Identifier | None = None
    tenure_months: Annotated[StrictInt, Field(ge=0, le=1200)] | None = None
    currency: Literal["INR"] = "INR"
    source_as_of: AwareDatetime | None = None
    usage: Usage = Field(default_factory=Usage)
    invoices: Annotated[list[Invoice], Field(max_length=120)] | None = None
    permissions: Permissions = Field(default_factory=Permissions)
    open_dispute: StrictBool | None = None
    reminder_history: Annotated[list[Reminder], Field(max_length=120)] | None = None
    redemptions: Annotated[list[Redemption], Field(max_length=120)] | None = None
    feedback: Annotated[list[Feedback], Field(max_length=40)] = Field(default_factory=list)

    @field_validator("synthetic", mode="before")
    @classmethod
    def explicitly_synthetic(cls, value):
        if value is not True:
            raise ValueError("synthetic must be the boolean true.")
        return value


class ProfileInput(Evidence):
    name: Annotated[StrictStr, Field(min_length=1, max_length=160)]
    email: Annotated[StrictStr, Field(max_length=320,
        pattern=r"^[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)*(?:example\.com|example\.test|test|invalid)$")]
    scenario: Annotated[StrictStr, Field(min_length=1, max_length=240)] = "Manual synthetic profile"
    context: ProfileContext

    @field_validator("name", "scenario")
    @classmethod
    def plain_text(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Use nonempty, single-line text.")
        return value.strip()


def example_profile():
    from app.models import utcnow, new_id
    from app.seed import _context
    key = "manual-" + new_id()[:8]
    return {"name": "Synthetic Example", "email": key + "@example.com",
            "scenario": "Manual synthetic profile", "context": _context(utcnow(), key)}
