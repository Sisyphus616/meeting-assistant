# AMD-AUP 部署

平台入口：https://tpe.aupcloud.io/hub/home

## 1. 文件与环境

通过 Code Server GPU Environment 或可用的 CPU 开发环境打开终端。项目放在 `/home/jovyan/projects/meeting-assistant`。不要依赖 `/ryzers/notebooks` 的内容保留。

如果有团队 Git 仓库，在该目录克隆项目；没有 SSH 也可以通过浏览器上传源代码压缩包。不要上传本地 `.venv`，Linux 环境与 PyTorch 构建应在平台重新准备。

先检查：

```bash
findmnt -T /home/jovyan
df -h /home/jovyan
python -c 'import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no GPU")'
```

先创建一个测试文件，再通过平台停止并重启环境，确认 `/home/jovyan` 文件保留。目录与配额以平台实例配置为准。

## 2. 保留 AMD 环境中的 PyTorch

如果平台已经安装可用的 ROCm PyTorch，可以用 **相同 Python** 创建继承系统包的虚拟环境：

```bash
cd /home/jovyan/projects/meeting-assistant
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-models.txt
```

随后再次运行 GPU 检查。不要运行 `--cpu-models`，它是本地 CPU 环境的安装选项。若平台没有可用的 ROCm torch，请先按该镜像与 gfx1151 的官方适配要求准备；不能假定安装标准 PyPI torch 就能加速 8060S。

## 3. 平台配置

将以下内容保存为项目根目录 `.env`，其中用户名和地址需要替换为实际值：

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

界面服务和数据库可以在 CPU 上运行；真实模型调用依赖当前选定的设备。平台运行时长到期会停止进程，数据保留不等于服务持续运行。`tmux` 仅防终端断开，不能让进程跨实例停止继续工作。

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
