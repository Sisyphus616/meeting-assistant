#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$PWD" != /workspace/* || ! -x /opt/venv/bin/python ]]; then
  echo '请在 Radeon Cloud 的 /workspace 项目目录运行；需要镜像预装的 /opt/venv。' >&2
  exit 1
fi
python3 - <<'PY'
import json
import pathlib
import subprocess
import sys

base = '/opt/venv/bin/python'
if not pathlib.Path('.venv/bin/python').exists():
    subprocess.run([sys.executable, '-m', 'venv', '.venv'], check=True)
query = 'import json,sys,sysconfig; print(json.dumps([list(sys.version_info[:2]),sysconfig.get_path("purelib")]))'
platform_version, platform_site = json.loads(subprocess.check_output([base, '-c', query]))
project_version, project_site = json.loads(subprocess.check_output(['.venv/bin/python', '-c', query]))
if platform_version != project_version:
    raise SystemExit('项目与平台 Python 版本不同，请使用与 /opt/venv 相同版本重新创建 .venv。')
if not (pathlib.Path(platform_site) / 'torch').is_dir():
    raise SystemExit('镜像 /opt/venv 缺少 ROCm PyTorch，请更换为 AMD OneClick Base。')
# A nested venv with --system-site-packages does not inherit its parent venv.
# Borrow the image packages explicitly; install overrides only in this project.
(pathlib.Path(project_site) / 'platform-rocm.pth').write_text(platform_site + '\n')
PY
python3 scripts/radeon_env.py .venv/bin/python -m pip install --no-cache-dir -r requirements.txt -r requirements-models.txt
python3 scripts/radeon_env.py .venv/bin/python -c 'import torch; assert torch.version.hip, "项目环境未使用 ROCm PyTorch，请先修正 .venv"; print("ROCm torch:", torch.__version__, torch.version.hip)'
if [[ ! -f .env ]]; then cp configs/radeon.env.example .env; fi
echo '依赖准备完成；请按 docs/RADEON_CLOUD.md 先验证实际 GPU 计算，再下载模型并启动。'
