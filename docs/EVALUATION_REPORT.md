# Synthetic evaluation report

These are reproducible fixture and bounded-template measurements, with any saved live-provider sample reported separately. They are not estimates of real-world telecom outcomes or population-level model quality.

Dataset: **5,000 synthetic customers**, 25 deliberately balanced scenarios. Reference time: `2026-11-17T06:30:00+00:00`. Policy: `prototype-v1`.

Predictions are produced by `app.policy.evaluate` from customer facts, offer catalogue, and synthetic delivery history. Authored expected labels are loaded only for comparison. No database, environment file, or paid model API is used.

## Segment classification

Each matrix uses expected labels as rows and actual predictions as columns. `match` is the positive class for precision, recall, and F1; unknown evidence remains a separate third class.

| Segment | Precision | Recall | F1 | Exact agreement | Expected unknown | Predicted unknown |
|---|---:|---:|---:|---:|---:|---:|
| consistent | 100.00% | 100.00% | 100.00% | 100.00% | 400 | 400 |
| loyalty_candidate | 100.00% | 100.00% | 100.00% | 100.00% | 400 | 400 |
| declining_activity | 100.00% | 100.00% | 100.00% | 100.00% | 600 | 600 |
| dormant | 100.00% | 100.00% | 100.00% | 100.00% | 600 | 600 |
| payment_reminder | 100.00% | 100.00% | 100.00% | 100.00% | 400 | 400 |

All five statuses agree together for **5,000/5,000** customers.

### consistent

| Expected \ Predicted | match | no_match | insufficient_evidence |
|---|---:|---:|---:|
| match | 3000 | 0 | 0 |
| no_match | 0 | 1600 | 0 |
| insufficient_evidence | 0 | 0 | 400 |

### loyalty_candidate

| Expected \ Predicted | match | no_match | insufficient_evidence |
|---|---:|---:|---:|
| match | 1800 | 0 | 0 |
| no_match | 0 | 2800 | 0 |
| insufficient_evidence | 0 | 0 | 400 |

### declining_activity

| Expected \ Predicted | match | no_match | insufficient_evidence |
|---|---:|---:|---:|
| match | 800 | 0 | 0 |
| no_match | 0 | 3600 | 0 |
| insufficient_evidence | 0 | 0 | 600 |

### dormant

| Expected \ Predicted | match | no_match | insufficient_evidence |
|---|---:|---:|---:|
| match | 200 | 0 | 0 |
| no_match | 0 | 4200 | 0 |
| insufficient_evidence | 0 | 0 | 600 |

### payment_reminder

| Expected \ Predicted | match | no_match | insufficient_evidence |
|---|---:|---:|---:|
| match | 1400 | 0 | 0 |
| no_match | 0 | 3200 | 0 |
| insufficient_evidence | 0 | 0 | 400 |

## Strategy selection

| Expected \ Predicted | offer_or_reward | appreciation_without_offer | billing_reminder | hold | no_outreach |
|---|---:|---:|---:|---:|---:|
| offer_or_reward | 800 | 0 | 0 | 0 | 0 |
| appreciation_without_offer | 0 | 400 | 0 | 0 | 0 |
| billing_reminder | 0 | 0 | 800 | 0 | 0 |
| hold | 0 | 0 | 0 | 2400 | 0 |
| no_outreach | 0 | 0 | 0 | 0 | 600 |

Strategy objective agreement: **5,000/5,000**. Wrong-strategy examples: **0**, recorded in `evaluation/wrong_strategy_examples.json`.

Complete decision agreement (all segment statuses, objective, reminder stage, selected offer ID): **5,000/5,000**.

## Grounded generation

Generated **2,000/2,000** actionable drafts with the local template provider. Application validation passed for **2,000**; independent raw-input checks passed for **2,000**.

Exact business fact slots: **6,400/6,400** (100.00%). Slots cover selected invoice/amount/due date and selected offer/title/code/benefit/terms/expiry, as applicable.

Independent checks read raw invoice and catalogue fields, recompute amount/expiry formatting, check action preconditions, and inspect numeric claims, identifiers, links, and support/activation statements. They do not call the application's message-spec helper. This bounded factual check cannot establish complete semantic correctness.

## Adversarial checks and refusals

**23/23** measured cases behaved as expected; **0** failed. Full case evidence is in `evaluation/adversarial_cases.json`.

| Case | Category | Result |
|---|---|---|
| invented_amount | edited_message_rejection | PASS |
| unapproved_link | edited_message_rejection | PASS |
| sensitive_account | edited_message_rejection | PASS |
| threatening_language | edited_message_rejection | PASS |
| invented_refund | edited_message_rejection | PASS |
| altered_due_date | edited_message_rejection | PASS |
| missing_support_path | edited_message_rejection | PASS |
| semantic_unverified_claim_probe | edited_message_rejection | PASS |
| subject_header_injection | edited_message_rejection | PASS |
| stale_source | policy_hold | PASS |
| unknown_invoice_evidence | policy_hold | PASS |
| explicit_dispute | policy_hold | PASS |
| billing_opt_out | policy_hold | PASS |
| billing_permission_unknown | policy_hold | PASS |
| no_draft_source_stale | nonactionable_generation_refusal | PASS |
| no_draft_inactive_no_outreach | nonactionable_generation_refusal | PASS |
| no_draft_expired_offer_no_outreach | nonactionable_generation_refusal | PASS |
| no_draft_open_dispute | nonactionable_generation_refusal | PASS |
| feedback_instruction_not_executed | input_instruction_isolation | PASS |
| provider_refusal | stubbed_provider_refusal | PASS |
| provider_incomplete | stubbed_provider_refusal | PASS |
| provider_invalid_schema | stubbed_provider_refusal | PASS |
| provider_hostile_copy | stubbed_provider_refusal | PASS |

Provider refusal/incomplete/schema/hostile-output cases use local stub responses to exercise actual failure-handling code. They do not measure live OpenAI refusal frequency. Any failed semantic probe is an observed validator limitation, not hidden as a success.

## Decision-feature inspection

Bundled counterfactual changes to personal attributes, display name/email, and raw feedback left **5,000/5,000** decisions unchanged. See `DECISION_FEATURES.md` for the inspected feature list and limits. This does not prove absence of proxy effects or fairness in a real population.

## Message usefulness

A separate assistant assessed **6/6** exact template drafts with the five-criterion usefulness-v1 rubric. Scores are **automated judgments**, not human ratings. All 6 message hashes were verified against the sampled subject/body.

| Criterion | Mean score / 5 |
|---|---:|
| relevance | 5.000 |
| clarity | 3.500 |
| personalization | 2.833 |
| call_to_action | 3.167 |
| tone | 4.500 |

Overall mean across the supplied criterion scores: **3.800/5**. Per-message scores, rationales, and limits are in `evaluation/usefulness_assessments.json`.

The lowest-scoring criteria in this automated sample are personalization and call to action. They are priorities for further review; improving wording should preserve verified facts and approved support routes.

No independent human usefulness study has been performed. The selected six-message sample is small and deliberately varied, not random. These template assessments do not establish live OpenAI quality or inter-rater reliability.

## Separate live OpenAI sample

A separately executed live run attempted **6** calls using `gpt-6-astra` with `high` reasoning. It generated **5** drafts; **1** attempts returned no accepted draft. This evaluator only re-read saved evidence and made no model requests.

Among the **5 produced drafts**, rechecked application validation passed for **5** and independent fact checks passed for **5**. Across all attempts these are 5/6 and 5/6, respectively. Raw fact slots in produced drafts: **16/16**. Saved draft hashes and source facts were verified.

| Live scenario | Draft produced | Recorded outcome |
|---|---|---|
| appreciation | Yes | passed |
| loyalty_offer | Yes | passed |
| decline_offer | No | Generated wording failed validation; no draft was created. Choose template generation or retry. |
| dormant_offer | Yes | passed |
| billing_first | Yes | passed |
| billing_stage3 | Yes | passed |

A rejected generation is retained as an observed failure to produce an accepted draft. Its wording was not returned, so it is not scored and no specific unseen wording is inferred. The failed call was not retried to improve the reported result.

A separate assistant rated **5** exact live drafts. These are automated judgments, kept separate from template scores and human ratings.

| Criterion | Live-sample mean / 5 |
|---|---:|
| relevance | 4.800 |
| clarity | 3.600 |
| personalization | 3.000 |
| call_to_action | 3.400 |
| tone | 4.400 |

Live-sample overall mean: **3.840/5**. Rationales and exact-message hashes are in `evaluation/openai_usefulness_assessments.json`.

Template and live usefulness means are not a direct comparison: the live summary excludes its rejected decline case, so the scored sets contain six and five messages respectively. Both are small selected samples.

This six-call convenience sample demonstrates provider integration and bounded facts, not production reliability or general model quality. No message was approved or delivered by the live evaluation script.

## Reproduction

```sh
.venv/bin/python scripts/evaluate_submission.py --include-openai
.venv/bin/python -m pytest tests/test_evaluation.py -q
```

The JSON report records dataset hashes, policy content hash, and implementation hashes. Predictions and per-draft findings are retained separately. Outputs are deterministic for the same fixture files and implementation.

## Interpretation limits

- Scenario-balanced synthetic fixtures are authored to exercise known rules, so high agreement is regression evidence, not a representative generalization estimate.
- The dataset does not supply real human sentiment, churn, commercial outcomes, or approved production treatment labels.
- The 2,000-draft benchmark uses templates. Any integrated live-provider evidence is a separate six-call synthetic sample; neither evaluates production delivery or real account actions.
- Conservative validation and finite adversarial cases cannot prove safety or semantic completeness. Exact-revision human review remains required.
- The feature counterfactual test changes one bundle of excluded attributes on finite synthetic records; it is not a comprehensive fairness study.
