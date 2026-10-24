#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  task_python="${PYTHON:-}"
  if [[ -z "$task_python" ]]; then
    for candidate in python3.11 python3.12 python3.13 python3; do
      if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; assert (3,11) <= sys.version_info[:2] < (3,14)' 2>/dev/null; then
        task_python="$candidate"
        break
      fi
    done
  fi
  if [[ -z "$task_python" ]]; then
    echo '需要 Python 3.11–3.13。可通过 PYTHON=/path/to/python bash scripts/setup.sh 指定。' >&2
    exit 1
  fi
  # WSL /mnt/c metadata operations are slow for model packages. Keep only the
  # environment on Linux; source code and application data remain in the project.
  task_venv="${VENV_DIR:-.venv}"
  if [[ "$task_venv" == '.venv' && "$PWD" == /mnt/* && -d /proc/sys/fs/binfmt_misc ]]; then
    task_key=$("$task_python" -c 'import hashlib,os; print(hashlib.sha256(os.getcwd().encode()).hexdigest()[:12])')
    task_venv="${XDG_DATA_HOME:-$HOME/.local/share}/tingjian/venvs/$task_key"
  fi
  "$task_python" -m venv "$task_venv"
  if [[ "$task_venv" != '.venv' ]]; then ln -s "$task_venv" .venv; fi
fi
task_venv=$(cd .venv && pwd -P)
"$task_venv/bin/python" -m pip install -r requirements.txt
if [[ ! -f .env ]]; then cp .env.example .env; fi
case "${1:-}" in
  --cpu-models)
    "$task_venv/bin/python" -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    "$task_venv/bin/python" -m pip install -r requirements-models.txt
    "$task_venv/bin/python" scripts/setup_translation.py
    ;;
  --models)
    # Preserve the platform's correctly selected ROCm torch build.
    "$task_venv/bin/python" -c 'import torch' || { echo '请先安装与平台匹配的 PyTorch。' >&2; exit 1; }
    "$task_venv/bin/python" -m pip install -r requirements-models.txt
    ;;
  --dev) "$task_venv/bin/python" -m pip install -r requirements-dev.txt ;;
  '') ;;
  *) echo '用法：bash scripts/setup.sh [--dev|--cpu-models|--models]' >&2; exit 1 ;;
esac
echo '准备完成。运行：bash scripts/start.sh'
