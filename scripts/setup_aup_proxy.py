"""Install the Notebook proxy in persistent user packages, then enable it."""
import importlib.util
import json
import os
from pathlib import Path
import site
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    if importlib.util.find_spec('jupyter_server') is None:
        raise SystemExit('请使用运行 AUP Notebook 服务的 Python 执行；当前 Python 找不到 jupyter_server。')
    if not site.ENABLE_USER_SITE:
        raise SystemExit('当前 Python 禁用了用户包目录，请联系平台启用代理扩展；尚未修改环境。')
    python = ROOT / '.venv/bin/python'
    if not python.exists():
        raise SystemExit('请先运行 python3 scripts/setup_aup.py 准备项目 pip。')
    version = json.loads(subprocess.check_output([
        str(python), '-c', 'import json,sys; print(json.dumps(list(sys.version_info[:2])))']))
    if tuple(version) != sys.version_info[:2]:
        raise SystemExit('项目与 Notebook Python 版本不同，请先使用相同版本重新准备项目环境。')
    # --prefix uses project pip without changing externally managed system Python.
    # Unlike --target, it reuses compatible dependencies already in the image.
    prefix = Path(site.getuserbase())
    print(f'将 Notebook 代理扩展安装到用户目录：{prefix}', flush=True)
    subprocess.run([
        str(python), '-m', 'pip', 'install', '--no-cache-dir', '--prefix', str(prefix),
        '-r', str(ROOT / 'requirements-aup-proxy.txt'),
    ], check=True, env=dict(os.environ, PIP_USER='false'))
    # A fresh process discovers the user site even if pip just created it.
    subprocess.run([sys.executable, '-c', '''import jupyter_server_proxy
from jupyter_server.extension.manager import ExtensionPackage
assert ExtensionPackage(name="jupyter_server_proxy", enabled=True).validate()
print("Notebook Python can load proxy:", jupyter_server_proxy.__file__)
'''], check=True)

    from jupyter_core.paths import jupyter_config_dir
    directory = Path(jupyter_config_dir()) / 'jupyter_server_config.d'
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / 'meeting-assistant-proxy.json'
    content = json.loads(config.read_text()) if config.exists() else {}
    content.setdefault('ServerApp', {}).setdefault('jpserver_extensions', {})[
        'jupyter_server_proxy'] = True
    temporary = config.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(content, indent=2) + '\n')
    temporary.replace(config)
    print(f'扩展启用配置已保存：{config}', flush=True)
    print('请保存工作，在 Hub 控制页停止并重启 Notebook 服务器，再运行 bash scripts/start.sh。', flush=True)
    print('重启 Notebook 内核或刷新网页不会加载新的代理路由。', flush=True)


if __name__ == '__main__':
    main()
