"""Prepare the project on AUP without replacing the working ROCm torch."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    os.chdir(ROOT)
    if not ROOT.is_relative_to(Path('/home/jovyan')):
        raise SystemExit('请先将项目放到 /home/jovyan 下的持久化目录，再运行此脚本。')
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        raise SystemExit('项目需要 Python 3.11–3.13，请用平台中符合版本且可运行 ROCm torch 的 Python 执行。')

    # Run separately so an unhealthy GPU cannot hang the installer indefinitely.
    probe = '''import torch
print("Platform torch:", torch.__version__, "ROCm:", torch.version.hip, flush=True)
assert torch.version.hip and torch.cuda.is_available(), "需要平台自带的可用 ROCm torch"
x = torch.ones(2, device="cuda") * 3
torch.cuda.synchronize()
assert x.cpu().tolist() == [3.0, 3.0]
print("GPU calculation passed", flush=True)
'''
    try:
        run([sys.executable, '-u', '-c', probe], timeout=45)
    except subprocess.TimeoutExpired:
        raise SystemExit('GPU 运算检查超时，尚未安装依赖；请先排查平台环境。') from None

    import torch
    platform_site = Path(torch.__file__).resolve().parent.parent
    python = ROOT / '.venv/bin/python'
    if not python.exists():
        run([sys.executable, '-m', 'venv', '--system-site-packages', ROOT / '.venv'])
    query = 'import json,sys,sysconfig; print(json.dumps([list(sys.version_info[:2]),sysconfig.get_path("purelib")]))'
    version, site = json.loads(subprocess.check_output([str(python), '-c', query]))
    if tuple(version) != sys.version_info[:2]:
        raise SystemExit('已有 .venv 与平台 Python 版本不同，请保留旧环境后重新创建。')
    # Also works when the platform torch lives inside a parent virtualenv.
    (Path(site) / 'platform-rocm.pth').write_text(str(platform_site) + '\n')
    verify = ('import torch; '
              f'assert torch.version.hip and torch.__version__ == {torch.__version__!r}, '
              '"项目 .venv 中的 torch 覆盖了平台构建，请先修正环境"; '
              'print("Project torch:", torch.__version__)')
    run([python, '-c', verify])
    tools = ROOT / 'tools'
    tools.mkdir(exist_ok=True)
    constraint = tools / 'aup-torch-constraint.txt'
    constraint.write_text(f'torch=={torch.__version__}\n')
    run([python, '-m', 'pip', 'install', '--no-cache-dir', '-c', constraint,
         '-r', 'requirements.txt', '-r', 'requirements-models.txt'])
    run([python, '-c', verify])

    template = (ROOT / 'configs/aup.env.example').read_text()
    prefix = os.getenv('JUPYTERHUB_SERVICE_PREFIX', '').rstrip('/')
    root_path = prefix + '/proxy/8765' if prefix else ''
    template = template.replace('ROOT_PATH=\n', f'ROOT_PATH={root_path}\n')
    target = ROOT / ('.env.aup' if (ROOT / '.env').exists() else '.env')
    target.write_text(template)
    print(f'AUP 配置已生成：{target.name}', flush=True)
    if target.name != '.env':
        print('已有 .env 已保留；确认 AUP 配置后运行 cp .env.aup .env 启用。', flush=True)
    if not prefix:
        print('未检测到 JupyterHub 代理前缀；稍后按平台生成的访问地址设置 ROOT_PATH。', flush=True)
    print('依赖准备完成。接下来下载三个模型，然后运行 bash scripts/start.sh。', flush=True)


if __name__ == '__main__':
    main()
