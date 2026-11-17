#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m app.render_bootstrap
exec python -m streamlit run ui/streamlit_app.py \
  --server.address=0.0.0.0 --server.port="${PORT:-10000}" \
  --server.headless=true --server.fileWatcherType=none \
  --browser.gatherUsageStats=false
