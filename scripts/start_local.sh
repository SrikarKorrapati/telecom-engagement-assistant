#!/usr/bin/env bash
# Start only this workspace's local prototype. Never replace existing clusters.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
PG_BIN="${PG_BINDIR:-/Library/PostgreSQL/16/bin}"
PGDATA="$ROOT/.local/postgres"
LOCAL_PORT="${LOCAL_PG_PORT:-55432}"
EXTERNAL_DB=0
DATASET_DASHBOARD=0
LOAD_DATA=1
SEED_DEMO=0
for argument in "$@"; do
  case "$argument" in
    --external-db) EXTERNAL_DB=1 ;;
    --dataset-dashboard) DATASET_DASHBOARD=1 ;;
    --skip-load) LOAD_DATA=0 ;;
    --seed-demo) SEED_DEMO=1 ;;
    --help)
      echo "Usage: scripts/start_local.sh [--external-db] [--dataset-dashboard] [--skip-load] [--seed-demo]"
      exit 0 ;;
    *) echo "Unknown argument: $argument" >&2; exit 2 ;;
  esac
done
if [[ ! -x "$PYTHON" ]]; then
  echo "Create .venv and install the project first; see README.md." >&2
  exit 1
fi
mkdir -p "$ROOT/.local/logs" "$ROOT/.local/pids"
export TELECOM_DEFAULT_DATABASE_URL="postgresql+psycopg2://telecom@127.0.0.1:$LOCAL_PORT/telecom_demo"
# Shell variables take priority over .env. No key is printed or written.
DATABASE_URL="$("$PYTHON" - <<'PY'
import os
from dotenv import load_dotenv
load_dotenv('.env', override=False)
print(os.getenv('DATABASE_URL') or os.environ['TELECOM_DEFAULT_DATABASE_URL'])
PY
)"
export DATABASE_URL
unset TELECOM_DEFAULT_DATABASE_URL
IFS=$'\t' read -r DB_HOST DB_PORT DB_USER DB_NAME < <("$PYTHON" - <<'PY'
import os
from sqlalchemy.engine import make_url
url = make_url(os.environ['DATABASE_URL'])
if url.get_backend_name() != 'postgresql':
    raise SystemExit('Local application startup requires PostgreSQL; SQLite is used in tests only.')
print('\t'.join([url.host or 'localhost', str(url.port or 5432), url.username or '', url.database or '']))
PY
)
if [[ -z "$DB_HOST" || -z "$DB_USER" || -z "$DB_NAME" ]]; then
  echo "DATABASE_URL must specify PostgreSQL host, role, and database." >&2
  exit 1
fi
if [[ "$EXTERNAL_DB" == 0 && ( "$DB_HOST" == "127.0.0.1" || "$DB_HOST" == "localhost" ) && "$DB_PORT" == "$LOCAL_PORT" ]]; then
  if [[ ! -x "$PG_BIN/pg_ctl" || ! -x "$PG_BIN/initdb" ]]; then
    echo "Set PG_BINDIR to your PostgreSQL bin directory, or use --external-db." >&2
    exit 1
  fi
  if [[ ! "$DB_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ || ! "$DB_NAME" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    echo "Managed cluster requires simple role/database names; use --external-db otherwise." >&2
    exit 1
  fi
  if [[ -L "$PGDATA" || -L "$ROOT/.local" ]]; then
    echo "Refusing to manage PostgreSQL through a symlink." >&2
    exit 1
  fi
  OWN_RUNNING=0
  if [[ -f "$PGDATA/PG_VERSION" ]] && "$PG_BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
    OWN_RUNNING=1
  fi
  if [[ "$OWN_RUNNING" == 0 ]]; then
    if "$PG_BIN/pg_isready" -h "$DB_HOST" -p "$DB_PORT" >/dev/null 2>&1; then
      echo "Another PostgreSQL server is using the requested port. Use --external-db to connect intentionally." >&2
      exit 1
    fi
    if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
      "$PG_BIN/initdb" -D "$PGDATA" -U "$DB_USER" --auth-local=trust --auth-host=trust >"$ROOT/.local/logs/postgres-init.log" 2>&1
    fi
    "$PG_BIN/pg_ctl" -D "$PGDATA" -l "$ROOT/.local/logs/postgres.log" \
      -o "-h 127.0.0.1 -p $DB_PORT" -w start
    touch "$ROOT/.local/postgres.started-by-script"
  fi
  EXISTS="$("$PG_BIN/psql" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")"
  if [[ "$EXISTS" != "1" ]]; then
    "$PG_BIN/createdb" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -- "$DB_NAME"
  fi
fi

if [[ "$SEED_DEMO" == 1 ]]; then
  "$PYTHON" -m app.cli init-db --seed-demo
else
  "$PYTHON" -m app.cli init-db
  if [[ "$LOAD_DATA" == 1 ]]; then
    "$PYTHON" -m app.cli load-dataset "$ROOT/datasets/telecom_5000"
  fi
fi

port_available() {
  "$PYTHON" - "$1" <<'PY'
import socket, sys
with socket.socket() as test:
    test.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        test.bind(('127.0.0.1', int(sys.argv[1])))
        test.listen(1)
    except OSError:
        raise SystemExit(1)
PY
}

start_service() {
  local name="$1" port="$2" marker="$3"
  shift 3
  local pidfile="$ROOT/.local/pids/$name.pid"
  if [[ -f "$pidfile" ]]; then
    local saved_pid
    saved_pid="$(cat "$pidfile")"
    if [[ "$saved_pid" =~ ^[0-9]+$ ]] && kill -0 "$saved_pid" 2>/dev/null; then
      local command
      command="$(ps -p "$saved_pid" -o command=)"
      if [[ "$command" == *"$ROOT"* && "$command" == *"$marker"* ]]; then
        echo "$name is already running."
        return
      fi
      echo "PID file for $name points to another process; refusing to replace it." >&2
      exit 1
    fi
    rm -f "$pidfile"
  fi
  if ! port_available "$port"; then
    echo "Port $port is already in use; no existing process was stopped." >&2
    exit 1
  fi
  local service_pid
  service_pid="$("$PYTHON" - "$ROOT/.local/logs/$name.log" "$ROOT" "$PYTHON" "$@" <<'PY'
import os
import subprocess
import sys

log_path, working_directory, python, *arguments = sys.argv[1:]
environment = os.environ.copy()
environment['PYTHONUNBUFFERED'] = '1'
with open(log_path, 'ab', buffering=0) as log:
    process = subprocess.Popen(
        [python, *arguments],
        cwd=working_directory,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
print(process.pid)
PY
)"
  echo "$service_pid" > "$pidfile"
  local attempts=0
  while [[ "$attempts" -lt 60 ]]; do
    if ! kill -0 "$service_pid" 2>/dev/null; then
      echo "$name exited during startup. See .local/logs/$name.log." >&2
      exit 1
    fi
    if ! port_available "$port"; then
      echo "$name started on 127.0.0.1:$port."
      return
    fi
    sleep 0.5
    attempts=$((attempts + 1))
  done
  echo "$name did not bind port $port within 30 seconds. See its log." >&2
  exit 1
}

start_service reviewer 8501 "streamlit_app.py" -m streamlit run "$ROOT/ui/streamlit_app.py" \
  --server.address 127.0.0.1 --server.port 8501 --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false
if [[ "$DATASET_DASHBOARD" == 1 ]]; then
  start_service dataset 8502 "dataset_dashboard.py" -m streamlit run "$ROOT/ui/dataset_dashboard.py" \
    --server.address 127.0.0.1 --server.port 8502 --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false
fi
echo "Reviewer: http://127.0.0.1:8501"
echo "Database health: .venv/bin/python -m app.cli health"
if [[ "$DATASET_DASHBOARD" == 1 ]]; then echo "Dataset: http://127.0.0.1:8502"; fi
