"""Install pinned official CPU llama.cpp and the official Hy-MT2 Q4_K_M checkpoint.

Run with the project's Python. No root permissions or package-manager changes.
"""
import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = 'b11849'
MODEL_REVISION = 'a0c709d9fac510f2c807aa3af52872340dc37a4a'
MODEL_NAME = 'Hy-MT2-1.8B-Q4_K_M.gguf'
MODEL_SIZE = 1133080448


def download(address, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    print(f'Downloading {destination.name}', flush=True)
    try:
        with urllib.request.urlopen(address, timeout=60) as response, partial.open('wb') as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        partial.replace(destination)
    except Exception:
        raise RuntimeError(f'下载未完成，可重新运行安装脚本：{address}') from None


def main():
    if sys.platform != 'linux' or platform.machine() not in ('x86_64','AMD64'):
        raise SystemExit('此安装器适用于 Linux x86_64 / WSL。其他平台请自备 llama-server 或使用 compatible 后端。')
    tools = ROOT / 'tools/llama.cpp'
    if not (tools / 'llama-server').is_file():
        with tempfile.TemporaryDirectory(prefix='translation-setup-') as temporary:
            archive = Path(temporary) / 'llama.tar.gz'
            download(f'https://github.com/ggml-org/llama.cpp/releases/download/{VERSION}/llama-{VERSION}-bin-ubuntu-x64.tar.gz', archive)
            tools.mkdir(parents=True, exist_ok=True)
            subprocess.run(['tar','-xzf',str(archive),'-C',str(tools),'--strip-components=1'],check=True)
        (tools / 'VERSION').write_text(VERSION + '\n')
    model = ROOT / 'models/Hy-MT2-1.8B-GGUF' / MODEL_NAME
    if not model.is_file() or model.stat().st_size != MODEL_SIZE:
        download(f'https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/{MODEL_REVISION}/{MODEL_NAME}', model)
    if model.stat().st_size != MODEL_SIZE:
        raise SystemExit('权重大小不匹配，请重新运行安装脚本。')
    if '--verify' in sys.argv:
        # Hugging Face LFS oid is the checkpoint SHA256, distinct from the git blob oid.
        import json
        with urllib.request.urlopen(f'https://huggingface.co/api/models/tencent/Hy-MT2-1.8B-GGUF/tree/{MODEL_REVISION}',timeout=30) as response:
            rows = json.load(response)
        expected = next(row['lfs']['oid'] for row in rows if row['path'] == MODEL_NAME)
        hasher = hashlib.sha256()
        with model.open('rb') as source:
            for chunk in iter(lambda: source.read(4*1024*1024), b''): hasher.update(chunk)
        if hasher.hexdigest() != expected:
            raise SystemExit('权重 SHA256 不匹配。')
        print('Model SHA256 verified.')
    print('Translation model ready. Start with: bash scripts/start-cpu.sh')


if __name__ == '__main__': main()
