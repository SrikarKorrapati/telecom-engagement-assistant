# Free deployment: Streamlit Community Cloud and Neon

This deployment uses free app hosting and Neon's Free PostgreSQL plan. Neither
requires a payment card. OpenAI generation uses the existing API account and its
balance; it is not included in the hosting plan.

1. Create a Neon project on the Free plan. Use its default PostgreSQL version and a region near the
   Streamlit host. Copy the direct connection string with TLS (`sslmode=require`).
2. In Streamlit Community Cloud, deploy this repository's `codex/free-deployment`
   branch, with `streamlit_cloud.py` as the main file and **Python 3.11** selected.
3. In Advanced settings, supply these secrets as top-level TOML strings:

   ```toml
   DATABASE_URL = "postgresql://.../neondb?sslmode=require"
   DEMO_ACCESS_PASSWORD = "a-random-password-at-least-16-characters-long"
   OPENAI_API_KEY = "your-existing-key"
   OPENAI_MODEL = "gpt-6-astra"
   OPENAI_REASONING_EFFORT = "high"
   ```

4. Deploy and sign in with the demo password. The first authenticated visit creates
   the schema and imports the 5,000 bundled synthetic customers. No startup step
   calls OpenAI or records deliveries. Subsequent starts preserve existing data.

The free host may sleep when idle, so the first visit after inactivity can take
longer. Stay within the free plans' database storage, compute and transfer limits.
The seeded evidence is dated; **Add profile** creates a fresh synthetic example
when older records no longer qualify for outreach.

## Customer dashboard

Create a second Streamlit app from the same branch with
`streamlit_dashboard_cloud.py` as its main file and **Python 3.11** selected.
Copy only `DATABASE_URL` and `DEMO_ACCESS_PASSWORD` from the reviewer into the
dashboard's secrets. Open the reviewer once first to initialize the shared database.

The dashboard reads live counts, segment and strategy charts, and recent activity
from that database. Customer search browses the bundled 5,000 synthetic records;
its stored strategy labels are historical sample labels, not current evaluations.
The dashboard does not initialize or modify the database and needs no OpenAI key.

## Durable configuration

The cloud entry point forces `POLICY_STORAGE=database`. It adds one optional
`hosted_configuration` table containing a single JSON document with both policy
and message-content settings. The seven workflow tables remain unchanged.
The local launcher continues to edit the existing JSON files.

Hosted policy updates lock the configuration row, check the edit's original hash,
validate both documents, and commit the settings and audit event in one database
transaction. Readers use the database, so losing the host's local files or
restarting the app cannot reset saved settings. Initializing again does not
overwrite them with Git defaults.

Keep one reviewer instance; workflow actions and configuration saves still
use a host-local shared lock. The separate read-only dashboard does not perform
these actions. This shared demo password gate does not establish
individual reviewer identity. Delivery remains mocked.

## Validation

Run `python -m pytest -q` using the packages in `requirements.txt`. Hosted-storage
tests verify persistence, stale-edit rejection and atomic rollback of settings
and audit events. A separate PostgreSQL smoke database can verify the complete
startup path without touching the local demo database or calling OpenAI.

References: [Streamlit hosting](https://docs.streamlit.io/deploy/streamlit-community-cloud),
[Neon Free plan](https://neon.com/pricing).
