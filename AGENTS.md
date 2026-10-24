# 项目协作约定

- 产品：课堂／组会语音转写、翻译与纪要。Python FastAPI + 原生 HTML/CSS/JS。
- 从本目录运行命令。使用 `.venv/bin/python`；不要修改其他项目的 Conda 环境。
- 默认 `demo` 模式必须明确标识为预设样本与原文摘录。真实模型失败不能悄悄切到模拟结果。
- 音频格式：16kHz 单声道 PCM16；浏览器按停顿切分，属于近实时片段识别，不是模型原生缓存流式。
- 翻译使用独立的翻译专用模型，不能退回 0.6B 通用模型；纪要与翻译配置分开。
- UI 保持黑白灰、简洁工具布局，不添加品牌名、宣传口号、装饰插图或无用的提示语。
- 所有模型调用经过 `InferenceQueue`，优先级为识别 0、翻译 1、纪要 2；不承诺抢占正在执行的推理。
- 录音、数据库、模型权重、`.env` 不进 Git；AUP 的运行数据和权重必须位于 `/home/jovyan`。
- Radeon Cloud 使用持久化 `/workspace`。SSH 经 `scripts/radeon_env.py` 补充平台代理与 ROCm 环境；保留 `/opt/venv` 的 torch。设备可见不等于可计算，部署前须完成带超时的实际 GPU 运算检查。
- 修改业务行为后运行 `.venv/bin/python -m pytest -q`。前端语法用 `node --check web/app.js`。
- UI 使用相对请求 URL，保持 JupyterHub 子路径兼容。用 textContent 或转义函数显示模型与用户内容。
- 更改模型适配后运行真实模型 smoke 并记录机器、模型、dtype、冷启动与热启动差异。不能把演示耗时当作模型实验。
- 本地服务默认只监听 127.0.0.1；AUP 通过平台认证代理访问，团队查看权限需在平台侧配置。
