# Radeon Cloud 部署

入口：<https://developer.amd.com.cn/radeon/>。与 AMD-AUP 分别配置存储和访问地址。

## 创建实例

先在设置中添加本机 SSH 公钥，再启动 Notebook。镜像选择 AMD OneClick Base，先用一张 GPU；打开自定义，选择**持久化 /workspace**。后续实例继续选择同一种持久化容量（25 GB 与 100 GB 是不同的存储）。

只有 `/workspace` 内的文件按此选项持久化，`/root` 和 `/opt/venv` 内自行安装的内容不会随新实例保留。请勿复制本机 `.venv`，也不要重新安装通用 CUDA / CPU PyTorch 覆盖平台的 ROCm 构建。

## 项目环境

在远程实例运行：

```bash
cd /workspace
git clone https://github.com/Sisyphus616/meeting-assistant.git
cd meeting-assistant
bash scripts/setup-radeon.sh
```

安装器在项目 `.venv` 内安装额外依赖，通过 `.pth` 引用镜像 `/opt/venv` 里的 ROCm torch 及基础包，不改动镜像环境。两边 Python 小版本必须一致。新实例换了镜像后需重新核对；持久化虚拟环境不能保证跨镜像兼容。

SSH 的默认 Python 可能是 `/usr/bin/python`，它没有 torch。`scripts/radeon_env.py` 从容器主进程读取固定白名单内的 ROCm、代理和模型站设置，仅补充当前环境未设置的值，不打印代理值、不复制认证令牌、不写入配置文件。这解决了 Notebook 可联网而 SSH 进程不能下载的问题。

由于继承镜像基础包，项目环境中的 `pip check` 可能报告镜像 vLLM 等未使用工具与 Transformers 5 的冲突；应用不导入这些工具，镜像 `/opt/venv` 不受影响。如需使用 vLLM，另设环境和推理服务。

## 必须验证实际 GPU 运算

```bash
rocm-smi
python3 scripts/radeon_env.py timeout 45 .venv/bin/python -u -c '
import torch
print(torch.__version__, torch.version.hip, flush=True)
assert torch.cuda.is_available()
print(torch.cuda.get_device_properties(0), flush=True)
x = torch.ones(2, device="cuda") * 3
torch.cuda.synchronize()
assert x.cpu().tolist() == [3.0, 3.0]
print("GPU calculation passed", flush=True)
'
```

`torch.cuda.is_available() == True` 只说明设备可见，不能作为计算已正常工作的结论。如果进程超时（退出码 124），先保留输出，检查平台 GPU 状态。不要直接启动模型让请求无限等待；也不要把改用 CPU 的结果当成 GPU 验收。

2026-10-24 首个实例实测：Python 3.12.3、平台 torch 2.9.1、ROCm 7.2、gfx1100、显存 49,136 MiB。torch 可以识别设备并分配张量，但第一次填充计算超时；独立 hipcc 编译的单线程 HIP 内核也在启动时卡住。禁用 SDMA、去掉额外 HIP 调优变量均未解决。因此该实例**尚未通过 GPU 验收**，故障原因需平台进一步确认。项目的 22 项后端测试在该实例通过，不能据此声称模型 GPU 推理通过。

为区分模型兼容性与 GPU 故障，另以 CPU / FP32 测试真实权重：4.203 秒中文音频识别为“甚至出现交易几乎停滞的情况。”，含加载首次调用 41.383 秒；Hy-MT2 Transformers 后端翻译为“In some cases, transactions even come to a halt almost completely.”，含加载首次调用 33.419 秒。此为单样本兼容性检查，不是 GPU 性能或翻译质量评测。演示模式的 WebSocket、四片段纪要、数据库与 MD / JSON / SRT 导出也通过独立验证。

## 下载与配置

GPU 检查通过后下载；文件保存在持久化目录，重复运行会复用已下载的文件：

```bash
HF_HOME=/workspace/model-cache HF_HUB_DISABLE_XET=1 \
  python3 scripts/radeon_env.py .venv/bin/python scripts/download_models.py \
  --directory /workspace/models \
  --llm Qwen/Qwen3-4B-Instruct-2507 \
  --translation tencent/Hy-MT2-1.8B
```

首次安装已从 `configs/radeon.env.example` 生成 `.env`，已有 `.env` 不会被覆盖。此配置使用 Qwen3-ASR-0.6B-hf 识别、Hy-MT2-1.8B 独立翻译和 Qwen3-4B-Instruct-2507 生成纪要，均以 FP16 在 GPU 上运行；实际模型兼容性与耗时仍需完整 smoke 验证。

```bash
python3 scripts/radeon_env.py .venv/bin/python scripts/model_smoke.py --audio /path/to/test.wav
python3 scripts/radeon_env.py bash scripts/start.sh
```

模型测试应分别记录冷启动、热调用、音频时长和输出，确认没有错误后再演示。

## 访问服务

服务监听远程 `127.0.0.1:8765`，可以先在远程终端检查：

```bash
curl --noproxy '*' http://127.0.0.1:8765/api/health
```

**首个实例实测 SSH 配置为 `AllowTcpForwarding no`，因此不能使用普通 SSH 端口转发。** 不要把 SSH 登录成功当作端口转发已可用。若后续平台允许端口转发，可在本机终端使用下列命令，将主机与端口替换为实例卡片显示的值：

```bash
ssh -i ~/.ssh/amd_cloud -p <SSH端口> -N \
  -L 127.0.0.1:18765:127.0.0.1:8765 root@<SSH主机>
```

浏览器访问 <http://127.0.0.1:18765/>，可通过 localhost 请求麦克风权限；本地原有 8765 服务可同时保留。此方式 `.env` 的 `ROOT_PATH` 保持空。SSH 转发需要保持运行。

当前平台支持 `rc-tunnel` 提供 HTTPS / WebSocket 访问。仅使用演示样本、没有真实数据时，可将服务显式启动为 `PROVIDER=demo DATA_DIR=/workspace/meeting-demo`，在另一远程终端运行：

```bash
/var/run/secrets/frp-self-service/install
"$HOME/.local/bin/rc-tunnel" expose --port 8765
# 完成演示后关闭：
"$HOME/.local/bin/rc-tunnel" stop
```

使用返回的 HTTPS 地址，`ROOT_PATH` 保持空。隧道地址公开且不提供鉴权，本项目当前没有账号隔离；使用真实录音和会议记录前，应先增加访问控制或使用平台提供的认证代理。当前实例尚未配置公开隧道。

代码通过 GitHub 分支协作，持久化存储不会自动向其他账号共享。

实例销毁或到期时，后台进程仍会停止。重建后恢复连接、核对环境并重新启动服务；`nohup` / `tmux` 不能跨实例继续运行。

官方说明：[存储](https://amd-aim.github.io/radeon-cloud-docs/zh-cn/guides/storage/)、[镜像](https://amd-aim.github.io/radeon-cloud-docs/zh-cn/guides/images/)、[SSH](https://amd-aim.github.io/radeon-cloud-docs/zh-cn/guides/ssh/)、[服务隧道](https://amd-aim.github.io/radeon-cloud-docs/zh-cn/guides/tunnel/)。
