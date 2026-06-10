# deploy/runw — 一键部署 live dashboard 到远程 GPU(runw)

把 pping-lang 的 vLLM live dashboard 部署到远程 16GB GPU 机(runw,RTX 5060 Ti / WSL2 /
Tailscale),并发布访问 URL。本地笔记本不再常驻烧 GPU。

## 一键用

```bash
bash deploy/runw/deploy.sh          # 测试→推代码→runw 拉取/编译/起服务→打印 URL
bash deploy/runw/stop.sh            # 看完停掉,释放 GPU
```

跳过测试:`RUN_TESTS=0 bash deploy/runw/deploy.sh`
换目标机:`RUNW=xxx RUNW_IP=1.2.3.4 IMAGE=vllm/vllm-openai:latest bash deploy/runw/deploy.sh`

部署完打印:`✅ DASHBOARD LIVE: http://100.97.8.55:8765 → Kernel tab`。浏览器(同 tailnet)打开,
进 Kernel tab **自动取证**出真实 vLLM stall 数据。

## 它做了什么

1. **CI**:本地 `pytest -q`,绿了才继续。
2. **push**:当前分支推 GitHub。
3. **runw**(`remote_deploy.sh`):`git reset --hard` 同步 → 重建容器(挂 repo→/work、
   **本地模型目录→/models**)→ `build_so.sh` 编 .so → 装 duckdb → 起 dashboard(注入+pre-warm)。
4. **wait + verify**:轮询就绪 → POST 取证确认出真数据 → 打印 URL。

## 设计要点 / 踩过的坑(都已固化在脚本里)

- **模型本地缓存**:`-v $MODELS:/models -e MODELSCOPE_CACHE=/models`,首次下载落到挂载目录,
  之后命中缓存免重下(`MODELS` 默认 `/home/leon/pping-work/models`)。
- **镜像 entrypoint**:`vllm/vllm-openai` 的 entrypoint 是 `vllm serve`,必须 `--entrypoint /bin/bash`
  覆盖,否则 `sleep infinity` 被当参数、容器秒退。
- **cu12/cu13 自适应**:`build_so.sh` 自动探测 `libcupti.so.12/13` + cupti 头 + cuda.h + libcuda stub,
  换镜像不用改。
- **PC sampling 权限(关键前置)**:目标机 Windows 宿主要开 **NVIDIA 控制面板/NVIDIA App →
  开发者 → 管理 GPU 性能计数器 → 允许所有用户**。没开时表现为 `INSUFFICIENT_PRIVILEGES` 或
  `HARDWARE_BUSY(26)`,verify 会提示。
- **§11 稳定性**:dashboard 内置 pre-warm 吃光 triton JIT + .so 默认 500ms cadence + 300ms JIT
  冷却,稳态无运行时 JIT → 持续采样稳(详见 `native/ppingcupti/README.md §11`)。
- **访问**:容器绑 `0.0.0.0:$PORT`,经目标机 Tailscale IP 访问;curl 用 `--noproxy '*'` 绕系统代理。

## 前置

- 本机:`ssh runw` 通(`~/.ssh/config` 配好)、git 能 push、python 能跑 pytest。
- runw:Docker + `vllm/vllm-openai` 镜像 + GPU(`--gpus all`)+ 能拉 GitHub/modelscope。
- 目标机:GPU 性能计数器权限已开(见上)。
