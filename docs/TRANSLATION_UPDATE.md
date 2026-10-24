# 翻译与界面改版 · 2026-10-24

## 界面

已移除品牌名称、标志、英文口号、宣传标题、插图、头像及绿色主题。界面使用黑白灰，保留记录列表、原文与译文、纪要、录音控制和必要状态。空状态仅显示“暂无转写”“暂无纪要”。

新增“重新翻译”，可使用当前翻译模型更新旧记录，保留原文、时间范围和已有纪要；重译状态在重连后可恢复。

## 翻译模型

翻译由通用 Qwen3-0.6B 改为腾讯翻译专用 **Hy-MT2-1.8B**。CPU 使用官方 Q4_K_M GGUF（1,133,080,448 字节，已校验 SHA256），通过官方 llama.cpp CPU 构建 b11849 推理。模型按需启动为受应用管理的本机子进程，后续翻译复用，退出应用时关闭。

- ASR：Qwen3-ASR-0.6B-hf。
- 翻译：Hy-MT2-1.8B Q4_K_M，独立的 `TRANSLATION_*` 配置。
- 纪要：独立 Qwen，原有 `LLM_*` 配置。
- 翻译错误直接报错，不退回 0.6B 通用模型。

选择依据：官方公开的翻译专项训练、评测及 CPU 量化版本；与需要更大内存的全精度通用模型相比，量化的翻译专用模型更适合当前 7.4 GiB WSL 环境。官方评测是作者报告，不能代替本项目课程语音的独立评测。

官方来源：
- https://huggingface.co/tencent/Hy-MT2-1.8B
- https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF
- https://arxiv.org/abs/2605.22064

固定 GGUF 修订：`a0c709d9fac510f2c807aa3af52872340dc37a4a`。

## 同一组原文的系统对比

对比的是两套实际运行配置（模型、量化、推理后端和提示模板均有变化），不是严格控制变量的模型排名。

### 样本 1

原文：甚至出现交易几乎停滞的情况。

旧译文（Qwen3-0.6B）：
> Even a transaction almost stops.

新译文（Hy-MT2-1.8B Q4_K_M）：
> In some cases, transactions even come to a standstill.

### 样本 2

原文：我们决定先在本地完成前后端开发，等 AMD 平台资源恢复后，再验证 GPU 上的实时性能。

旧译文（Qwen3-0.6B）：
> We decided to complete the front-end and back-end development locally before AMD platform resources recover, and then verify real-time performance on the GPU.

新译文（Hy-MT2-1.8B Q4_K_M）：
> We decided to complete the front-end and back-end development locally first. Once the resources on the AMD platform are available again, we will verify the real-time performance on the GPU.

## 六条中英样本

以下为独立服务检查，未提供前文或术语表。第一次调用包含服务启动与加载，其余为模型已加载时的执行耗时。结果只作功能与语义检查，不是准确率或端到端实时性基准。

| 原文 | 译文 | 耗时（秒） |
|---|---|---|
| 甚至出现交易几乎停滞的情况。 | In some cases, transactions even come to a standstill. | 23.303 |
| 我们决定先在本地完成前后端开发，等 AMD 平台资源恢复后，再验证 GPU 上的实时性能。 | We decided to complete the front-end and back-end development locally first. Once the resources for the AMD platform are available again, we will then verify the real-time performance on the GPU. | 1.763 |
| 识别、翻译和纪要共享计算资源，因此识别优先级最高，纪要在后台排队执行。 | Identification, translation, and summary processing utilize computing resources; therefore, identification has the highest priority, while summaries are queued for execution in the background. | 1.386 |
| 会议暂定于下周三下午两点举行，请在周二前提交实验结果。 | The meeting is tentatively scheduled for 2 p.m. next Wednesday. Please submit the experimental results by Tuesday. | 1.044 |
| Recognition, translation, and summarization share compute resources, so recognition has the highest priority. | 识别、转换和总结都需要使用计算资源，因此识别的优先级最高。 | 0.929 |
| He didn't say the experiment had failed; he said the results were inconclusive. | 他没有说实验失败了；他说结果没有定论。 | 0.660 |

多数样本比原通用小模型更流畅，日期、否定等信息得以保留；但仍有术语问题，例如没有领域上下文时将 recognition 译为“识别”、将 translation 译为“转换”。后续应增加术语对照和真实课堂语料评价，不能据此声称整体准确率达到某一水平。

## 验证

- 22 项自动化测试通过，覆盖专用翻译适配、无静默回退、错误处理、原有流程、重译与重连状态。
- Playwright 验证了新的桌面／手机布局、录音、上传、搜索、纪要、导出及重译。
- 实际模型通过“官方中文音频 → Qwen ASR → Hy-MT2 翻译 → 纪要 → 数据持久化”联调。
- 最近一次音频联调总耗时 34.066 秒，含模型初始化；仍不能把本机 CPU 当作实时语音性能演示。

结构化结果在本机 `artifacts/translation-system-comparison.json`、`artifacts/translation-hymt2.json`、`artifacts/real-hymt-e2e.json`，这些运行产物不进 Git。
