# Policy dashboard

Open [Policies](http://localhost:8501/?page=policies), or select **Policies** in the application sidebar.

| View | Controls |
|---|---|
| Segments | Independent segment conditions, payment history, tenure, activity thresholds, evidence age, and contact frequency. |
| Offers | Actual PostgreSQL offer versions, active/expired status, eligible plans, benefit, terms, expiry, and redemption interval. |
| Reminders | Overdue-day bands, tone, and minimum reminder interval. |
| Messages | Subject, courtesy wording, required statements, greeting, signature, tones, and blocked-language patterns. |

The main view contains summaries. Open an editing section to make a change, then explicitly save it. Browsing, switching tabs, and downloading the configuration do not write data or call a model.

Policy and content saves require a new version and validate the complete combined configuration. Invalid fields, inconsistent reminder stages, and invalid patterns are rejected. If another session changed the same configuration, refresh before retrying; the dashboard does not silently overwrite it.

Offer edits publish a new version of the selected code. Earlier active versions of that code are retired, while their original terms remain available for historical evidence. The fixture file under `datasets/` is not the live catalogue and is never rewritten by the dashboard.

Existing customer evaluations, drafts, and approvals retain their original snapshots. A changed policy or offer can block an old draft at the next workflow check. Create a fresh evaluation before drafting with the new configuration.

Policy/content live in the existing versioned JSON files, and offers live in PostgreSQL. Saves produce audit events; no new tables are required. This remains a local, single-user synthetic prototype with illustrative policies and mock delivery. It does not provide production policy approvals or authenticated administration.
