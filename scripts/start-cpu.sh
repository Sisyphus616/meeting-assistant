#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PROVIDER=qwen
export DEVICE=cpu
export MODEL_DTYPE="${MODEL_DTYPE:-bfloat16}"
# Translation settings are read from .env. Default: dedicated Hy-MT2 Q4_K_M.
if [[ -f models/Qwen3-ASR-0.6B-hf/model.safetensors && -z "${ASR_MODEL:-}" ]]; then
  export ASR_MODEL="$PWD/models/Qwen3-ASR-0.6B-hf"
fi
if [[ -f models/Qwen3-0.6B/model.safetensors && -z "${LLM_MODEL:-}" ]]; then
  export LLM_MODEL="$PWD/models/Qwen3-0.6B"
fi
exec bash scripts/start.sh
