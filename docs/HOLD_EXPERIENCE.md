# Hold experience investigation

The focused fix removes 602 unnecessary current holds across 5,001 existing PostgreSQL customers: 400 eligible later-stage reminders become actionable and 202 already-completed actions become closed no-outreach outcomes. Other customers’ eligibility outcomes are unchanged. No customer data, consent, send history, benchmark inputs or expected labels were rewritten.

## Measurement

Measured 17 November 2026 at 14:43 IST. The before/after comparison evaluates the same frozen customer snapshots at the same time, including the actual PostgreSQL offer catalogue and mock-delivery history. It uses deterministic policy evaluation and makes no model calls. Aggregate results are in [hold_experience.json](evaluation/hold_experience.json).

| Current policy outcome | Before | After |
|---|---:|---:|
| Hold | 2,803 | 2,201 |
| Billing reminder | 399 | 799 |
| Offer or reward | 970 | 970 |
| Appreciation | 328 | 328 |
| No eligible outreach | 501 | 501 |
| Already completed; no repeat | 0 | 202 |
| Total | 5,001 | 5,001 |

The initial database contained 5,008 saved evaluations, including 2,405 holds and 3 mock deliveries. Latest saved evaluations per customer contained 2,402 holds. These historical counts differ from current evaluations because snapshots retain earlier policy decisions and multiple attempts. The fix does not rewrite these rows to lower a dashboard total; the overview now labels the metric **Historical holds**.

The 2,405 saved holds comprised 600 permission refusals (400 marketing, 200 billing); 200 each for stale sources, outages, contact frequency, incomplete segment evidence, disputes, missing invoices, missing redemption history, and reminder timing; 203 duplicate-action refusals; and 2 policy-change refusals. The policy-change refusals remain correct: old drafts must receive a fresh evaluation and approval after configuration changes.

## Causes and changes

| Affected case | Before | After |
|---|---|---|
| 200 stage-2 and 200 stage-3 fixtures | The single-reminder rule blocked every invoice with prior history; invoice-wide deduplication also prevented progression. | Next stage allowed only with matching, consecutive, chronological delivery history, configured overdue days and minimum interval, and all contact/billing checks. |
| 202 completed marketing actions | Fresh “On hold” results on repeat evaluation. | `no_outreach / outreach_completed`. Real local deliveries reopen the original run, revision, approval and delivery. Fixture-only history stays a closed result without inventing a delivery record. |
| 200 recent-reminder fixtures and 1 recently mock-delivered bill | Generic “already sent” hold. | Explicit next eligible timing. Re-evaluating the locally delivered bill reopens the completed stage with the next timing check, without adding another evaluation or delivery. The pure policy count still includes this timing hold. |
| Default customer selection | Alphabetical first customer had already been delivered. | **Ready for demo** selects current eligible, uncontacted examples across five cases. **All customers** keeps every customer, refusal case and saved outcome accessible. |
| Historical duplicate evaluations | “On hold” and “resolve the reason” implied a failed new action. | “Previously delivered · Repeat outreach prevented,” with a path to another eligible customer. Original audit and saved state remain intact. |

The five current demo examples are customers 00003 (declining), 00004 (dormant), 00007 (billing), 00026 (loyalty) and 00044 (consistent). All five are currently actionable and have no recorded outreach. Selection uses actual rule results, not fixture scenario labels, and changes as customers are contacted or evidence expires. It creates no profiles, drafts, evaluations or deliveries. If no eligible examples remain, the UI says so and exposes all customers; it does not refresh stale evidence automatically.

## Legitimate holds retained

The 2,201 remaining policy holds are 600 permission refusals, 200 stale sources, 200 disputes, 200 missing invoice records, 200 incomplete segment evidence, 200 outages, 200 unknown redemption histories, 200 contact-frequency limits and 201 reminder timing waits. Missing or contradictory history also fails closed in regression tests.

The dataset deliberately balances 25 scenarios with 200 customers each. Its original independent labels expect 2,400 holds out of 5,000 records (48%), including duplicate-prevention fixtures. This is a stress-test distribution, not a real-world customer refusal rate. The 400 added false holds came from the later single-reminder simplification, not the dataset. The original labels and all manifest hashes are preserved; older benchmark agreement scores are not claimed as measurements of the changed policy.

## Reminder progression and preserved requirements

The `reminders` list in `app/policies/policy.json` configures one to three stages. The separate Reminders editor was removed by a concurrent, explicitly requested UI simplification; that does not remove deterministic progression. Defaults retain the original illustrative timing: stage 1 is courteous from day 1 overdue, stage 2 direct from day 8, stage 3 firm from day 15; later stages require at least 7 days since the previous send. A first reminder never jumps straight to a severe stage because an invoice is old. Every stage retains support/dispute wording and requires a separate reviewed revision.

History must match delivery evidence for the invoice, start at stage 1, have no gaps or repeated stages, use increasing nonfuture times, and satisfy configured timing. A duplicate key is specific to customer, invoice and stage. Legacy monthly appreciation keys remain recognized. Missing billing history cannot be bypassed by calling a later stage a new action.

FR-01–FR-10 remain supported: permitted evidence and explainable classification; deterministic strategy/offer/billing checks; grounded configured content; reminder progression; consent, dispute, freshness, contact frequency and duplicate controls; exact-revision human approval and feedback; immutable historical evidence and audit events; and mock-only delivery. No new tables or API service were introduced. The configured model remains `gpt-6-astra` with high reasoning.

## Verification

All **485 tests passed**, with no skips when `TEST_POSTGRES_URL` was set from `Settings`. PostgreSQL concurrency tests use disposable isolated schemas and cover competing runs and exact-revision retries. New tests cover staged timing boundaries, malformed and mismatched history, stage limits, lost consent, payment, disputes, stale evidence, other recent contacts, repeated evaluation, separate approval for each of three deliveries, fixture-only completion, read-only demo selection, and UI navigation/configuration.

The comparison made zero paid model calls. Template generation in isolated tests uses unconfigured model settings; the configured live UI still requires AI critique even for templates.

Local services were restarted with `./scripts/start_local.sh --skip-load --dataset-dashboard`. PostgreSQL health is OK, the seven-table schema is unchanged, and the reviewer and dataset explorer remain available on ports 8501 and 8502. Existing unrelated documentation and customer-label edits were preserved.

Live browser checks confirmed the default eligible customer reaches **Ready to draft**, Customer 00001 reopens its original **Mock delivery recorded** result with downloadable approved output, and Customer 00011 still holds for denied billing permission. The app was left on Customer 00003 ready to draft. No live draft or delivery was created during these checks: all 5,001 customer profiles retain identical context values, versions, names and emails, and the database retains 3 deliveries, 5 revisions and 4 reviews. Only evaluations and the completion-reopen audit were added.
