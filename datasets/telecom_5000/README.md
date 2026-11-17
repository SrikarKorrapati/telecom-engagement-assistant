# Synthetic postpaid telecom dataset

5,000 fictional customers, **25 deliberately balanced scenarios × 200 records**. This is a reproducible prototype demonstration and regression dataset. It is not sampled from, calibrated to, or representative of any telecom operator or real population. All policy thresholds and offers are illustrative.

The supplied snapshot uses **2026-11-17 12:00:00 Asia/Kolkata** (`2026-11-17T06:30:00+00:00`) and seed `20261117`. Decisions should be evaluated at that reference time. Evaluating a saved snapshot much later correctly makes its evidence stale; regenerate to demonstrate at a new time.

| File | Purpose |
|---|---|
| `customers.jsonl` | 5,000 application-input customer objects: `id`, `name`, `email`, `scenario`, `context`, `version`. |
| `offers.json` | Three synthetic versions of `LOYALTY_2GB`: expired, active eligible, and inactive. |
| `evaluation_contexts.jsonl` | Input history keyed by `customer_id`: reference `as_of` and synthetic prior delivery summaries. |
| `expected_labels.jsonl` | Separate evaluation labels: all five segment results, selected objective, reason code, reminder stage, and offer ID. Never send these labels to the model or policy evaluator. |
| `manifest.json` | Record count, reference time, seed, version, scenario counts, SHA-256 checksums. |
| `DATA_DICTIONARY.md` | Fields, relationships, intentional missingness, and conventions. |
| `VALIDATION_REPORT.md` / `validation_report.json` | Independent integrity/oracle results and comparison with the app policy. |
| `generate.py` / `validate.py` / `test_dataset.py` | Reproduction, validation, and boundary tests. |

Every customer has `context.synthetic=true`; names are explicitly fictional and email addresses use reserved `example.com`. Each customer contains support-chat, complaint, survey, and email feedback. Text is synthetic and templated. There are no sentiment labels or sentiment-analysis workflow.

## Reproduce and validate

From the repository root, using Python 3.11 or later:

```sh
python3 datasets/telecom_5000/generate.py --as-of 2026-11-17T12:00:00+05:30 --seed 20261117
python3 datasets/telecom_5000/validate.py --check-reproducibility
python3 -m pytest datasets/telecom_5000/test_dataset.py -q
```

`--as-of` is required and must contain a timezone. `--seed` changes the reproducible numeric variation and customer UUIDs. `--output-dir` writes an alternative dataset without overwriting the shipped files. There are always 5,000 records; each scenario retains 200 records. The generator uses only Python's standard library and never imports the application evaluator, reads environment files, calls APIs, or sends messages.

The validator performs its own numerical segment checks, financial/time/reference integrity checks, and manually authored strategy-precondition checks. It then compares the application policy against the separately authored labels. Use `--skip-app-check` for standalone integrity/oracle validation when `app.policy` is unavailable. Reproducibility regenerates into a temporary subfolder and compares all four payload checksums.

## Evaluate one customer

Join `customers.jsonl`, `evaluation_contexts.jsonl`, and `expected_labels.jsonl` by customer ID. Construct the application snapshot from customer fields, the offer catalogue, and the evaluation context:

```python
snapshot = {
    "customer_id": customer["id"],
    "customer_name": customer["name"],
    "customer_email": customer["email"],
    "customer_version": customer["version"],
    "context": customer["context"],
    "offers": offers,
    "as_of": evaluation_context["as_of"],
    "recent_deliveries": evaluation_context["recent_deliveries"],
}
result = evaluate(snapshot)
```

`scenario` is descriptive demo metadata; it is not a policy feature. Do not train a classifier on scenario names or pass expected labels into evaluation. The policy evaluates the evidence directly. Independent segment rules may all match or fail separately; labels are deliberately not mutually exclusive.

For database import, map only the customer fields into `customers`, and offers into `offers`. The histories in `evaluation_contexts.jsonl` are fictional fixture evidence for pure-function evaluation. A database-backed run must have the corresponding valid mock-delivery history to reproduce frequency-cap and duplicate-action scenarios. Importing customer rows alone will not reconstruct those historical delivery records or approvals. This package performs no database writes.

## Coverage and limits

- Overlap: consistent + loyalty + decline, and billing reminder + declining activity.
- Strategies: offers, appreciation, billing stages 1–3, holds, and no outreach.
- Holds: stale source, missing invoices, incomplete usage, disputes, known outage, denied/unknown permission, unknown redemption history, contact-frequency limit, duplicate action, and recent reminder.
- Offer decisions: eligible current version, recent redemption, expired-only eligible catalogue, and inactive version excluded.
- All three segment statuses are represented. Missing values remain `null`; missing observations are never converted to zero activity.

The dataset validates policy plumbing and demonstrations. It does not establish model accuracy, real customer segmentation quality, offer effectiveness, fairness, consent compliance, or population prevalence. Feedback is not suitable as a sentiment benchmark without separately curated human labels. Historical amounts and timing are fictional but internally coherent. No customer is contacted and no offer is provisioned.
