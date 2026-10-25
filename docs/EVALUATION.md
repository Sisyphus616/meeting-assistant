# 课程实验：质量、文件响应时间与切分对照

本流程运行真实模型，使用独立数据库与本机回环服务，不修改日常会议数据。实验原始文件位于不入 Git 的 `artifacts/evaluation/`。本地 CPU 结果与 AUP GPU 结果必须分别报告。

## 样本与取样

先运行 `scripts/prepare_evaluation.py`，再运行任何模型推理。固定 seed 为 20261026：

| 场景 | 片段数 | 来源 | 时长筛选 |
|---|---:|---|---|
| 会议发言 | 10 | AMI `ihm/test`，真实参与者的会议录音 | 5–40秒 |
| 演讲 | 10 | TED-LIUM legacy test 的公开镜像 | 15–40秒 |
| 中文辅助验证 | 5 | FLEURS `cmn_hans_cn/validation` | 5–40秒 |
| 固定加噪对照 | 5 | 前5条 AMI 原始样本的配对副本 | 同原始样本 |

合计25个原始片段、5个派生对照，同会议或同讲者之间可能相关，不能称为30条独立样本。AMI 按测试集均匀分布的15个窗口加首窗口收集候选，固定随机顺序后每场会议最多2条；TED 收集全部测试集元数据，按固定随机顺序每位讲者最多1条；中文从首100条按固定随机顺序每个文本ID最多1条。AMI 长发言稀少，因此会议取样没有强行要求15秒以上。取样依据场景、时长和参考文本可用性，未依据模型结果筛选。

每条保存来源行号、数据集 commit、参考转写、来源时间（若有）、实际时长和归一化 WAV 的 SHA256。重复执行会核对已生成音频的哈希，不重新抽样。5条噪声样本使用可复现的高斯噪声，全文件 RMS 定义10dB信噪比；它们不是实际会议噪声。

首次抽样完成后，将行索引、来源版本和全部音频哈希固定在 `configs/evaluation-suite.json`，不含录音或参考文本。其他机器默认按该计划恢复同一批数据并核对哈希；若上游更新版本则停止，需转移原有已验证样本包，不会悄悄换一批样本。`--resample` 仅用于明确创建新实验清单，不用于CPU/GPU对照。

AMI 为近讲单人麦克风，不能代表远场多人混合发言；部分会议为设计任务角色扮演。TED 是演讲，不全是学术报告。FLEURS 中文为朗读，仅用于中文识别及跳过翻译的辅助验证。参考文本来自公开标注，尚未逐条重新人工审听；没有人工中文参考译文，模型训练集是否包含这些录音未知。本流程不是公开排行榜评测。

## 指标口径

- 英语 WER、中文 CER 分开计算：所有样本的替换、删除、插入数之和除以参考词数/字数之和；不平均每条错误率，不截断100%以上的结果。
- Unicode NFKC、英文大小写与标点归一化；合并 TED/AMI 的缩约词分词（`i 'm`、`do n't`）；剔除括号标注。保留口语填充词，不把数字自动改写为英文单词。中文按去空白/标点后的字母数字字符计数。
- 英语评分保留ASR误识别出的非拉丁内容作为错误token，不能过滤后再算WER。初版采集器使用拉丁词正则，最终报告统一从原始参考与识别文本重算，不修改采集日志。
- 质量主结果取每条第1次热态试验。其他热态重复用于延迟与稳定性检查，不算额外独立质量样本。模型异常与空输出保留，不能只统计识别成功的文件。
- 首条确认字幕延迟：客户端开始 HTTP 上传至 WebSocket 收到首条非空、已入库转写。上传 ACK、临时预览均不算字幕结果。
- 首译延迟：同一起点至首条非空模型译文。中文本来就是目标语言，因此不要求额外译文。
- 完成延迟：文件处理与自动纪要完成后请求结束会议，收到 `finished`。包含最后不足一组的纪要。文件任务完成由仅存在于隔离评测服务的端点每100ms观测，生产应用没有增加评测路由。
- 完整管线 RTF = 完成延迟/原始音频时长；它包括翻译和纪要，不能当作 ASR 模型 RTF 或实时麦克风延迟。
- 每条热态重复3次，固定样本顺序在重复之间轮转。报告 P50、P95、范围和失败数。少量重复的P95只是描述统计，不作为稳定的尾延迟保证。
- 全新进程的第一次真实文件请求单独标为 model-cold，再显式准备模型。未清空操作系统文件缓存，所以不能称为磁盘完全冷启动。
- 当前方案与基线的冷启动样本不同，各只测1次，不用于方案间延迟对比。
- 全部模型调用沿用应用的 `InferenceQueue`，没有绕过队列直接推理。阶段推理/排队耗时来自服务 health；若单次任务超过最近20条日志，报告会标记截断。

## 切分对照

固定比较 TED 清单前5条，不按最终效果选例子。使用独立服务进程，唯一切分差异为当前停顿切分与旧的固定10秒切分；模型、数据、语言策略、纪要设置相同。每种都进行3次热态试验，比较WER、片段数和首字幕/完成延迟。该对照运行在当前识别适配器上，不是回滚整个历史版本。先完整测当前方案，再测固定10秒方案，顺序效应及缓存可能影响时延，不能把微小差异直接归因于切分。

## 三个独立模型评审

由 `scripts/report_evaluation.py --prepare-judging` 导出第一次热态的25条英语语音→中文输出。中文辅助样本不参加翻译评分。每个评委获得相同标准和参考内容，各自顺序打乱且不看到场景标签、被测模型名、其他评委分数。识别结果只作定位依据，参考转写才是语义评分基准，因此能计入 ASR 误差的传导影响。

三个子代理采用不同模型别名和推理强度。当前子代理接口不提供温度、采样seed或精确服务端模型快照，记录为未公开；不能伪称使用了可指定的temperature，也不能据此推断某个参数的独立因果效果。模型评审可能有共同偏差，不等于三位独立人类。

固定每项0–5分，以0.5为步长：忠实性60%、术语及关键概念20%、中文流畅性20%。先计算每个评委对每条的加权分，再等权平均评委；场景内按样本等权平均。主汇总保留会议、演讲、加噪三组，另外可列20条原始英语录音的均值，避免派生噪声样本重复加权影响主结论。与WER/CER不混成一个总分。缺少输出按0分，不剔除。

每条必须给具体错误证据与置信度，评委加权分最大差达到1分的条目列为分歧案例。固定抽5条供团队人工复核，文件标记 `human-audit-pending.json`，在真人检查前不得宣称已完成人工验证。完整rubric及哈希、盲化输入、映射和原始评分均保存。

## 本地执行

```bash
.venv/bin/python scripts/prepare_evaluation.py
.venv/bin/python scripts/run_evaluation.py --cpu --repeats 3 --compare \
  --output artifacts/evaluation/local-cpu-run
.venv/bin/python scripts/report_evaluation.py artifacts/evaluation/local-cpu-run --prepare-judging
```

评委读取各自 `judges/input-A.json`、`input-B.json`、`input-C.json` 和共同 `rubric.json`，分别将符合schema的完整评分写到 `ratings-A.json` 等文件，携带rubric的SHA256。聚合时拒绝缺失、重复、越界评分或不同rubric。

```bash
.venv/bin/python scripts/report_evaluation.py artifacts/evaluation/local-cpu-run
```

输出 `REPORT.md`、`summary.json`、逐次记录 `trials.jsonl` 与 `trials.csv`。录音、数据库和模型不提交到Git，课程展示可使用脱敏汇总表。

可选导出用于汇报的 PNG/SVG 图表（在完整实验和评分完成后运行）：

```bash
.venv/bin/python -m pip install -r requirements-evaluation.txt
.venv/bin/python scripts/plot_evaluation.py artifacts/evaluation/local-cpu-run
```

## AUP 执行

先结束录音、等任务完成，在运行日常应用的终端按Ctrl+C，避免生产服务与实验共享GPU。无需停止Notebook。项目、实验数据和权重保持在 `/home/jovyan`。

```bash
cd /home/jovyan/projects/meeting-assistant
git pull --ff-only origin main
```

优先将本地已验证的 `artifacts/evaluation/evaluation-suite.zip` 上传到AUP项目根目录，再解压并校验。该包约9MB，含与本地完全相同的30个案例，不含模型或数据库：

```bash
mkdir -p artifacts/evaluation/suite
.venv/bin/python -m zipfile -e evaluation-suite.zip artifacts/evaluation/suite
.venv/bin/python scripts/prepare_evaluation.py
```

如果没有样本包，也可直接运行 `prepare_evaluation.py`，按固定计划在线下载。已有完整样本清单时，脚本只验证本地哈希，无需访问外网。

沿用当前AUP配置（`DEVICE=cuda`、模型路径在 `/home/jovyan`），执行：

```bash
.venv/bin/python scripts/run_evaluation.py --repeats 3 --compare \
  --output artifacts/evaluation/aup-gpu-run
.venv/bin/python scripts/report_evaluation.py artifacts/evaluation/aup-gpu-run --prepare-judging
.venv/bin/python scripts/report_evaluation.py artifacts/evaluation/aup-gpu-run
```

省略 `--cpu`，沿用AUP的 `.env` 和平台ROCm torch。若配置仍为CPU则拒绝运行，避免将CPU测量误标为GPU数据。启动隔离服务前自动在独立进程进行45秒超时的GPU实际运算检查。实验仅监听127.0.0.1，计时客户端也在AUP服务器上，因此报告应写“服务器本机HTTP/WebSocket文件响应时间”，不包含浏览器到AUP的上传网络和认证代理耗时。评测结束会关闭隔离服务；需要时再运行 `bash scripts/start.sh` 恢复日常应用。输出目录已存在时脚本拒绝覆盖，应使用新的目录名。

导出待评分输入后，AUP新生成的译文需要重新独立评审，不能复用本地CPU的评分。可将结果打包后交回分析：

```bash
cd artifacts/evaluation/aup-gpu-run
python3 -m zipfile -c ../aup-results.zip \
  environment.json trials.jsonl trials.csv summary.json REPORT.md judges
```

从Notebook文件列表下载 `artifacts/evaluation/aup-results.zip`。包含转写、译文、指标和待评分输入，不包含模型、数据库或音频。打包后返回项目根目录可执行 `bash scripts/start.sh` 恢复服务。

若需要本地比较AUP结果，下载输出目录中的 `environment.json`、`trials.jsonl`、`summary.json` 和 `judges` 即可，不需要把会议数据库或模型下载回来。AUP模型与本地模型的dtype、翻译量化及纪要模型规模不同，因此本地CPU/AUP GPU对比是系统配置对比，不能直接声称同模型纯硬件加速比。

## 来源

- AMI 官方：[数据集介绍及许可](https://groups.inf.ed.ac.uk/ami/corpus/)，[HF预处理版本](https://huggingface.co/datasets/edinburghcstr/ami)。
- TED-LIUM：[官方发布](https://www.openslr.org/19/)，[TensorFlow数据说明](https://www.tensorflow.org/datasets/catalog/tedlium)，[测试集镜像](https://huggingface.co/datasets/distil-whisper/tedlium-dev-test)。录音与参考文本供本地课程实验，保留原许可，不随代码重新分发音频。
- 中文：[Google FLEURS](https://huggingface.co/datasets/google/fleurs)。
- 指标：[Hugging Face ASR评测](https://huggingface.co/learn/audio-course/chapter5/evaluation)。
- 模型评审方法参考：[G-Eval](https://aclanthology.org/2023.emnlp-main.153/)。本项目只采用固定多维评分和独立评审，不声称复现其完整方法。
