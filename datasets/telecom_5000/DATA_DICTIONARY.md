# Data dictionary

JSONL files contain one UTF-8 JSON object per line. Every identifier/reference is deterministic for the configured seed. UUIDs are strings. Timestamps are timezone-aware ISO 8601 in UTC; dates use `YYYY-MM-DD`. Billing-date comparisons use `Asia/Kolkata`. Money is a nonnegative exact decimal string with two decimal places, in INR. `null` means intentionally unknown; it does not mean false, zero, or an empty history.

## customers.jsonl

| Field | Type | Meaning |
|---|---|---|
| `id` | UUID string | Unique fictional customer identifier; joins evaluation contexts and labels. |
| `name` | string | `Synthetic Customer NNNNN`; no real name. |
| `email` | string | Unique fictional contact at reserved `example.com`. |
| `scenario` | string | Human-readable fixture category; display metadata, not a classification input. |
| `version` | positive integer | Current synthetic customer snapshot version, initially 1. |
| `context` | object | Selected account/subscription facts described below. |

Each record represents one selected account/subscription context. It does not claim that real customers have only one mobile line. Account and subscription IDs are unique across this fixture, simplifying demonstrator imports.

### Customer context

| Field | Type | Meaning |
|---|---|---|
| `synthetic` | boolean | Always `true`; required fixture marker. |
| `account_id`, `subscription_id` | strings | Unique fictional selected-account and line references. |
| `billing_timezone` | string | Always `Asia/Kolkata`. |
| `active` | boolean | Whether the selected subscription is active. |
| `plan_code` | string | `POSTPAID_499`, `POSTPAID_799`, or `POSTPAID_EXPIRED_499` for expired-offer eligibility fixtures. |
| `tenure_months` | integer | Fictional subscription tenure; covers below/at/above 12-month example threshold. |
| `currency` | string | Always `INR`. |
| `source_as_of` | UTC timestamp | Time represented by the source snapshot; stale fixtures exceed 48 hours of age. |
| `usage` | object | Two adjacent 30-day usage windows. |
| `invoices` | array or null | Paid historical cycles and, where applicable, the current outstanding invoice. Null intentionally represents missing invoice evidence. |
| `permissions.billing` | string | `allowed` or `denied`; permission for billing outreach in the example policy. |
| `permissions.marketing` | string or null | `allowed`, `denied`, or unknown (`null`). |
| `open_dispute` | boolean | Explicit dispute state; disputed customers are held. |
| `reminder_history` | array | Previously sent synthetic reminders referencing invoice IDs; empty means known none. |
| `redemptions` | array or null | Known prior offer redemptions; null is unknown history. |
| `feedback` | four-element array | One entry per support chat, complaint, survey, and email channel. |

### Usage object

| Field | Type | Meaning |
|---|---|---|
| `prior_active_days`, `current_active_days` | integer or null | Days with activity, 0–30. Incomplete current-window fixtures use null, never zero. |
| `prior_complete`, `current_complete` | boolean | Whether the full window was observed. |
| `prior_days`, `current_days` | integer | Nominal window length, always 30 in this package. Completeness is separate. |
| `prior_start`, `prior_end`, `current_start`, `current_end` | ISO dates | Inclusive adjacent windows ending before the snapshot's local source date. |
| `known_outage` | boolean | Explicit known outage; usage interpretation is held. |

Example decline threshold: prior activity at least 10 days, current activity greater than zero and at most 60% of prior, with both windows complete. Dormancy requires an active subscription and a complete current window with exactly zero active days. These thresholds are illustrative.

### Invoice objects

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Unique invoice ID within and across customers; reminder-history FK target. |
| `period_end` | ISO date | Billing period end, no later than due date or source date. Historical cycles end on preceding month ends. An outstanding adjustment invoice can have a different period end. |
| `due_date` | ISO date | Payment deadline. Overdue means strictly before the local evaluation date. |
| `amount` | decimal string | Total billed amount. |
| `outstanding` | decimal string | Unpaid balance, between zero and total; derived amount paid is `amount - outstanding`. |
| `paid_full_at` | UTC timestamp or null | Full-payment timestamp, present only for fully paid invoices. It is never after the source snapshot or before the billing period end. |
| `status` | string | `paid`, `overdue`, or `partially_paid` in these fixtures. |
| `disputed` | boolean | Explicit invoice dispute status. |

Three complete historical monthly cycles are supplied when invoice evidence is known. Deliberately inconsistent-payment scenarios have settled historical invoices paid after the due date; arithmetic remains valid. Billing scenarios also have a current outstanding invoice, sometimes partially paid. `CURRENT` in its identifier means currently outstanding, not necessarily a period in the current calendar month. Late historical payments ensure deterministic non-consistent labels even when the reference date crosses a month boundary.

### Histories and feedback

| Object/field | Type | Meaning |
|---|---|---|
| `reminder_history[].invoice_id` | string | References an invoice in the same customer context. |
| `reminder_history[].stage` | integer 1–3 | Previously sent stage; constrained by age of the overdue invoice at send time. |
| `reminder_history[].sent_at` | UTC timestamp | Historical mock send time; matched by an evaluation delivery summary. |
| `redemptions[].offer_code` | string | References an offer code in the catalogue. |
| `redemptions[].redeemed_at` | UTC timestamp | Prior redemption; recent-redemption cases fall within 90 days. |
| `feedback[].channel` | enum string | `support_chat`, `complaint`, `survey`, `email`. |
| `feedback[].text` | string | Clearly prefixed synthetic text. No sentiment ground truth is provided. |
| `feedback[].occurred_at` | UTC timestamp | Feedback time at or before source snapshot. |

## offers.json

Array of versioned catalogue entries. Each has `id` (UUID), `code` (`LOYALTY_2GB`), `version` (positive integer), `active` (boolean), and `details` (object).

| `details` field | Type | Meaning |
|---|---|---|
| `synthetic` | boolean | Always true. |
| `title`, `value_text`, `terms` | strings | Fictional approved wording: 2GB at no additional cost, valid 30 days after activation, at most once in 90 days. |
| `eligible_plans` | string array | Exact eligible plan codes. |
| `expires_at` | UTC timestamp | Eligibility expires at this time. |
| `redemption_days` | integer | 90-day illustrative redemption exclusion. |
| `channel` | string | `email`. |

Version 1 is active but expired, and only matches the expired-plan fixture. Version 2 is active, unexpired, and matches standard plans. Version 3 is unexpired but inactive and must never be selected. Code/version pairs and IDs are unique. Redemptions reference the code across versions.

## evaluation_contexts.jsonl

| Field | Type | Meaning |
|---|---|---|
| `customer_id` | UUID string | FK to `customers.id`. |
| `as_of` | UTC timestamp | Reference time used for every expectation in the package. |
| `recent_deliveries` | array | Synthetic history required for contact-frequency and duplicate checks; includes known reminder sends. |
| `recent_deliveries[].created_at` | UTC timestamp | Fictional prior delivery time. |
| `recent_deliveries[].purpose` | string | `billing` or `marketing`. |
| `recent_deliveries[].idempotency_key` | string | Logical outreach identity, prefixed by customer UUID. |

These are policy-input summaries, not persisted message approvals or real delivery receipts. Duplicate fixtures reuse the exact current-offer action key; frequency fixtures use a different prior-campaign key within seven days.

## expected_labels.jsonl

Separate benchmark data; never an application input. `customer_id`, `scenario`, and `as_of` identify the fixture. `segments` maps all five independent segment names to `match`, `no_match`, or `insufficient_evidence`. `strategy_objective` is one of `offer_or_reward`, `appreciation_without_offer`, `billing_reminder`, `hold`, `no_outreach`. `reason_code` is the expected policy reason. `reminder_stage` is 1–3 for a selected reminder, otherwise null. `offer_id` references the selected catalogue version for an offer, otherwise null.

Expectations are authored in the scenario definitions, independently of the application evaluator. They are applicable only to the documented prototype-v1 thresholds at the manifest reference time. They are policy regression expectations, not human ground truth about real customers.
