#!/usr/bin/env bash
# Start the CareLens demo: API + web app on http://localhost:8000
# Uses a local SQLite file and synthetic data only. Needs Python 3.12+.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
# Check for the packages, not the folder: an interrupted first run can leave
# an empty virtualenv behind.
if ! .venv/bin/python -c "import uvicorn, fastapi, sqlalchemy" 2>/dev/null; then
  echo "Installing Python packages..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r services/api/requirements.txt
fi

if [ ! -d apps/web/dist ]; then
  echo "Building the web app (needs Node 20+)..."
  (cd apps/web && npm install --no-audit --no-fund && npm run build)
fi

# Local by default: file database, no access code. "--hosted" loads the
# settings saved by scripts/configure_hosted.py (access code, private copy per
# tester, remote database) so hosted mode can be tried before deploying.
if [ "${1:-}" = "--hosted" ]; then
  if [ ! -f infra/.env ]; then
    echo "No infra/.env yet. Run: .venv/bin/python scripts/configure_hosted.py" >&2
    exit 1
  fi
  set -a; . infra/.env; set +a
  echo "Hosted mode (infra/.env). Testers enter this access code: ${CARELENS_ACCESS_CODE:-<not set>}"
else
  unset CARELENS_SANDBOXES CARELENS_ACCESS_CODE CARELENS_DATABASE_URL
fi

export CARELENS_ENV="${CARELENS_ENV:-demo}"
echo "CareLens demo: http://localhost:${PORT:-8000}   (synthetic data only)"
cd services/api
# HOST=0.0.0.0 lets a phone on the same Wi-Fi reach the demo.
exec ../../.venv/bin/python -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
