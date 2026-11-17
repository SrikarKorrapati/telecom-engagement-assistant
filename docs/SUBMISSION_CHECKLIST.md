# Submission evidence checklist

This checklist distinguishes implemented prototype evidence from external validation that has not been performed. All customer records and offers are synthetic; all delivery is mocked.

| Submission item | Evidence | Status and limits |
|---|---|---|
| Working local application | Streamlit reviewer, direct Python service, PostgreSQL, local startup/stop scripts | Implemented; local single-user prototype. |
| Complete FR-01–FR-10 path | Profile → independent classification → strategy/offer/reminder controls → grounded draft → validation → exact-revision review → delivery gate; audit throughout | Implemented; see repository README and walkthrough. |
| Explicit synthetic inputs | `app/profiles.py`, profile API, Add profile UI | Implemented with strict types, unknown values preserved, synthetic marker and reserved contacts; extra attributes rejected. |
| Compact data model | Seven tables with relational identities and JSONB context/snapshots | Implemented; schema initialization/additive prototype migrations are not a full production migration programme. |
| Representative fixture coverage | `datasets/telecom_5000/`, dictionary, manifest, generator, validation report | 5,000 records across 25 deliberately balanced scenarios; not representative of a real population. |
| Classification metrics | `EVALUATION_REPORT.md`, `evaluation/metrics.json`, `evaluation/predictions.jsonl` | Actual predictions compared to separate authored labels; per-segment 3×3 matrices, match precision/recall/F1, unknown counts, exact agreement. |
| Strategy metrics | Same report plus `evaluation/wrong_strategy_examples.json` | Full strategy matrix and recorded mismatches; complete-decision agreement includes reminder stage and offer ID. |
| Grounded generation | `evaluation/template_findings.jsonl` | Every actionable template draft is measured; application validation plus independent raw-source fact checks. This does not measure live model generalization. |
| Adversarial and refusal checks | `evaluation/adversarial_cases.json` | In-memory message edits, policy holds, nonactionable generation rejection, input-instruction isolation, and stubbed provider failure handling. No paid API calls or database mutations. |
| Configurable content controls | `app/policies/content.json`, `app/content.py`, content/validation tests | One current configuration, updated in place; frozen snapshots and hashes preserve history. Heuristic checks still require human review. |
| Decision-feature transparency | `DECISION_FEATURES.md`, allowlists in `app/policy.py` | Enforced feature boundaries, code inspection, and counterfactual tests; not a generalized fairness proof. |
| Usefulness rubric | `app/rubric.py`, review API/form, stored score/source fields | Five criteria, 1–5 scale, separate human/automated/unspecified provenance; form implemented. |
| Six-sample usefulness evidence | `evaluation/usefulness_samples.json`, separate `evaluation/usefulness_assessments.json` | Six separate assistant judgments labelled automated, hash-bound to exact template drafts; no fabricated human scores. |
| Independent human usefulness study | None | **Not performed.** A review form or assistant assessment is not a human study. |
| Live OpenAI evidence | `evaluation/openai_samples.json`, separately assessed successful drafts | Six `gpt-6-astra` / high-reasoning attempts: five accepted drafts, one validation rejection with no returned draft. Failure retained, not retried or scored. Saved artifacts are rechecked without making new calls; no population-level model score is claimed. |
| Delivery correctness | Workflow/acceptance tests, PostgreSQL concurrent-delivery checks | Approval bound to current exact revision, current-evidence recheck, unique action key, and mock-only outcome. |
| Demonstration | `DEMO_WALKTHROUGH.md` | Six stable-ID cases plus inactive control, input form, ratings, holds, and audit evidence. |
| Reproducibility | `scripts/evaluate_submission.py`, dataset seed/manifest, implementation hashes, `tests/test_evaluation.py` | Recomputes from inputs; labels never populate predictions; use the documented reference time. |
| Production readiness | README roadmap | **Not claimed.** Authentication, approved real policies, real-system integration, operational controls, real evaluation data, and actual messaging remain future work. |

## Reproduction commands

```sh
.venv/bin/python scripts/evaluate_submission.py
.venv/bin/python scripts/evaluate_submission.py --include-openai
.venv/bin/python -m pytest tests/test_evaluation.py -q
.venv/bin/python -m pytest -q
.venv/bin/python datasets/telecom_5000/validate.py --check-reproducibility
```

PostgreSQL race tests run only when `TEST_POSTGRES_URL` is configured. They use temporary schemas to isolate test data. Do not replace a skipped database integration test with a claim that it ran; retain the actual test output for the submission.

The integration verification completed by the implementation team for this submission ran `TEST_POSTGRES_URL=<isolated-local-PostgreSQL> .venv/bin/python -m pytest tests datasets/telecom_5000/test_dataset.py -q`: **385 passed, zero skips**. It included the two PostgreSQL concurrent-delivery tests and the legacy-review migration test. The submission-evaluation suite contributes eight focused metric, fact, and provenance checks within that combined test tree.

## Evidence interpretation

Read each metric with its denominator and source. Classification and strategy scores measure deterministic regression agreement on authored synthetic cases. Groundedness measures fixed fact slots and explicit bounded checks. Refusal tests include local provider stubs. Usefulness scores are separate automated judgments on six selected template messages and, separately, the five produced live messages. The sixth live attempt is an unscored rejection. None of these is an independent human study, a real telecom field experiment, or a guarantee of complete semantic safety.
