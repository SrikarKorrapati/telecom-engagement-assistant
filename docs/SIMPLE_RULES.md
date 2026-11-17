# Current simple rules

The application chooses the first applicable outcome:

| Outcome | Condition |
|---|---|
| Hold | Required data is missing/invalid, a dispute is open, or contact is blocked. |
| Billing reminder | A bill is unpaid and overdue, and its next configured reminder stage is due. |
| Offer | The customer is loyal, declining, or dormant, and an offer is eligible. |
| Appreciation | Latest completed bill paid on time; subscription active; usage positive and not declining; no earlier action selected. |
| No outreach | Enough evidence is available, but none of the actions applies, or the action is already complete. |

**Loyal:** active for at least 12 months. **Declining:** usage is lower than in the previous period, but above zero. **Dormant:** active subscription with zero usage. **Consistent:** latest completed bill paid fully on or before its due date. Older late payments do not change that result.

Usage periods are complete 30-day windows. An eligible offer is active, unexpired, available for the customer's plan and email channel, and outside its redemption cooldown (90 days in the sample catalogue). Its approved terms must be available. Billing advances one stage at a time: courteous from day 1 overdue, direct from day 8, and firm from day 15. Each later stage also requires at least 7 days after the previous reminder. These illustrative thresholds, tones, and maximum stage (1–3) are configurable in `app/policies/policy.json`. The support/dispute path remains in every stage.

Basic controls remain: current identifiable evidence (maximum age 48 hours), valid balances/dates, no open dispute, permission for the selected contact purpose, at least 7 days between contacts, and no duplicate logical outreach. Known or unknown outage status blocks offers/appreciation when usage cannot be trusted. Missing information is never silently treated as zero or permission.

## One live configuration

`app/policies/policy.json` is the single current policy. `app/policies/content.json` holds the current message settings and prompts. The dashboard updates these files in place; it has no new-version field. The compatibility `version` fields retain stable names (`prototype` and `telecom-facts`).

Content hashes detect changes, reject saves from outdated forms, and require a fresh evaluation before further drafting, editing, critique, approval or delivery. Dashboard saves wait for in-progress service mutations. Every evaluation retains its frozen inputs/configuration and the audit log records changes. Saved history is not rewritten to match new rules.

Appreciation deduplication uses customer and month, independent of the policy name. Older appreciation keys are recognized when checking for duplicate outreach. Billing deduplication uses customer, invoice and stage; matching chronological reminder and delivery histories are required before advancing. Missing, conflicting, skipped, premature or future history holds the action. Offer records retain their historical identifiers and terms.

The bundled 5,000-record dataset, authored expected labels and earlier evaluation reports describe the earlier rules. They remain historical evidence; their scenario names do not determine current decisions. New evaluations apply the simple rules above. Historical scores must not be reported as measurements of this implementation.

The reviewer starts with **Ready for demo**: five live, eligible, uncontacted examples selected from actual evidence. **All customers** retains refusal scenarios and completed deliveries. Re-evaluating a completed local action reopens its original delivery without another run or send; synthetic history without a local delivery is a closed no-outreach result. The overview labels saved holds as historical; old rows are not rewritten.

The generator–critic loop and exact-message human approval still apply after strategy selection. All delivery remains mocked.
