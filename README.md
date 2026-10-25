# 语音转写、翻译与纪要

面向智能计算系统课程的全栈项目：麦克风采集 → 语音活动检测 → Qwen ASR → Hy-MT2 专用翻译 → 分段纪要。支持本地 CPU 开发及 AMD-AUP 部署。

## 立即运行

推荐 Python 3.11–3.13。安装依赖后，启动命令为：

```bash
cd meeting-assistant
bash scripts/start.sh
```

浏览器打开 **http://localhost:8765**。Windows 可双击 `start-local.cmd`（依赖已配置的 WSL）。关闭服务按 `Ctrl+C`。

若 localhost 受到浏览器代理或 IPv6 解析影响，使用 **http://127.0.0.1:8765**；本机已从 Windows 侧验证该地址可访问。

当前 WSL 安装将 `.venv` 链接到 Linux 文件系统下的独立环境，减少 Windows 跨盘安装大量模型依赖的开销。项目源代码、数据和权重仍保存在项目目录。迁移 AUP 时重新建环境，不复制这个链接。

新电脑首次安装（演示模式）：

```bash
git clone https://github.com/Sisyphus616/meeting-assistant.git
cd meeting-assistant
PYTHON=python3.11 bash scripts/setup.sh
bash scripts/start.sh
```

默认运行演示模式，不需要 GPU、模型下载或 API key。点击“播放演示”，8 条预设双语句子会依次进入，按配置自动整理。演示纪要是原文摘录，界面明确标注。自由文字输入在演示模式不产生虚构译文。

## 已实现

- 新建课堂、设置中英语言方向及课程术语；SQLite 保存历史记录。
- 新会议默认名称为“新会议”，术语留空，方向为英语到中文。中文为主的输入（可含英文专业术语）保留原文、跳过翻译，不显示翻译占位行；英文正常译为中文。该规则同时用于文字、录音、导入和重译。
- WebSocket 实时更新原文、译文、任务状态；断线重连后读取已持久化内容。
- Qwen 模式：录制中持续显示可修正的原文预览；停顿后确认，并按中英文句末标点拆成独立句子翻译。
- 默认英语到中文时，ASR 自动识别实际语言，以保留会议中偶尔出现的中文。原文仍可用于生成纪要。
- 首次录音前准备模型，避免冷启动落在采集期间。预览约从 0.8 秒开始、每 1.2 秒更新请求；能量 VAD 保留前后音频，约 0.8 秒停顿或最多 12 秒确认一片段。
- 按优先级执行识别、翻译、纪要，共享单个推理工作线程。推理不阻塞 HTTP / WebSocket 事件循环。
- 每 4 句自动生成纪要；手动整理；结束会议时先等待已接收任务，再整理剩余原文。
- 纪要关联到输入原文片段，支持点击回查；有录音的片段支持回放。
- 导入最长 5 分钟、20MB 以内的 PCM16 WAV，先检测停顿，再顺序识别；不再每 10 秒硬切。连续无明显停顿的长语音才使用 30 秒兜底，并优先寻找短停顿或低音量位置。
- 双语视图／原文视图、搜索、自动滚动；Markdown、JSON、SRT 导出。
- 当前运行模式、模型名称、任务积压、最近翻译推理耗时可查看。

## 启用本地模型（CPU）

```bash
bash scripts/setup.sh --cpu-models
cp .env.example .env  # 只在尚未创建 .env 时执行，避免覆盖个人配置
```

依赖和权重准备好后，可以直接运行 `bash scripts/start-cpu.sh`（Windows 双击 `start-qwen.cmd`）使用本地 CPU 真实模型；`bash scripts/start-demo.sh` 运行演示模式。两种模式共用相同数据目录。启动脚本优先使用项目 `models/` 下已下载的两个检查点。

修改 `.env`：

```dotenv
PROVIDER=qwen
DEVICE=cpu
ASR_MODEL=Qwen/Qwen3-ASR-0.6B-hf
LLM_MODEL=Qwen/Qwen3-0.6B
MODEL_DTYPE=bfloat16
TORCH_THREADS=6
SUMMARY_STYLE=extractive
```

重新启动服务。模型按需加载并缓存到 `HF_HOME`。第一次请求包括权重下载和加载，可能需要较长时间；查看启动终端的下载／错误信息。`/api/health` 的 `models_loaded` 可以区分服务启动和模型加载。

本机 Ryzen 5 7640HS 支持相关 CPU 指令；若其他 CPU 的 bfloat16 算子不兼容，改为 `MODEL_DTYPE=float32`，同时注意内存增加。0.6B Qwen 仅负责抽取式纪要，翻译已独立为 Hy-MT2-1.8B 专用模型。本地采用官方 Q4_K_M 量化文件，通过 CPU llama.cpp 推理。ASR 接口依据官方原生 Transformers Qwen ASR 示例，要求 `transformers>=5.13`。

本地小模型默认采用抽取式纪要：Qwen 选择关键句编号，程序验证范围后从原文呈现对应句子，避免小模型凭空补充任务或结论。若编号不合法，界面明确标注后改为前几句原文摘录；模型加载／推理失败仍直接报错。`SUMMARY_STYLE=abstractive` 切换为概括式生成，建议在 AUP 上配合更大的模型并人工核查。来源链接代表输入段落，不能当作概括结果正确性的证明。

模型测试：

```bash
.venv/bin/python scripts/check_environment.py
MODEL_DTYPE=bfloat16 .venv/bin/python scripts/model_smoke.py
MODEL_DTYPE=bfloat16 .venv/bin/python scripts/model_smoke.py --audio /path/to/sample.wav
```

真实模式从不自动退回演示。模型缺失、内存不足或推理异常会在界面显示；翻译错误会保存在对应片段，原文仍保留。

### 翻译模型安装与配置

```bash
# --cpu-models 会一并安装；已有 Python 模型依赖时只运行下面这条即可。
.venv/bin/python scripts/setup_translation.py --verify
```

安装器使用官方 llama.cpp CPU 构建 b11849，以及腾讯官方 Hy-MT2-1.8B-GGUF 的 Q4_K_M 文件（约 1.13 GB）。`--verify` 校验模型 SHA256。首次翻译会启动本机 8081 端口的子进程，后续请求复用模型；应用退出时一并关闭。

```dotenv
TRANSLATION_BACKEND=llama_cpp
TRANSLATION_MODEL=tencent/Hy-MT2-1.8B
TRANSLATION_GGUF=./models/Hy-MT2-1.8B-GGUF/Hy-MT2-1.8B-Q4_K_M.gguf
TRANSLATION_BINARY=./tools/llama.cpp/llama-server
TRANSLATION_PORT=8081
```

如果使用独立推理服务，将翻译后端改为 `compatible` 并设置 `TRANSLATION_BASE_URL`。AUP 可以使用 `transformers` 后端直接加载翻译模型。翻译配置与 `LLM_MODEL`、`LLM_BACKEND` 等纪要配置彼此独立，翻译失败不会退回通用小模型。

固定中英样本检查：`.venv/bin/python scripts/benchmark_translation.py`。旧记录可点击“重新翻译”更新译文，原文和纪要保留。

### 使用单独的纪要推理服务

如果已经在本地 llama.cpp 或 AUP 的 vLLM 中运行 Qwen，可以让纪要使用该服务；翻译仍使用独立的专用模型，ASR 由本应用执行：

```dotenv
PROVIDER=qwen
LLM_BACKEND=compatible
LLM_BASE_URL=http://127.0.0.1:8080/v1
LLM_MODEL=your-served-qwen-model-name
LLM_API_KEY=
```

这可用于后续验证量化模型。服务端必须提供兼容的 `/chat/completions` 接口。

## 数据与协作

```text
app/                   后端、存储、音频处理、模型适配、调度
web/                   浏览器应用，无前端构建步骤
scripts/               安装、启动、环境检查及模型 smoke
tests/                 集成测试与优先级调度测试
docs/                  AUP 部署、架构、验收与实验记录
data/meetings.sqlite3   会议、转写、译文、纪要（不进 Git）
data/audio/<会议ID>/    已接收的录音片段（不进 Git）
models/                Hugging Face 缓存（不进 Git）
```

团队仓库：[Sisyphus616/meeting-assistant](https://github.com/Sisyphus616/meeting-assistant)。队员使用各自分支开发。运行数据通过平台授权的共享存储或导出文件共享。个人 AUP home 不会自动向队员开放。模型权重、录音、数据库、虚拟环境和 `.env` 不包含在仓库中；克隆后按本文安装依赖并下载模型。

数据库使用 WAL。备份时可先停止服务再复制整个 `DATA_DIR`；运行中备份数据库应使用 SQLite backup API，而不是仅复制主 `.sqlite3` 文件。

## 测试

```bash
bash scripts/setup.sh --dev
.venv/bin/python -m pytest -q
node --check web/app.js
node --check web/pcm-worklet.js
node --check web/speech-chunker.js
node tests/speech-chunker.cjs
```

浏览器验证脚本 `scripts/ui-smoke.cjs` 使用 Playwright，需自行在测试目录安装 Playwright / Chromium；生产启动不依赖 Node.js。脚本在测试临时目录启动服务，不写入正式 `data/`。

## 实现范围与限制

- **语音采用累计音频的滚动识别**，尚未实现 Qwen vLLM 原生流式缓存解码。录制中显示临时字幕，最终结果确认后才入库、翻译和生成纪要；更新速度仍取决于模型推理和排队耗时。
- 能量 VAD 是第一版基线，安静或嘈杂环境可能误切；长句按最大时长切分，不保证语义边界，也没有跨片段重叠纠错。后续可替换 Silero VAD。
- 文件导入按约 350ms 停顿选择片段，并合并邻近的短尾音，避免短杂音单独送入识别；整份标准化音频与实际切分位置一起保存。自然停顿不必然等于一句话，文本条目仍按识别出的句末标点拆分。
- 单线程优先级是非抢占式调度；正在生成纪要时到来的音频，仍需等当前生成结束。模型装载也占用这条队列。
- 页面显示的翻译耗时是调用执行耗时（冷调用包括模型加载），不等于用户说完到字幕出现的端到端延迟。排队耗时另记在服务指标中；指标为最近 100 个任务的内存数据，重启清空。
- 同一音频内拆出的句子共享来源录音与片段起止时间，回放播放来源片段。SRT 按来源片段合并为一条字幕，不伪造逐句／逐字对齐；文字输入没有真实语音时长，演示样本使用预设时间。
- 点击“暂停／结束”会发送尚未发出的有效语音片段；达到积压阈值时自动暂停麦克风。关闭或刷新浏览器会丢失尚未发送的浏览器缓冲，已接收的片段仍保存在服务器。
- WebSocket 断线后不会重放未确认消息，避免重复入库；用户需检查末尾片段。页面会停止录音并提示连接状态。
- 页面已实现响应式布局。麦克风需要 localhost 或 HTTPS。实际麦克风权限和声学表现仍需用户在电脑浏览器中验证。
- 当前为单团队课程原型，没有账号／多租户隔离。保持本机回环监听；AUP 通过平台认证的代理访问。

部署及课程实验见 [AUP 部署](docs/AUP_DEPLOY.md)、[Radeon Cloud 部署](docs/RADEON_CLOUD.md) 和 [架构与实验](docs/ARCHITECTURE.md)。

本机已完成真实模型端到端联调。初版记录见 [本地验收记录](docs/VALIDATION.md)，最新翻译模型与界面变更见 [翻译与界面改版](docs/TRANSLATION_UPDATE.md)。

本次录制中转写和自动断句的设计与测试见 [实时转写更新](docs/LIVE_TRANSCRIPTION.md)。

课程质量与性能实验、公开会议/演讲样本、三模型独立评分及 AUP 复测命令见 [实验方法](docs/EVALUATION.md)。

已完成的本地 CPU 实测、质量与延迟图表、切分对照见 [实验结果](docs/EVALUATION_RESULTS.md)。
