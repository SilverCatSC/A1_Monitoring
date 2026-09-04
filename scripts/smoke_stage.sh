#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_DIR:=$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PROJECT_DIR"

DB_PATH="${PROJECT_DIR}/.stage_smoke.db"
TMP_CSV="/tmp/a1_stage_smoke.csv"
TMP_LOG="/tmp/a1_stage_smoke.log"
VENV_DIR=".tmp_smoke_env"

for p in "$DB_PATH" "$TMP_CSV" "$TMP_LOG"; do
  if [ -f "$p" ]; then
    rm -f "$p"
  fi
done

: "${DB_DIR:=./artifacts}"
mkdir -p "$DB_DIR"

cat > "$TMP_CSV" <<'CSV'
brand,model,vin,year
Nissan,Note,TESTVIN1234567890,2020
CSV

export DATABASE_DSN="sqlite:///$DB_PATH"
export EVIDENCE_DIR="$DB_DIR"

python3.12 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install -q -e ".[dev]"

python -m app.cli init
python -m app.cli import-source --path "$TMP_CSV"
python -m app.cli run-cycle

python -m app.cli serve > "$TMP_LOG" 2>&1 &
PID=$!
sleep 4
python3.12 - <<'PY'
import urllib.request

urls = [
    "http://127.0.0.1:8000/api/v1/health",
    "http://127.0.0.1:8000/api/v1/dashboard/kpi",
]
for u in urls:
    with urllib.request.urlopen(u, timeout=5) as resp:
        if resp.status != 200:
            raise SystemExit(f"bad status for {u}: {resp.status}")
        if not resp.read():
            raise SystemExit(f"empty response for {u}")
print("SMOKE_OK")
PY

kill "$PID" || true
wait "$PID" || true

rm -f "$DB_PATH" "$TMP_CSV" "$TMP_LOG"
rm -rf "$VENV_DIR"
deactivate
