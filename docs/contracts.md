# Prototype implementation contract

Seven PostgreSQL tables. Synthetic telecom data, deterministic independent segment evaluation, ordered strategy selection, exact-revision human approval, mock delivery only. No sentiment-analysis workflow. Never contact a real customer or change billing/offers. Current demo context represents a selected account/subscription, with their explicit identifiers; it is not a claim that a real customer has only one line. Missing/stale evidence is unknown and must never silently become a negative rule result.

## Ownership

- Storage: app/models.py, app/db.py, app/seed.py, tests/test_storage.py.
- Policy: app/policy.py, app/policies/policy.json, tests/test_policy.py.
- UI: ui/streamlit_app.py, ui/styles.css.
- Root: configuration, workflow, validation, model adapter, Python service, packaging and integration tests.

## Storage (SQLAlchemy 2)

All IDs are UUID strings (String(36), with helper new_id()). Datetimes timezone-aware UTC. JSON uses JSONB in PostgreSQL and JSON in SQLite tests. Python field names below are the shared contract. Foreign keys and unique constraints enforce identity. No hidden session commits in model helpers.

- Customer / customers: id, name, email, scenario, context (JSON), version (integer, default 1), updated_at.
- Offer / offers: id, code, version (integer), details (JSON), active (boolean). Unique(code,version).
- EngagementRun / engagement_runs: id, customer_id FK, created_at, state, snapshot (JSON), policy_snapshot (JSON), policy_hash, segments (JSON list), strategy (JSON), latest_revision_id (nullable string), blocked_reasons (JSON list).
- MessageRevision / message_revisions: id, run_id FK, revision_no, subject, body, content_hash, validation (JSON), provider, model_version, prompt_version, template_version, created_at. Unique(run_id,revision_no).
- Review / reviews: id, message_id FK, action (approve/reject/edit), feedback, actor, content_hash, created_at.
- DeliveryJob / delivery_jobs: id, run_id FK, message_id FK, approval_review_id FK, idempotency_key UNIQUE, status, result (JSON), created_at, delivered_at nullable.
- AuditEvent / audit_events: id, run_id nullable FK, event_type, actor, details (JSON), created_at. Application append-only.

Export Base and model classes from app.models. app.db exposes make_engine(url), make_session_factory(engine), initialize_database(engine). SQLite tests enable FK constraints; PostgreSQL is the intended application backend. Initialization may use Base.metadata.create_all for this explicitly prototype application.

app.seed exposes seed_demo(session, now=None), idempotently adding scenarios and one versioned offer without resetting existing edits. Return inserted customer count. Store an applied offer catalogue separately, and mark all data synthetic. Do not seed on every request.

## Customer context shape

All dates are ISO strings, money decimal strings, missing data null. time fields ISO UTC; due dates ISO dates, billing_timezone Asia/Kolkata. `now` is current timezone-aware UTC.

```
{
  "account_id": "ACC-...", "subscription_id": "SUB-...",
  "active": true, "plan_code": "POSTPAID_499", "tenure_months": 18,
  "currency": "INR", "source_as_of": "ISO timestamp",
  "usage": {"prior_active_days": 20, "current_active_days": 10,
            "prior_complete": true, "current_complete": true,
            "prior_days": 30, "current_days": 30, "known_outage": false},
  "invoices": [
    {"id":"INV-...", "period_end":"ISO date", "due_date":"ISO date",
     "amount":"700.00", "outstanding":"700.00", "paid_full_at":null,
     "status":"open", "disputed":false}
  ],
  "permissions": {"billing":"allowed", "marketing":"allowed"},
  "open_dispute": false,
  "reminder_history": [
    {"invoice_id":"INV-...", "stage":1, "sent_at":"ISO timestamp"}
  ],
  "redemptions": [{"offer_code":"LOYALTY_2GB", "redeemed_at":"ISO timestamp"}],
  "feedback": [{"channel":"support_chat|complaint|survey|email", "text":"...", "occurred_at":"ISO timestamp"}]
}
```

Offer.details: title, eligible_plans (list), value_text, terms, expires_at (ISO timestamp), redemption_days (90), channel (email). Offer code LOYALTY_2GB; free 2GB add-on valid 30 days from activation. Synthetic example only.

## Policy pure-function contract

app.policy exports load_policy() -> dict, evaluate(snapshot: dict, policy: dict | None = None) -> dict.

Root constructs snapshot: {customer_id, customer_name, customer_email, customer_version, as_of, context: <above>, offers: [{id,code,version,active,details}], recent_deliveries: [{created_at,purpose,idempotency_key}]}.

evaluate returns {segments:[{segment,status,reason_code,reason,evidence,evidence_quality}], strategy:{objective,purpose,tone,reason_code,reason,invoice_id,offer_id,reminder_stage,blocked_reasons}, blocked_reasons:[str]}.

Segment codes: consistent, loyalty_candidate, declining_activity, dormant, payment_reminder. Statuses: match, no_match, insufficient_evidence. All five are evaluated independently. Consistent means the latest completed bill (period_end before the current calendar month) was fully paid by its due date. Loyalty means active with tenure >=12 months. Decline means 0 < current active days < previous active days in complete 30-day windows. Dormant means active with zero current usage. Billing means a verified positive unpaid balance and due_date before the billing-local evaluation date.

Selection order is hold, billing, eligible offer, appreciation, no outreach. Billing advances through configured stages only after matching reminder/delivery history, overdue thresholds and minimum intervals; each stage has its own duplicate key. Offer eligibility and basic evidence, permission, dispute, outage, frequency and duplicate checks remain. Appreciation requires active status, a paid-on-time latest bill, and positive non-declining usage. Details are in [the simple rules](SIMPLE_RULES.md).

Policy/content are updated in place with fixed identity names. Content hashes and frozen snapshots preserve consistency. Changing the policy name cannot reset monthly appreciation deduplication; historical delivery keys remain recognized. Old snapshots and historical reports are retained as evidence, not selectable active policies.

## Python service/UI contract

`app.service.EngagementService` is the application boundary. The Streamlit UI on port 8501 calls it directly; it never imports workflow/database helpers. There is no separate HTTP API server. The default actor is `local-demo-reviewer`; production authentication is not implemented.

The UI caches the service/engine, while every operation owns a fresh SQLAlchemy session. Successful changes commit atomically. Workflow refusals preserve their audit/hold records; database errors roll back. Returned dictionaries are detached and JSON-compatible, with ISO timestamps. Pydantic validates inputs and UUIDs before workflow execution. Errors raise `ServiceError` with a safe message and a code: `invalid_input`, `not_found`, `conflict`, or `unavailable`.

- `health()` -> {status,database,delivery_mode}; also available as `python -m app.cli health`.
- `config()` -> {delivery_mode:"mock",openai_configured:bool,critique_enabled:bool,openai_model:str,openai_reasoning_effort:str,policy_label:str}.
- `profile_example()` -> synthetic profile input; `add_customer(profile)` -> saved customer without evaluating it.
- `list_customers()` -> summary list [{id,name,email,scenario,version}].
- `demo_customers()` -> up to five current eligible, uncontacted examples [{id,name,scenario,demo_case}], without writing evaluations.
- Completed actions use `no_outreach` with `reason_code: outreach_completed`, `completed_key` and `completed_at`; timing holds include `next_eligible_at`. Re-evaluation reopens a matching local delivered run and audits the revisit. Fixture history alone cannot create a delivered run.
- `get_customer(customer_id)` -> full object including context and updated_at.
- `create_run(customer_id)` -> detailed run.
- `list_runs(customer_id=None)` -> up to 200 recent run summaries {id,customer_id,customer_name,state,created_at,strategy}.
- `get_run(run_id)` -> {id,customer_id,customer_name,state,created_at,snapshot,policy_snapshot,segments,strategy,blocked_reasons,latest_revision_id,revisions:[...],reviews:[...],deliveries:[...],audit:[...]}.
- `draft(run_id, provider="template"|"openai")` -> detailed run.
- `edit(revision_id, subject=..., body=..., feedback="")` -> detailed run.
- `critique(revision_id)` -> reassess latest revision, invalidate earlier approval and return the run.
- `review(revision_id, action="approve"|"reject", feedback="", scores={}, review_source="human"|"automated"|"unspecified")` -> detailed run.
- `deliver(revision_id)` -> detailed run.
- `simulate(customer_id, event="payment"|"opt_out"|"dispute")` -> customer object; synthetic-only, increments version for freshness demo.
- `rubric()`, `evaluation_summary()`, `dashboard()` -> read-only rubric, review aggregates, and overview metrics.

Run states: evaluated, held, no_outreach, drafted, validation_failed, approved, rejected, delivered. Validation JSON: {passed:bool,checks:[{code,passed,detail}]}. UI must show backend rejection reasons and never claim delivery without status delivered. New edits invalidate prior approval. Only latest revision can be approved/delivered. Delivery reevaluates current evidence/permission and reserves unique action transactionally under customer lock. Provider template is explicit local demo generation, never labelled OpenAI. OpenAI mode is optional, server-side key only.

Generator–critic extension: validation JSON additionally records `critique_required`, `critique`, `generation_attempt` and `generation_attempt_limit`. A configured model requires critique for template and OpenAI drafts and edits. OpenAI generation loops at most three times, preserving every candidate as a separate revision and sending critique feedback to the next generator call. Deterministic failures skip the critic and feed rule descriptions to the generator. Critic errors stop the loop; failed assessments or an exhausted loop block approval. Without OpenAI configuration, template-only demo revisions explicitly record `not_required`; a revision that already required critique cannot lose that requirement by changing provider or editing. Only human action can restart a bounded cycle. Approval and delivery verify the stored critique's content hash, policy hash and input fingerprint. No LLM request occurs during review or delivery. Both prompts and the loop bound live in the frozen content policy.
