# Demonstration walkthrough

> Historical walkthrough for the original dataset and rules. Current decisions follow [Simple rules](SIMPLE_RULES.md); fresh evaluations can differ from the examples below.

For the current demo, start with **Ready for demo**, choose an eligible example, then **Evaluate customer**. The picker skips recorded outreach; **All customers** retains the refusal examples. Completed local outreach reopens as a successful mock delivery, and **Choose another eligible customer** continues the demo.

This historical walkthrough uses the shipped 5,000-customer synthetic fixture. It demonstrates actual application behavior, independent segment overlap, controlled drafting, human review, and mock delivery. The six scenarios below use the **second fixture record** in each scenario, with concrete stable IDs for seed `20261117`. The first appreciation, loyalty, and billing records have already been mock-delivered during application smoke checks and can correctly hit duplicate/contact-frequency controls.

## Preparation

Open the reviewer at [localhost:8501](http://127.0.0.1:8501), and the data explorer at [localhost:8502](http://127.0.0.1:8502). If needed, start the existing local application without reimporting data:

```sh
./scripts/start_local.sh --skip-load --dataset-dashboard
```

Isolated deterministic tests use a service with no model key for a reproducible, zero-API-call demonstration. In the configured live UI, both template and AI drafts require AI critique and can make paid calls. Optional OpenAI generation uses the configured `gpt-6-astra` model with high reasoning; it is a distinct provider and must be labelled accordingly. The main benchmark measures templates and local provider stubs. A separate six-call live sample produced five accepted drafts and one validation rejection; it is not a population-level OpenAI quality estimate. The failed call remains visible and was not retried to improve the score.

**Create a fresh evaluation for every selected customer before drafting.** Existing evaluations created before content-policy configuration was added carry an older policy hash and are intentionally blocked before generation. Do not use those historical runs for a current-action demonstration.

The fixture reference time is `2026-11-17T06:30:00+00:00`. Current-time evaluations correctly hold stale data after the evidence window. For reproducible offline metrics, run `python scripts/evaluate_submission.py --include-openai`; this only rechecks saved live evidence and makes no model requests. Historical evaluation replay does not establish current eligibility and does not bypass the delivery recheck. Regenerate/import into a fresh demo database when demonstrating live current-time delivery after the fixture becomes stale.

If a selected customer has since been demonstrated and delivered, choose another uncontacted record among the 200 records for that scenario and create a fresh evaluation. Never remove delivery history to force eligibility.

Show the customer page first: search the 5,000 synthetic records, choose a customer, and view their plan, stored strategy, recent activity and outstanding balance. Missing values remain distinct from zero. Stored strategy labels are fixture expectations; the reviewer uses actual policy evaluation.

## 1. Consistent customer → appreciation

Select **Synthetic Customer 00027**, scenario `appreciation`.

Customer ID: `1586d363-3265-5073-b8b3-5bf523d2f496`.

Create a fresh evaluation. The three preceding completed cycles were paid fully on time; tenure is below the example loyalty threshold. `consistent` matches; `loyalty_candidate`, `declining_activity`, `dormant`, and `payment_reminder` do not match. Strategy: `appreciation_without_offer`.

Generate the template draft. Show that appreciation does not invent a benefit, discount, payment demand, or required customer action. Inspect the validation results in Draft & review and the classification reasons in Decision.

## 2. Loyalty candidate → eligible offer

Select **Synthetic Customer 00026**, scenario `loyalty_offer`.

Customer ID: `b7dac56e-be52-56a8-893b-26b88f324849`.

Show both `consistent` and `loyalty_candidate` matching independently. Strategy: `offer_or_reward`. Inspect the selected `LOYALTY_2GB` version 2, plan eligibility, expiry, redemption history, and marketing permission.

Generate the template. It includes the exact approved benefit, terms, expiry, and statement that the offer has not been activated. Enter a genuine reviewer judgment using the five-criterion rubric, approve the exact revision, and then use mock delivery if current evidence still passes. Show the delivery record and audit events. No customer email is sent and no add-on is provisioned.

For an edit demonstration, edit before approval or create another undelivered evaluation. Show that editing produces a new revision, revalidates it, and invalidates approval of the earlier revision. Do not present an already-delivered draft as editable.

## 3. Declining activity → eligible offer

Select **Synthetic Customer 00028**, scenario `decline_offer`.

Customer ID: `39ab2194-174e-5f36-8654-121f4550a4ef`.

Open the customer's evidence in the reviewer. Both windows are complete, prior activity exceeds the minimum baseline, and current activity is positive but at most 60% of prior. `declining_activity` matches; `dormant` does not. The customer can be consistent without qualifying for the tenure-based loyalty segment.

Strategy: `offer_or_reward` because a qualifying segment and all offer controls match. Show that the outcome comes from the usage and catalogue rules; the system does not infer churn or mood from the scenario label or feedback wording.

## 4. Dormant active subscription → offer; inactive control → no outreach

Select **Synthetic Customer 00029**, scenario `dormant_offer`.

Customer ID: `2e7204b0-fdce-554d-bbb0-3c20babda6b0`.

Show zero current active days in a complete 30-day window and an active subscription. `dormant` matches. Zero is not missing evidence and is not the same as the positive-activity decline rule. Strategy: `offer_or_reward`, subject to the same catalogue and permission checks.

As a short control, select **Synthetic Customer 00046**, scenario `inactive_no_outreach`, ID `252c4c1b-9ee3-5adb-bbd0-647b3ed60fd3`. Its inactive subscription does not qualify as an active dormant customer. The result is `no_outreach`; the system must not produce a draft for that strategy.

## 5. Verified overdue invoice → courteous billing reminder

Select **Synthetic Customer 00031**, scenario `billing_first`.

Customer ID: `b3933b31-4bd9-5fa6-a5c4-37a15e28e327`.

Show the selected unpaid invoice, exact outstanding amount, due date, explicit billing permission, and known reminder history. `payment_reminder` matches. Strategy: `billing_reminder`, stage 1. Billing takes priority over discretionary offers.

Generate the draft. Verify the invoice ID, amount, due date, respectful wording, and support/dispute path against the source. Do not imply that overdue age or negative feedback alone permits a stronger tone. Stage progression is separately constrained by actual prior reminder history and the minimum interval.

To show a controlled failure, edit an undelivered draft to add an invented amount or unsupported benefit claim. Validation should fail; remove the change before proceeding. The offline evaluation already measures these adversarial cases, without database mutation.

## 6. Disputed invoice → held

Select **Synthetic Customer 00042**, scenario `open_dispute`.

Customer ID: `34947ff1-eb00-5e66-b60d-12f8d297ebfe`.

Show that `payment_reminder` can match while the overall strategy remains `hold`: the invoice is overdue, but dispute controls prevent outreach. This distinction separates a factual segment result from permission to act. Inspect the explicit blocking reason. No draft or mock delivery should be permitted.

Other available hold scenarios include stale evidence, missing invoices, incomplete usage, marketing/billing opt-out, unknown permission, recent reminders, and duplicate/frequency restrictions. Unknown evidence remains `insufficient_evidence` rather than silently becoming a negative result.

## Demonstrate explicit input and feedback

The **Add profile** page provides an editable synthetic profile and an example download. Keep `synthetic=true` and a reserved-domain fictional email. Missing fields remain unknown; unsupported extra fields are rejected by the API. Saving a profile is separate from evaluating it.

The review form offers relevance, clarity, personalization, call to action, and tone scores, each on the canonical 1–5 scale. Only a person who actually reviews the draft should submit ratings labelled `human`. The separately supplied six-sample assistant assessments are labelled `automated`. Do not treat the presence of a rating form or automated scores as an independent human study.

Finish by opening [EVALUATION_REPORT.md](EVALUATION_REPORT.md): confusion matrices, actual strategy agreement, independent raw-fact checks, adversarial/refusal outcomes, and clearly separated automated usefulness evidence. State the limits: synthetic data, illustrative policies, mock operations, no production authentication, and no real-world quality or fairness guarantee.
