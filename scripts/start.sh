#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo '请先运行 bash scripts/setup.sh' >&2
  exit 1
fi
task_venv=$(cd .venv && pwd -P)
exec "$task_venv/bin/python" scripts/run.py
