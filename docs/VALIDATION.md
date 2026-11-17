# Prototype validation — 17 November 2026

The implemented prototype covers FR-01 through FR-10 with deterministic classification and controls, bounded OpenAI wording, exact-revision review, audit records, and mock delivery. Delivery never sends email or activates offers.

See [the submission evaluation](EVALUATION_REPORT.md) for measured classification/confusion results, 2,000 template checks, the six-call Astra sample, separately labelled automated usefulness judgments, and known limitations. Profile input, configurable content, frozen configuration, and five-criterion review scoring are now implemented.

## Current direct-service validation

After removing FastAPI, the reviewer calls `EngagementService` directly. The following checks were rerun on 17 November 2026 against the updated application:

| Check | Result |
|---|---|
| Complete component, service, UI, generator–critic and dataset suite | **423 passed**, no skips with `TEST_POSTGRES_URL` configured. Includes two PostgreSQL concurrency tests and the isolated migration test. |
| Service input and transaction boundary | Invalid identifiers/action inputs rejected before database access; failed profile/audit transactions rolled back; database errors returned safe messages; detached results serialized correctly. |
| Direct-service PostgreSQL smoke | Imported the 5,000-record fixture into a temporary schema. Offer, billing reminder and appreciation each completed template draft → automated test approval → mock delivery; retries returned one delivery. No model calls or changes to demo records. Temporary schema removed afterward. |
| Reviewer integration | Existing evaluate → draft → approve → edit → reapprove → deliver → download tests now exercise the direct service. Generator–critic feedback, attempt limits and approval gates passed scripted model tests. |
| Actual Streamlit script rendering | Overview, Review customer and Add profile each ran against the configured PostgreSQL database without script exceptions using the installed Streamlit script runner. This is not a visual browser inspection. |
| Application startup | Reviewer on 8501 and optional dataset explorer on 8502 returned healthy. The recorded legacy API process was stopped and port 8000 had no listener. PostgreSQL remained healthy. |
| Launcher syntax | Both startup and stop scripts passed `bash -n`. |

## Historical checks before the service refactor

The following results describe the earlier HTTP-based version. They are retained as historical evidence, not results from the current transport or generator–critic implementation.

| Check | Result |
|---|---|
| Combined component, integration, UI-transport and dataset suite | 385 passed, including the two PostgreSQL concurrency tests; no skips when `TEST_POSTGRES_URL` is set. |
| Dataset integrity and authored expectations | 5,000 synthetic customers across 25 scenarios; all pass independent arithmetic/reference checks and policy comparison. |
| PostgreSQL import and evaluation | 5,000 customers loaded into seven tables; all 5,000 evaluated successfully. |
| Actual OpenAI request | `gpt-6-astra`, reasoning `high`; a real offer draft passed validation, approval and mock delivery. |
| Actual running-API smoke test | Offer, billing reminder and appreciation completed; repeated delivery returned the same single delivery record. |
| Concurrent delivery, separate approved runs | Exactly one delivery; the competing run was blocked. |
| Concurrent retry, same revision | Both requests succeeded with exactly one delivery. |
| Reviewer transport tests | Evaluate → draft → approve → edit → reapprove → deliver → download passed using isolated fixtures and FastAPI TestClient. |
| Visual browser inspection | Earlier review was blocked by browser permission. During the policy-dashboard update, browser access succeeded: the four policy tabs, overview, and an existing held customer evaluation were inspected. Configuration saves were tested in isolated fixtures rather than changing live policies. |

## Classification and refusal coverage

### Policy dashboard and minimal reviewer update

The dashboard reads the actual configuration files and PostgreSQL catalogue. Isolated tests cover validation, stale-edit conflicts, audit failure rollback, new offer versions, frozen evaluation snapshots, read-only rendering, and explicit form submissions. The combined regression and dataset suite passed **448 tests**, including PostgreSQL integration checks. Eight additional dashboard interaction tests passed; the final UI-only rerun passed all **57 tests**. No live model requests were needed for this update.

At the dataset reference time, 800 customers select an offer, 800 a billing reminder, 400 appreciation, 2,400 hold, and 600 no outreach. Each customer receives all five independent segment results. These deliberately balanced scenarios do not estimate real telecom population rates or model accuracy.

Missing usage stays unknown rather than zero. Tests include stale data, incomplete billing evidence, disputes, permission denial/uncertainty, previous redemptions, expired offers, contact limits, duplicate logical actions, and three reminder stages. Source timestamps and current permissions are checked again before delivery.

## Groundedness, safety and review

Verified customer greetings, invoice amounts/dates, offer details/terms/expiry, and support or activation statements are rendered by code. The generator receives objective/tone and, on repair attempts, critique feedback. The independent critic receives an anonymized draft and its selected verified facts. Full profiles, customer feedback and contact details are excluded. Refusal, incomplete output, API errors and failed validation produce explicit failures without silently substituting a template draft. Earlier validation results in this document predate the generator–critic extension; its behavior and focused checks are described in [the architecture](ARCHITECTURE.md).

Tests exercise altered/invented amounts, changed terms, unsupported links and claims, prohibited threats, sensitive identifiers, malformed content, stale revisions, missing approval, post-approval tampering, policy changes, and payment/opt-out/dispute events after approval. Invalid edits can be corrected in a new revision. Review decisions and feedback are persisted; prior approval cannot authorize the replacement revision.

Lexical checks are conservative heuristics, not a proof of arbitrary prose correctness. Human review remains required. Automated test feedback and the smoke-test approvals are explicitly labelled synthetic tests; no independent human usefulness study was performed. The implemented rubric supports relevance, clarity, personalization, call to action and tone. The submission includes actual automated assessments with exact-message hashes; human scores remain uncollected. These categories are reported separately.

## Reproduce

```sh
TEST_POSTGRES_URL=postgresql+psycopg2://telecom@127.0.0.1:55432/telecom_demo \
  .venv/bin/python -m pytest tests datasets/telecom_5000/test_dataset.py -q

# Direct-service smoke: uses the configured database and offline templates, no model calls.
.venv/bin/python scripts/smoke_test.py

# Opt in to generator/critic calls and mock delivery on fictional customers.
.venv/bin/python scripts/smoke_test.py --openai
```

The direct-service report is saved at `.local/service_smoke_report.json`, or `.local/service_openai_smoke_report.json` with `--openai`. Use `--output path.json` to select a report path. The older `.local/live_smoke_report.json` is historical evidence and is not overwritten by this script. Smoke commands use the configured database and retain their test drafts, automated approvals and mock delivery records there; the refactor check above used a temporary schema to keep demo records unchanged. Repeating the smoke test uses an eligible synthetic customer; it does not contact real recipients. The dataset and its saved expectations become stale for current-time decisions; regenerate a fresh snapshot and import it into a separate empty database for future demonstrations.

## Scope limits

This is a local, single-reviewer prototype. Production authentication, authoritative source integration, policy approval, migrations, operational monitoring, a delivery outbox/provider, immutable audit enforcement, and privacy lifecycle controls remain roadmap work documented in the README. No production or real-customer readiness is claimed.
