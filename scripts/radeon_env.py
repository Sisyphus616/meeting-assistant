"""Run a command with the Radeon Cloud notebook's public runtime settings.

SSH shells do not inherit the notebook container's proxy and ROCm settings.
Read a fixed allowlist from PID 1; never copy credentials or write proxy values
to disk. Explicit settings in the current shell take precedence.
"""
import os
from pathlib import Path
import sys


def main():
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python3 scripts/radeon_env.py COMMAND [ARGS...]')
    keys = {
        'LD_LIBRARY_PATH', 'HIP_FORCE_DEV_KERNARG', 'HSA_NO_SCRATCH_RECLAIM',
        'TORCH_BLAS_PREFER_HIPBLASLT', 'PYTHONPATH', 'HTTP_PROXY', 'HTTPS_PROXY',
        'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy',
        'no_proxy', 'HF_ENDPOINT', 'PIP_INDEX_URL',
    }
    try:
        platform = dict(item.split('=', 1) for item in
                        Path('/proc/1/environ').read_text().split('\0') if '=' in item)
    except (OSError, UnicodeError):
        platform = {}
    for key in keys:
        if key in platform:
            os.environ.setdefault(key, platform[key])
    if Path('/opt/venv/bin').is_dir():
        os.environ['PATH'] = '/opt/venv/bin:' + os.environ.get('PATH', '/usr/bin:/bin')
    os.environ.setdefault('PYTHONUNBUFFERED', '1')
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)


if __name__ == '__main__':
    main()
