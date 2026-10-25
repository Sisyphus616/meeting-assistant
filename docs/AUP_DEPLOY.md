# AMD-AUP 部署

平台入口：https://tpe.aupcloud.io/hub/home

## 1. 文件与环境

通过 Code Server GPU Environment 或可用的 CPU 开发环境打开终端。项目放在 `/home/jovyan/projects/meeting-assistant`。不要依赖 `/ryzers/notebooks` 的内容保留。

如果有团队 Git 仓库，在该目录克隆项目；没有 SSH 也可以通过浏览器上传源代码压缩包。不要上传本地 `.venv`，Linux 环境与 PyTorch 构建应在平台重新准备。

先检查：

```bash
findmnt -T /home/jovyan
df -h /home/jovyan
python3 -c 'import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no GPU")'
```

先创建一个测试文件，再通过平台停止并重启环境，确认 `/home/jovyan` 文件保留。目录与配额以平台实例配置为准。

## 2. 保留 AMD 环境中的 PyTorch

使用能通过 GPU 实际运算检查的 Python 运行安装器。当前已验证的 AUP 实例使用 `/usr/bin/python3`，没有 `python` 命令：

```bash
cd /home/jovyan/projects/meeting-assistant
python3 scripts/setup_aup.py
```

安装器先以 45 秒超时验证实际 GPU 运算，在项目 `.venv` 引用平台的 ROCm torch，并约束安装期间的 torch 版本，额外依赖只安装到项目环境。平台 Python 若本身处于虚拟环境，单独使用 `--system-site-packages` 不足以继承父环境；安装器会显式添加 torch 所在包目录。

平台缺少 `ensurepip` / `python3.12-venv` 时，安装器使用 `venv --without-pip` 创建环境；pip 不可用时通过 PyPA 官方 `get-pip.py` 仅为项目补齐 pip，无需 sudo 或改动系统包。旧版脚本失败留下的不完整 `.venv` 可原地补齐，不需要删除。更新代码后直接重新执行安装器即可。

该恢复流程已在本地 `/usr/bin/python3.12`（ensurepip、pip 均缺失）复现验证：旧命令失败并留下不完整环境，新流程成功补齐、安装 pip 和项目内依赖，保留已有文件，重复执行正常。此验证不代表 AUP 上完整模型安装已完成。

随后安装器生成 `.env`，自动从 `JUPYTERHUB_SERVICE_PREFIX` 设置代理前缀。已有 `.env` 会保留，新的配置写入 `.env.aup`；查看后可用 `cp .env.aup .env` 启用。不要运行 `--cpu-models` 或 `setup-radeon.sh`。

下载配置中对应的三个模型：

```bash
HF_HOME=/home/jovyan/model-cache HF_HUB_DISABLE_XET=1 \
  .venv/bin/python scripts/download_models.py \
  --directory /home/jovyan/models/meeting-assistant \
  --llm Qwen/Qwen3-4B-Instruct-2507 \
  --translation tencent/Hy-MT2-1.8B
```

重复执行会复用已下载文件。三份权重合计约 13.7 GB，另需为依赖、缓存和录音留空间；`df` 显示共享存储容量不代表个人配额。下载失败时保留报错，先确认模型站连通性，不要安装其他 torch。

2026-10-24 用户在 AUP 实测：`/usr/bin/python3`、torch `2.9.1+rocm7.13.0`、ROCm `7.13.99004-3309c6114a`、Radeon 8060S Graphics，GPU 张量计算与结果回传通过。此记录仅代表基础 GPU 检查通过，模型推理及完整安装仍需在该实例验证。

## 3. 平台配置

推荐使用安装器生成的配置，模板见 `configs/aup.env.example`（本地模型目录、离线加载）。如果希望直接从 Hugging Face 加载模型，可手工使用下面的配置，其中用户名和地址需要替换为实际值：

```dotenv
PROVIDER=qwen
DEVICE=cuda
MODEL_DTYPE=float16
ASR_MODEL=Qwen/Qwen3-ASR-0.6B-hf
LLM_MODEL=Qwen/Qwen3-4B-Instruct-2507
TRANSLATION_BACKEND=transformers
TRANSLATION_MODEL=tencent/Hy-MT2-1.8B
DATA_DIR=/home/jovyan/meeting-data
HF_HOME=/home/jovyan/model-cache
TORCH_THREADS=6
SUMMARY_EVERY=4
SUMMARY_STYLE=abstractive
HOST=0.0.0.0
PORT=8765
ROOT_PATH=/user/<URL-encoded-username>/proxy/8765
```

ROCm PyTorch 沿用 `cuda` 设备名。先验证 FP16；其他数据类型和优化由实测决定。模型权重需从平台可访问的 Hugging Face 入口获取。离线环境可以预下载到本地目录，配置 `ASR_MODEL` / `LLM_MODEL` 为目录，并设置 `LOCAL_FILES_ONLY=true`。

翻译现在使用独立的 Hy-MT2 专用模型，`LLM_MODEL` 只负责纪要。上述 GPU 配置不依赖本地 CPU 的 llama.cpp 二进制；请先核对三份模型并存的可用显存／共享内存。更大翻译模型可配置 `tencent/Hy-MT2-7B`，应在资源允许时实测。若已有 vLLM 服务，使用 `TRANSLATION_BACKEND=compatible` 与独立 `TRANSLATION_BASE_URL`。

GPU 不可用时，可先使用 `PROVIDER=demo` 启动界面及联调。

## 4. 启动与访问

```bash
bash scripts/start.sh
```

在 Code Server 的 PORTS 面板添加 8765，并打开平台生成的地址，通常形如：

```text
https://tpe.aupcloud.io/user/<URL-encoded-username>/proxy/8765/
```

保留地址末尾 `/`。前端使用相对 URL 请求 API、静态文件和 WebSocket。`ROOT_PATH` 填平台实际代理前缀，服务本身仍然只注册 `/api/...` 路由。如果代理已经自动设置 ASGI root path，请避免重复配置。

`scripts/run.py` 同时向 Uvicorn 传递 `ROOT_PATH`，让去掉代理前缀的请求正确访问挂载的静态文件；只设置 FastAPI 的 root path 会导致某些版本的 Starlette 静态文件路由返回 404。请使用最新启动脚本。

### JupyterLab 返回 404，但本机健康检查正常

`/proxy/8765/` 需要 Notebook 服务启用 `jupyter_server_proxy`。先运行 `python3 -m jupyter server extension list` 查看；AUP 某些 Notebook 镜像没有此扩展。仅把包安装进项目 `.venv` 不会让平台的 Notebook 服务发现它。

使用安装脚本，将扩展及缺少的依赖安装到持久化的用户包目录，并生成用户级启用配置：

```bash
cd /home/jovyan/projects/meeting-assistant
git pull --ff-only
python3 scripts/setup_aup_proxy.py
python3 -m jupyter server extension list
```

预期出现 `jupyter_server_proxy enabled` 和 `OK`。这是用户目录安装，不需要 sudo，不更改系统 torch。脚本使用项目 pip 的 `--prefix` 安装到 `/home/jovyan/.local`，通过平台 Python 验证模块，再写入独立的 `meeting-assistant-proxy.json` 启用配置，不覆盖其他扩展。必须保证平台 Python 能导入该模块；若验证失败，保留输出排查 Python 环境和用户包路径，不要继续重启。

启用配置会在 **Notebook 服务器下次启动** 时加载。保存工作后到 Hub 控制页停止并重新启动服务器，继续选择相同账号和环境；仅重启 Notebook 内核或刷新页面不会加载新服务端路由。此操作会中断当前应用进程，重新连接后再次执行：

```bash
cd /home/jovyan/projects/meeting-assistant
bash scripts/start.sh
```

然后打开当前 Notebook 服务前缀下的 `/proxy/8765/`。安装包在 `/home/jovyan/.local`、扩展配置在 `/home/jovyan/.jupyter`，与项目和模型一起保留；若新镜像更换了 Python 小版本，需要按新的环境重新安装扩展。重新申请 GPU 可能需要等待平台调度。

本地隔离环境已复现“后端健康、Notebook 代理 404”，并验证用户目录安装和启用配置。以真实 Jupyter Server + Server Proxy 启动服务后，验证了认证代理下的页面、JS / CSS / AudioWorklet、API、WebSocket、演示记录与三种导出。此检查不包含 AUP 实例中的实际路由加载和模型 GPU 推理；用户须在平台重启后验收。

界面服务和数据库可以在 CPU 上运行；真实模型调用依赖当前选定的设备。平台运行时长到期会停止进程，数据保留不等于服务持续运行。`tmux` 仅防终端断开，不能让进程跨实例停止继续工作。

## 更新已部署实例

本次滚动转写、会议默认值和文件停顿切分更新没有增加依赖或更换模型。先结束录音并等待任务完成，在运行 `start.sh` 的终端按 `Ctrl+C` 停止应用。无需停止 Notebook 服务器，也无需重新申请 GPU。

先按现有 `.env` 中的路径备份数据库（录音仍保留在原目录）：

```bash
cd /home/jovyan/projects/meeting-assistant
.venv/bin/python - <<'PY'
from datetime import datetime
import sqlite3
from app.config import Settings

directory = Settings.from_env().data_dir
source = directory / 'meetings.sqlite3'
if source.exists():
    target = directory / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite3')
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    print('数据库已备份：', target)
else:
    print('当前配置下尚无数据库：', source)
PY
git pull --ff-only origin main
```

若 Git 提示本地修改冲突，保留输出并处理差异，不要使用 `reset --hard`。本次无需重新运行安装器；现有 `.env`、模型和会议数据继续使用。首次启动会自动添加 `translation_skipped` 字段，已有记录保留；新默认值只作用于新会议。

启动前在项目环境完成带超时的 GPU 实际运算检查：

```bash
timeout 45 .venv/bin/python -u - <<'PY'
import torch
print('PyTorch:', torch.__version__, 'ROCm:', torch.version.hip)
assert torch.version.hip and torch.cuda.is_available(), '项目环境无法使用 ROCm GPU'
x = torch.ones(2, device='cuda') * 3
torch.cuda.synchronize()
assert x.cpu().tolist() == [3.0, 3.0]
print('GPU 计算通过')
PY
```

检查通过后运行 `bash scripts/start.sh`，另开终端验证：

```bash
curl --noproxy '*' http://127.0.0.1:8765/api/health
```

`inference_mode` 应为 `rolling-preview-with-final-sentences`，`provider` 为 `qwen`，`device` 为 `cuda`。浏览器原代理地址保持不变，使用 `Ctrl+Shift+R` 强制刷新前端。首次录音会准备模型；健康检查的 `models_loaded` 在模型准备前为 `false` 是正常的。实际 GPU 模型延迟仍需在 AUP 上测量，本地 CPU 测试不能替代。

## 5. 团队协作

- 代码通过团队 Git 仓库与分支协作；演示使用一个固定分支／目录。
- 若三人需要直接打开同一份运行文件，需平台提供共享目录和权限，不能只把个人 `/home/jovyan` 地址发给队员。
- 应用地址的访问权限由 JupyterHub／管理员控制；个人代理链接不一定向其他账号开放。不要分享账号或 Codex 凭据。
- 远程 Codex 可通过平台终端运行；当前本地 Codex 若要直接操作 AUP，需要平台提供的 SSH 主机、端口与授权。

## 6. 验收

1. 重启后数据库、录音、权重仍存在。
2. 健康检查显示 `qwen`；真实测试调用能加载模型。
3. 麦克风取得权限，识别出录音的实际内容。
4. 翻译、纪要、原文回溯及导出均可用。
5. 连续录音 5–10 分钟，观察排队和暂停保护。
6. 用固定音频评测字幕延迟、CER/WER、翻译和纪要质量；演示样本不能用于模型性能结论。

官方资料：
- https://amdresearch.github.io/aup-learning-cloud/user-guide/platform-basics.html
- https://amdresearch.github.io/aup-learning-cloud/user-guide/code-server-guide.html
- https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf
- https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507
