#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PROVIDER=demo
exec bash scripts/start.sh
