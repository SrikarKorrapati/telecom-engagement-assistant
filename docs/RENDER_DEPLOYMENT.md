# Render deployment

The root `render.yaml` creates one Python web service, one PostgreSQL 16 database,
and a 1 GB persistent disk in Singapore. It targets `codex/render-deployment` and
disables automatic deploys. These are paid resources; review Render's displayed
monthly estimate before creating them. OpenAI usage is billed separately.

## Deploy

1. In Render, create a Blueprint from this repository and select the
   `codex/render-deployment` branch. Use `render.yaml` as the Blueprint path.
2. Enter `OPENAI_API_KEY` in Render's secret field. The Blueprint uses the same
   configured model and reasoning effort as the local application. Never put
   keys in Git, screenshots, or a deployment URL.
3. Review and create the resources. The database accepts connections only from
   Render's private network. The web service reads its internal connection string.
4. Wait for the web service to become healthy, then open its `onrender.com` URL.
   Read the generated `DEMO_ACCESS_PASSWORD` from the service's Environment tab
   and use it to sign in. Share that password only with intended demo reviewers.

The build installs `requirements-render.txt`. The startup script validates access
configuration, initializes the database, imports the bundled 5,000 synthetic
customers only into an empty database, and starts Streamlit on Render's `PORT`.
No startup step calls OpenAI, generates emails, or records deliveries. Existing
customers, reviews, offers and delivery history survive subsequent restarts.

The hosted database begins with the bundled dataset, not a copy of the local
database or its saved reviews. Evaluations are created on demand. The dataset
contains dated evidence; as it ages, some customers intentionally become
ineligible. **Add profile** creates a fresh synthetic example for a live demo.

## Persistent policy edits

Both policy readers and the dashboard editor use `POLICY_DATA_DIR`. On the first
start, the application copies the bundled policy and content JSON to the disk.
Later starts preserve them, including dashboard edits. Invalid persisted files
block startup instead of silently resetting settings. Git changes to the bundled
defaults do not overwrite the deployed policy; apply intentional changes through
the dashboard or an explicit migration.

Keep this service at one instance: configuration updates use a process-shared
file lock on this host. The optional standalone customer dashboard is not deployed
as a second service. Its entry point uses the same password gate if deployed later.

## Access and operations

The shared password protects the entire Streamlit workflow, including direct
policy links. Render requires a password of at least 16 characters and generates
a random 256-bit value. Successful sessions contain a fingerprint rather than
the password. Sign out clears session state; changing the environment password
invalidates prior sessions on their next interaction after restart.

This is a shared demo access gate, not per-reviewer authentication or an identity
audit. For organizational use, replace it with SSO and reviewer permissions.
All delivery remains mocked.

Render checks `/_stcore/health`. Database connectivity and schema initialization
must succeed before the listener starts; this liveness endpoint does not monitor
later database failures. Application errors report a database outage separately.

After a code update, manually deploy the latest commit in Render. A persistent
disk requires a brief service interruption during deploys. Use Render database
backups for relational data and disk snapshots for the two policy files. Do not
delete the database or disk to restart the application.

References: [Blueprint specification](https://render.com/docs/blueprint-spec),
[persistent disks](https://render.com/docs/disks),
[pricing](https://render.com/pricing).
