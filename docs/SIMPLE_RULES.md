# Current simple rules

The application chooses the first applicable outcome:

| Outcome | Condition |
|---|---|
| Hold | Required data is missing/invalid, a dispute is open, or contact is blocked. |
| Billing reminder | A bill is unpaid, overdue, and has not already received a reminder. |
| Offer | The customer is loyal, declining, or dormant, and an offer is eligible. |
| Appreciation | Latest completed bill paid on time; subscription active; usage positive and not declining; no earlier action selected. |
| No outreach | Enough evidence is available, but none of the actions applies. |

**Loyal:** active for at least 12 months. **Declining:** usage is lower than in the previous period, but above zero. **Dormant:** active subscription with zero usage. **Consistent:** latest completed bill paid fully on or before its due date. Older late payments do not change that result.

Usage periods are complete 30-day windows. An eligible offer is active, unexpired, available for the customer's plan and email channel, and outside its redemption cooldown (90 days in the sample catalogue). Its approved terms must be available. Billing gets one courteous reminder per invoice, with no escalation.

Basic controls remain: current identifiable evidence (maximum age 48 hours), valid balances/dates, no open dispute, permission for the selected contact purpose, at least 7 days between contacts, and no duplicate logical outreach. Known or unknown outage status blocks offers/appreciation when usage cannot be trusted. Missing information is never silently treated as zero or permission.

## One live configuration

`app/policies/policy.json` is the single current policy. `app/policies/content.json` holds the current message settings and prompts. The dashboard updates these files in place; it has no new-version field. The compatibility `version` fields retain stable names (`prototype` and `telecom-facts`).

Content hashes detect changes, reject saves from outdated forms, and require a fresh evaluation before further drafting, editing, critique, approval or delivery. Dashboard saves wait for in-progress service mutations. Every evaluation retains its frozen inputs/configuration and the audit log records changes. Saved history is not rewritten to match new rules.

Appreciation deduplication uses customer and month, independent of the policy name. Older appreciation keys and billing reminder stages are recognized when checking for duplicate outreach. Offer records retain their historical identifiers and terms.

The bundled 5,000-record dataset, authored expected labels and earlier evaluation reports describe the earlier rules. They remain historical evidence; their scenario names do not determine current decisions. New evaluations apply the simple rules above. Historical scores must not be reported as measurements of this implementation.

The generator–critic loop and exact-message human approval still apply after strategy selection. All delivery remains mocked.
