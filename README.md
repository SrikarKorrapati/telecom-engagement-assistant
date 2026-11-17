# Telecom customer engagement assistant

A local prototype that assembles synthetic customer evidence, evaluates five independent segments, chooses an illustrative engagement strategy, drafts a message, and requires approval of the exact revision before recording a mock delivery.

The application uses Streamlit, a direct Python application service, and SQLAlchemy/PostgreSQL. Streamlit calls `EngagementService` in its server process, so no separate API server is needed. OpenAI drafting uses a generator–critic loop: the generator writes the complete email subject and body and the independent critic assesses the complete message, feeding required corrections back for up to three attempts. The policy engine chooses segments, strategy, eligibility, and reminder stage. Customer feedback is available as context, with no separate sentiment-analysis workflow. Internal scripts retain an explicit offline template mode for testing without an API key.

**This is a synthetic, single-user prototype. All delivery is mocked. No customer is contacted, no offer is provisioned, and no billing account is changed.**

## Open the prepared application

This workspace's native PostgreSQL demo contains 5,000 synthetic customers and an initial 5,000 policy evaluations. Open the [reviewer dashboard](http://127.0.0.1:8501) to inspect an evaluation and draft/review a message, or the [data dashboard](http://127.0.0.1:8502) to search customers and view their basic details. If the services are stopped, reuse the existing database without reimporting:

```sh
./scripts/start_local.sh --skip-load --dataset-dashboard
```

The chosen application model is `gpt-6-astra` with reasoning effort `high`. Keep the existing `OPENAI_API_KEY` in the application's server-side `.env` and set only these configuration fields as needed:

```dotenv
OPENAI_MODEL=gpt-6-astra
OPENAI_REASONING_EFFORT=high
```

AI drafting is the only generation option in the reviewer; the user explicitly starts each generation. Generator and critic use the configured model and reasoning settings with separate system prompts. OpenAI generation makes up to six requests (three generator/critic pairs); rule failures skip the critic, and API errors stop the loop. With OpenAI configured, template drafts and human edits also receive one AI critique. Without a key/model, the reviewer disables email generation; offline scripts can still run template checks. Classification and strategy remain deterministic.

See [the implemented architecture and stack](docs/ARCHITECTURE.md) for the connections and loop. The new content/prompt configuration requires a fresh evaluation before older runs can draft or deliver; their saved history remains readable.

## Start locally

Requirements: Python 3.11+, PostgreSQL 16 binaries, and the project dependencies. The local launcher defaults to the PostgreSQL installation at `/Library/PostgreSQL/16/bin`; set `PG_BINDIR` for another installation. Docker is optional and is not required.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Configure `DATABASE_URL` in your shell or existing `.env`. Shell variables take priority. The application and launcher never rewrite `.env` or replace its API key. For a new isolated local database:

```sh
export DATABASE_URL=postgresql+psycopg2://telecom@127.0.0.1:55432/telecom_demo
./scripts/start_local.sh --dataset-dashboard
```

The launcher creates a cluster only under this workspace's `.local/postgres`, listens on loopback port 55432, creates the configured database if absent, initializes the seven tables, and idempotently imports the 5,000-customer dataset. It does not touch another PostgreSQL cluster. If the port belongs to another server, it stops and asks you to select an external database explicitly.

| Interface | Local URL |
|---|---|
| Reviewer application | http://127.0.0.1:8501 |
| Optional dataset dashboard | http://127.0.0.1:8502 |

Check the database connection with `.venv/bin/python -m app.cli health`. The Python service interface is documented in [the implementation contract](docs/contracts.md). When upgrading an already running version, run `./scripts/stop_local.sh` followed by the startup command; the stop script also cleans up a recorded legacy API process.

The isolated local cluster uses trust authentication on loopback for demonstration. It is not a production database configuration. Existing shell and `.env` database settings are honored; a pre-existing setting pointing to port 5432 will connect there instead of creating the isolated cluster. Use an explicit `DATABASE_URL` when you want the isolated default.

Startup options:

```sh
./scripts/start_local.sh --external-db          # Connect to an already managed database; no PostgreSQL lifecycle changes.
./scripts/start_local.sh --skip-load            # Start with existing database contents.
./scripts/start_local.sh --seed-demo            # Use 10 compact examples instead of importing the 5,000-record catalogue.
./scripts/stop_local.sh                         # Stop only application processes recorded by this launcher.
./scripts/stop_local.sh --postgres              # Also stop this workspace's isolated PostgreSQL cluster.
```

Logs are appended under `.local/logs/`; application PID files are under `.local/pids/`. Services start in their own process session with no terminal input, so they remain running after the launching shell exits. Streamlit file watching is disabled. Repeated startup reuses matching running processes. Stop checks recorded PIDs and command identity before signaling a process. Neither script deletes data. The launcher requires a prepared virtual environment; it does not install packages automatically.

The 10-example seed and 5,000-record dataset use different offer identities for the same code/version. Choose one per database. A conflicting import fails without overwriting rows; use a separate empty database when switching datasets.

## Database commands

All commands use the configured `DATABASE_URL`. To avoid reading `.env` for a command, provide `--database-url` before the subcommand.

```sh
.venv/bin/python -m app.cli init-db
.venv/bin/python -m app.cli health
.venv/bin/python -m app.cli load-dataset datasets/telecom_5000
.venv/bin/python -m app.cli evaluate-all --fixture-time
```

`init-db` creates tables but does not seed by default. `load-dataset` inserts absent customers and offers, preserving every existing edit. Conflicting catalogue identities are rejected before imports are written. The import is atomic and repeatable. Expected labels are never loaded into the database or sent to the model.

`evaluate-all` calls the same workflow used by the Python service and commits each customer's run separately. It creates evaluations, not drafts or deliveries. Its options are:

- `--fixture-time`: replay each explicitly synthetic record at its saved reference timestamp, reproducing the shipped expectations.
- `--as-of 2026-11-17T06:30:00+00:00`: evaluate at a supplied timezone-aware reference time.
- `--limit 20`: evaluate a small sample.
- `--skip-existing`: skip customers that already have a run.

Without a time option, evaluation uses the current time. Saved data becomes stale after the illustrative 48-hour evidence window. Repeated evaluation creates additional auditable runs unless `--skip-existing` is used. Failures are isolated per customer and reported by ID and error type.

Synthetic historical delivery summaries are stored in `context.fixture_delivery_history`, with `fixture_history_synthetic=true` and `fixture_as_of`. They supply fixture evidence for frequency and duplicate checks; they are never inserted as actual deliveries, approvals, or message revisions. Real mock-delivery records remain separate in `delivery_jobs`.

For only the compact ten examples, use a separate empty database:

```sh
.venv/bin/python -m app.cli init-db --seed-demo
```

## Optional Docker database

`compose.yaml` runs PostgreSQL only; Streamlit and its Python service run in the local virtual environment. Choose this or the isolated local cluster, because both default to host port 55432.

```sh
docker compose up -d postgres
export DATABASE_URL=postgresql+psycopg2://telecom:telecom_local@127.0.0.1:55432/telecom_demo
./scripts/start_local.sh --external-db --dataset-dashboard
```

`POSTGRES_PASSWORD` and `POSTGRES_PORT` can override the Compose demo defaults; update `DATABASE_URL` to match. The Compose volume persists data. Do not use `docker compose down -v` unless you intend to delete that demo database. Docker is not installed or invoked by the local startup scripts.

## Reviewer workflow

Use **Add profile** to download a fresh synthetic example, edit its JSON evidence, save it, and evaluate it. The same flow is available through `EngagementService.profile_example()`, `add_customer(profile)`, then `create_run(customer_id)`. Profile input is explicitly synthetic and accepts only reserved example email addresses. The input schema rejects unknown fields, including demographic/proxy fields, malformed dates and numeric values. Missing evidence remains null and produces a hold or unknown segment; it is not silently filled with invented data. Input schemas are defined in `app/profiles.py` and `app/service.py`.

Reviewers may optionally score **relevance, clarity, personalization, call to action, and tone** when approving or rejecting a draft. All five scores must be integers from 1 (unusable) to 5 (excellent), or the score object must be empty. Scores remain attached to the exact reviewed revision. The overview reports human and automated judgments separately and does not turn missing human ratings into zeros. `EngagementService.rubric()` returns the rubric and `evaluation_summary()` returns the aggregates. Repeated judgments count only the latest judgment per revision and source. Review source is declared by the local caller; authenticated reviewer identity is still production roadmap work.

The reviewer opens with **Ready for demo**, a read-only selection of current eligible, uncontacted examples. Switch to **All customers** to inspect intentional refusal cases or completed outreach. Re-evaluating a completed local action opens its original delivery without sending again. See [hold investigation](docs/HOLD_EXPERIENCE.md) for measured causes and changes.

Select a customer, create an evaluation, inspect every independent segment result and its supporting evidence, then review the chosen strategy. For an actionable strategy, click **Generate email** for a complete AI-written subject and body. Every generator attempt is a saved revision. The critic's findings feed the next generator attempt until both rules and critique pass or the attempt limit is reached. Unresolved findings block approval. The reviewer can edit, reject, or approve; edits invalidate both prior approval and critique and receive a new assessment when configured. A final delivery attempt rechecks current evidence, permissions, eligibility, duplicates, the saved exact-message critique, and approval before recording a mock outcome. Critic errors can be retried with **Run AI critique**; **Regenerate email** starts a new bounded cycle using the current findings. Human edits are critiqued once and never silently rewritten.

Billing takes precedence over discretionary engagement. All of the following are illustrative policy conditions:

- **IF** a verified invoice has a positive outstanding balance and its due date is before today, select a billing reminder, provided the next configured stage is due, prior stages have matching delivery evidence, and contact checks pass.
- **ELSE IF** loyalty, dormancy, or declining activity matches and a valid approved offer is eligible, select an offer/reward.
- **ELSE IF** the active customer is consistent, has no dormancy/decline match, and no eligibility uncertainty remains, select appreciation without an offer.
- Missing, stale, disputed, conflicting, or disallowed evidence can hold the affected outreach. Otherwise select no outreach.

These are ordered strategy conditions. Segment evaluation is independent: each customer receives all five segment results, and several can match together.

```mermaid
flowchart LR
  A[FR-01 Profile] --> B[FR-02 Segments]
  B --> C[FR-03 Strategy]
  C -->|IF eligible offer| D[FR-05 Offer checks]
  C -->|IF overdue invoice| E[FR-06 Reminder checks]
  C -->|IF appreciation| F[FR-04 Draft]
  D --> F
  E --> F
  F --> G[FR-07 Rule checks and AI critique]
  G -->|Revise, attempts remain| F
  G -->|Pass| H[FR-08 Human review]
  H -->|Approve current revision| I[FR-10 Mock delivery gate]
  H -->|Edit| F
  C -. Record every stage .-> J[FR-09 Audit]
  I -. Record outcome .-> J
```

The diagram shows the main successful path. Holds and validation failures record their reason; review edits return through validation; rejected drafts are not delivered. FR-07 appears after draft generation. Audit records span the workflow.

## Seven-table design

Reviews now also store `scores` and `review_source`. `init-db` applies an idempotent, additive migration for existing prototype databases. Existing rows remain intact and receive empty scores with source `unspecified`; old test approvals are not relabelled as human assessments. The schema still has seven tables.

| Table | What it stores | Rationale |
|---|---|---|
| `customers` | Current selected account/subscription context, source data, permissions, and version | One JSONB context keeps the synthetic prototype small while preserving explicit evidence and missing values. |
| `offers` | Versioned catalogue and eligibility details | Separates approved commercial terms from generated wording. |
| `engagement_runs` | Frozen customer/policy snapshots, independent segments, selected strategy, holds | Makes each decision explainable and replayable. |
| `message_revisions` | Draft versions, content hashes, validation results, provider/version metadata | Preserves review history and supports approval of one exact revision. |
| `reviews` | Approval, rejection, and edit feedback linked to a message revision | Keeps human decisions explicit and auditable. |
| `delivery_jobs` | Mock delivery status, exact revision/approval links, unique logical-action key | Prevents duplicate logical sends and cross-run/message approval linkage. |
| `audit_events` | Stage decisions, evidence references, review actions, outcomes | Provides a chronological investigation trail. |

PostgreSQL provides transactions, row locking, relational identities, JSONB, and unique constraints. SQLAlchemy keeps persistence explicit; SQLite exists only as a lightweight unit-test backend. Composite foreign keys bind a delivery to the correct run, message, and review. The application treats revisions and audit events as append-only; database-level immutable audit enforcement is a production task. Nested JSON changes replace the full context object so SQLAlchemy tracks them.

| Functional requirement | Implementation responsibility |
|---|---|
| FR-01 | Assemble minimal selected-customer context with source timestamps and references. |
| FR-02 | Evaluate consistent, loyalty candidate, declining activity, dormant, and payment-reminder segments independently. |
| FR-03 | Resolve overlap through explicit policy priority and select objective/tone. |
| FR-04 | Generate a grounded subject and body directly with an LLM and a system prompt. |
| FR-05 | Verify catalogue version, plan eligibility, value, terms, expiry, channel, and redemption history. |
| FR-06 | Verify an unpaid overdue bill and progress through configured stages using verified history, timing and duplicate protection per stage. |
| FR-07 | Run deterministic checks and an independent LLM critique; feed findings back to the generator within the bounded loop. |
| FR-08 | Show evidence and draft, collect review feedback, and bind approval to the current revision. |
| FR-09 | Record workflow evidence, policy/provider versions, reviews, and mock outcomes. |
| FR-10 | Recheck current eligibility, permission, duplication, and exact-revision approval before mock delivery. |

## Dataset and validation

The complete submission materials are indexed in [the submission checklist](docs/SUBMISSION_CHECKLIST.md): [measured evaluation](docs/EVALUATION_REPORT.md), [scenario walkthrough](docs/DEMO_WALKTHROUGH.md), and [permitted decision features](docs/DECISION_FEATURES.md). Automated usefulness assessments are labelled as automated judgments. No independent human study or real-population quality claim is implied.

## Current policy and structured output

Open **Policies** in the sidebar, or [the policy dashboard](http://localhost:8501/?page=policies), to inspect and edit the exposed segment, contact and offer controls. Reminder progression remains configured in `app/policies/policy.json`; approved wording and safety settings remain in `app/policies/content.json`. Each tab shows a short summary; editing controls and technical details are expandable. [Dashboard guide](docs/POLICY_DASHBOARD.md).

Saving policy or content requires a new version, validates the complete configuration, and rejects changes made from an outdated view. Offer edits create a new catalogue version and retire earlier active versions of the same offer code. Saves are recorded in the audit table. Existing evaluations keep their original snapshots; create a fresh evaluation after configuration changes.

`app/policies/policy.json` is the single current policy; `app/policies/content.json` contains current templates, prompts and validation settings. Save changes in place from **Policies** without entering a new version number. See [the simple decision rules](docs/SIMPLE_RULES.md) for the five outcomes and segment definitions.

Consistency uses the latest completed bill, loyalty uses active status and tenure, decline means any positive decrease in usage, and dormancy means zero usage. Billing advances through configured courteous, direct and firm stages only with verified prior delivery and elapsed timing limits. Permission, evidence, dispute, offer eligibility, contact-frequency and duplicate checks still apply.

Each evaluation freezes its evidence and configuration. Full content hashes detect changes even though the policy name stays fixed. Changed settings require a new evaluation before drafting, editing, critique, approval or delivery. Outdated edit forms are rejected. Dashboard saves wait for in-progress service actions. Old evaluations, approvals and audit records remain readable. Appreciation deduplication uses customer/month rather than the policy name and recognizes older delivery keys.

The bundled dataset's expected labels and earlier evaluation reports are historical. They describe the earlier rules and are not measurements of the current simple policy. Fresh evaluations use the current rules; fixture scenario labels never select customer treatment.

The model uses Responses Structured Outputs with the following Pydantic contract:

```python
class EmailDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=6000)
```

Example output, shown for the schema rather than as an evaluation result:

```json
{"subject":"A note from Customer Care","body":"Hello customer,\n\n...complete email...\n\nCustomer Care"}
```

The generator receives the selected purpose, tone and verified required lines, and returns the entire email subject and body. The application validates that output rather than assembling it from template openings and closings. Repairs receive critic findings, failed rule descriptions and the previous draft when it passed rule checks. Only **Generate email** is exposed in the reviewer; there is no provider selector, Reminders tab or Messages editor. Explicit offline template generation remains available to internal scripts and fixtures.

The critic receives the complete draft with the customer name replaced by `[customer]`, selected objective/tone, and only the selected verified message facts. It does not receive the full customer context, feedback, email address, account/subscription identifiers, or the complete offer catalogue. Failed deterministic checks prevent sending a draft to the critic. Its structured response contains `verdict` (`pass` or `revise`), a summary, and findings with category, severity, quote, explanation and suggested fix. A revise verdict requires a blocking finding; a pass cannot contain one.

Critiques are saved in `message_revisions.validation.critique`, with model/prompt metadata and hashes binding them to exact text, facts and policy. Approval and delivery recheck this binding without another model request. No new database table or migration is required. Refusals, incomplete responses, API errors and malformed assessments fail closed. The critic cannot override failed rule checks or replace human approval. Both heuristic checks and model assessments have limits; neither proves arbitrary prose correct.

## Reproducible submission evaluation

```sh
.venv/bin/python scripts/evaluate_submission.py --include-openai
```

This uses the dataset's recorded reference time, compares actual policy predictions against separately authored labels, and writes the evaluation artifacts under `docs/evaluation/`. It does not send messages or call a paid model API. The `--include-openai` flag rechecks saved live outputs without API calls. Fresh live generation is explicitly opt-in through `scripts/evaluate_openai_samples.py --run` (six requests, no delivery); it refuses to overwrite an existing live report. `scripts/smoke_test.py` separately exercises the Python service and mock delivery without needing a running UI or API server. It defaults to offline template mode even when `.env` contains an OpenAI key; `--openai` explicitly enables generator and critic model calls.

## Dataset validation details

See [implementation validation](docs/VALIDATION.md) for the submission test results, live Astra 6 verification, known limitations, and smoke-test instructions.

The [dataset guide](datasets/telecom_5000/README.md), [data dictionary](datasets/telecom_5000/DATA_DICTIONARY.md), and [coverage report](datasets/telecom_5000/VALIDATION_REPORT.md) describe all 5,000 records. They are balanced synthetic fixtures, not a representative telecom population. Contacts use reserved `example.com`; all customer contexts are marked synthetic. Customer feedback covers support chats, complaints, surveys, and emails.

The shipped expected distribution is 800 offers, 400 appreciation messages, 800 billing reminders, 2,400 holds, and 600 no-outreach decisions at its reference time. Labels live separately from application inputs. The independent validator checks arithmetic, timestamps, references, usage windows, authored segment/strategy expectations, and application policy agreement.

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest datasets/telecom_5000/test_dataset.py -q
.venv/bin/python datasets/telecom_5000/validate.py --check-reproducibility
```

Real PostgreSQL concurrency checks use `TEST_POSTGRES_URL`. The test role needs permission to create a temporary schema. Each test creates and removes its own uniquely named schema, leaving application/demo tables untouched. Both competing-run delivery and repeated delivery of the same revision are covered:

```sh
TEST_POSTGRES_URL=postgresql+psycopg2://telecom@127.0.0.1:55432/telecom_demo \
  .venv/bin/python -m pytest tests/test_acceptance.py -q
```

Without `TEST_POSTGRES_URL`, those two PostgreSQL-specific tests are skipped; the remaining tests use isolated test databases.

To generate a fresh synthetic snapshot, choose an explicit reference time and revalidate:

```sh
.venv/bin/python datasets/telecom_5000/generate.py --as-of 2026-11-17T12:00:00+05:30 --seed 20261117 --output-dir /tmp/telecom-fresh
.venv/bin/python datasets/telecom_5000/validate.py --directory /tmp/telecom-fresh --check-reproducibility
```

Import into a fresh database to demonstrate a regenerated snapshot. Idempotent import intentionally preserves existing customer edits instead of refreshing them silently.

## Prototype limits and production roadmap

There is no production authentication, authorization, multi-tenant isolation, real customer data integration, consent service, email gateway, offer provisioning, or payment processing. The local reviewer identity is a demo label. JSONB input is synthetic and intentionally compact; one selected account/subscription is represented per record. Rules are example policies, not approved telecom treatment. Optional model-generated wording can fail validation and needs human review. No performance, sentiment accuracy, population-representativeness, or production-safety claim is made by the synthetic tests.

Before production:

1. Integrate authoritative CRM, subscriptions, usage, billing, consent, and campaign systems; preserve source lineage and freshness contracts. Separate shared customers, accounts, and subscriptions when actual cardinalities require it.
2. Obtain approved policy thresholds, offer terms, reminder progression, contact limits, and dispute-handling rules. Add real evaluation datasets and policy-change review.
3. Add authentication, role-based review permissions, secret management, encryption, retention/deletion controls, and a clear personal-data access model.
4. Introduce migrations, immutable audit storage, backup/restore checks, metrics, alerting, workload limits, and PostgreSQL concurrency/load tests.
5. Add a durable queue/outbox, provider delivery receipts, retry reconciliation, and end-to-end idempotency before connecting an actual messaging gateway. Preserve current eligibility rechecks and exact-revision approval.
6. Evaluate model quality, prompt-injection handling, factuality, language coverage, costs, and failure modes against approved examples. Roll out under monitored human review before considering any automation expansion.
