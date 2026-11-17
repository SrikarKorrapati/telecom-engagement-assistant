# Decision features and boundaries

This document describes the current simple policy implementation, not a learned model or a claim about real-world fairness. The policy selects customer treatment using explicit facts and rules. The model is used only for bounded courtesy wording after the decision.

## Inspected policy inputs

The implementation in `app/policy.py` enforces `DECISION_SNAPSHOT_FEATURE_ALLOWLIST` (`customer_id`, `as_of`, `context`, `offers`, `recent_deliveries`) and `DECISION_FEATURE_ALLOWLIST` (`account_id`, `subscription_id`, `source_as_of`, `currency`, `active`, `plan_code`, `tenure_months`, `usage`, `invoices`, `permissions`, `open_dispute`, `reminder_history`, `redemptions`). `evaluate()` filters local copies before applying rules; nested data is read through explicit field lookups.

The allowed fields supply these feature groups:

| Feature group | Fields used | Why they are used |
|---|---|---|
| Evaluation control | `as_of`, policy version/thresholds/timezone, `customer_id` | Defines comparison time and policy; customer ID binds logical action identities, not segment membership. |
| Source integrity | `source_as_of`, `account_id`, `subscription_id`, `currency` | Checks freshness and whether selected-account billing evidence is identifiable and valid. |
| Payment history | Invoice `id`, `period_end`, `due_date`, `amount`, `outstanding`, `paid_full_at`, `status`, `disputed` | Checks the latest completed bill, exact balances, overdue status, evidence conflicts, and disputes. Billed amount validates balance consistency; it is not a customer-value score. |
| Subscription | `active`, `tenure_months` | Active status and illustrative tenure eligibility; dormancy also requires an active subscription. |
| Usage | Prior/current active-day counts, completeness, observed-day counts, known outage | Detects decline/dormancy only when relevant evidence is complete; outages hold interpretation. |
| Dispute control | `open_dispute`, per-invoice dispute flag/status | Prevents outreach before a dispute is resolved. |
| Offer eligibility | `plan_code`; catalogue ID/code/version/active flag; plan list, expiry, channel, redemption period; title/value/terms presence | Chooses only an approved, valid offer. Catalogue wording is a verified generation input; it is not invented by the model. |
| Redemption history | Offer code and redemption timestamp | Prevents repeat eligibility within the example redemption interval. |
| Contact permission | `permissions.billing`, `permissions.marketing` | Requires explicit permission for the selected purpose. Unknown is not treated as allowed. |
| Reminder history | Invoice ID, prior stage, sent timestamp | Prevents a second reminder for the same invoice; there is no escalation. |
| Delivery history | Prior delivery timestamp and idempotency key | Applies the contact-frequency cap and logical duplicate suppression. |

The snapshot's billing timezone is controlled by the approved policy configuration. Context window-start/end dates provide provenance and are checked by dataset validation; the current segment implementation uses completeness, observed-day counts, and active-day counts. Extending live integrations requires stronger event/window lineage validation.

## Excluded from treatment decisions

Customer display name, email, raw support-chat/complaint/survey/email text, and the fixture `scenario` label do not determine segment membership, strategy, offer eligibility, or reminder stage. The name appears only in a verified greeting. There is no sentiment classifier or inferred sentiment feature.

Age, gender, ethnicity, religion, disability, income, education, and marital status are not decision features. The synthetic profile service uses strict Pydantic models with extra fields forbidden; it does not provide inputs for these attributes. `context.synthetic=true` and reserved email domains constrain profile creation to this demonstration. The pure policy function remains a lower-level function and should not be treated as a service-validation boundary.

The optional OpenAI request contains the chosen objective and tone for courtesy copy. It does not receive raw customer feedback, demographic attributes, invoice amounts, account identifiers, or offer terms. Verified facts are inserted by deterministic code and checked afterward.

## Measured counterfactual check

The submission evaluator creates an in-memory alternate version of each of the 5,000 fixture snapshots. It changes all eight listed personal attributes together, changes the display name/email, and replaces raw feedback with alternate text containing an instruction. It compares the complete policy result with the original result.

The measured result is recorded in `evaluation/metrics.json` under `feature_invariance` and summarized in `EVALUATION_REPORT.md`. The earlier submitted run found 5,000 unchanged decisions out of 5,000 checks; this is historical evidence, not a rerun of the current rules. Fixtures and database rows are not modified. Separate tests in `tests/test_policy.py` change names, contact details, gender, ethnicity, religion, addresses, and postcodes, and also verify that relevant usage or billing changes do alter the decision.

This is a finite bundled perturbation check plus code inspection. It does not isolate every attribute separately, prove the absence of every possible proxy, establish demographic parity, or predict performance on a real population. Tenure, plans, billing, and usage may correlate with real demographic characteristics; approved production policy and appropriate real-world evaluation are still required. A future policy change must rerun these checks and update this list.

## Message usefulness boundaries

Usefulness is assessed separately with the five-criterion rubric: relevance, clarity, personalization, call to action, and tone. Each criterion uses an integer from 1 (unusable) to 5 (excellent). Scores are reviewer judgments, not inputs to customer treatment.

The reviewer form and Python service support all five scores and explicit `review_source` values (`human`, `automated`, `unspecified`), persisted on the existing reviews table. Automated assistant assessments, human review ratings, and unspecified legacy review records remain distinct evidence categories. This implements feedback collection but does not itself constitute a user study. No independent human study has been performed. Template samples cannot establish the quality of live OpenAI generation.
