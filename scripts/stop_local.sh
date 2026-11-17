#!/usr/bin/env bash
# Stop only recorded application processes and, optionally, this workspace's cluster.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_BIN="${PG_BINDIR:-/Library/PostgreSQL/16/bin}"
STOP_POSTGRES=0
if [[ "${1:-}" == "--postgres" ]]; then
  STOP_POSTGRES=1
elif [[ -n "${1:-}" ]]; then
  echo "Usage: scripts/stop_local.sh [--postgres]" >&2
  exit 2
fi

stop_service() {
  local name="$1" marker="$2" pidfile="$ROOT/.local/pids/$1.pid"
  [[ -f "$pidfile" ]] || return 0
  local saved_pid
  saved_pid="$(cat "$pidfile")"
  if [[ ! "$saved_pid" =~ ^[0-9]+$ ]]; then
    echo "Invalid PID file for $name; no process stopped." >&2
    return 1
  fi
  if kill -0 "$saved_pid" 2>/dev/null; then
    local command
    command="$(ps -p "$saved_pid" -o command=)"
    if [[ "$command" != *"$ROOT"* || "$command" != *"$marker"* ]]; then
      echo "PID for $name belongs to a different process; leaving it untouched." >&2
      return 1
    fi
    kill "$saved_pid"
    local attempts=0
    while kill -0 "$saved_pid" 2>/dev/null && [[ "$attempts" -lt 40 ]]; do
      sleep 0.25
      attempts=$((attempts + 1))
    done
    if kill -0 "$saved_pid" 2>/dev/null; then
      echo "$name is still stopping; PID file retained." >&2
      return 1
    fi
    echo "$name stopped."
  fi
  rm -f "$pidfile"
}

stop_service dataset dataset_dashboard.py
stop_service reviewer streamlit_app.py
# Clean up an API process recorded by a pre-service version of this launcher.
stop_service api app.main:app
if [[ "$STOP_POSTGRES" == 1 ]]; then
  PGDATA="$ROOT/.local/postgres"
  if [[ -L "$PGDATA" || -L "$ROOT/.local" ]]; then
    echo "Refusing to manage PostgreSQL through a symlink." >&2
    exit 1
  fi
  if [[ -f "$PGDATA/PG_VERSION" && -x "$PG_BIN/pg_ctl" ]] && "$PG_BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
    "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast -w stop
    rm -f "$ROOT/.local/postgres.started-by-script"
  fi
fi
