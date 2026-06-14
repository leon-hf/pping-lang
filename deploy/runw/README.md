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
2. **sync**:`tar over ssh` 把工作树直接同步到 runw(**不走 GitHub**,commit 与否都行)。
3. **runw**(`remote_deploy.sh`):重建容器(挂 repo→/work、**本地模型目录→/models**)→
   `build_so.sh` 编 .so → 装 duckdb → 起 dashboard(注入+pre-warm)。
4. **wait + verify**:轮询就绪 → POST 取证确认出真数据 → 打印 URL。

## ⚠️ 两套部署机制 —— 当前 runw 实跑的是 `pping-vllm serve`,不是本脚本

本脚本(`deploy.sh` + `dashboard.py`)起的是 **`pdash` 容器**:进程内 `from vllm import LLM` 跑 demo,
只有 `:8765` 面板。但 runw 上长期实跑的是 **`pvllm21` 容器**:`pping-vllm serve`(产品真路径),
同时有 `:8000` OpenAI server + `:8765` 面板,且 **`pping_lang` 是 pip 装在镜像里的**(不是 `/work`)。

**两者不能同时占 `:8765`。** 改了 UI 想在 `pvllm21` 上看到,别跑 `deploy.sh`(会抢端口/起第二个 vLLM),
按下面这套来。

### 在现有 `pvllm21` 上更新 UI + 重启(离线友好)

```bash
# 1) 同步工作树到 runw(deploy.sh 的 tar 步骤即可,/work 已挂进容器)
# 2) 覆盖 pip 装的 UI 文件(build_app 从 包目录/ui 读,不读 /work!)
D=/usr/local/lib/python3.12/dist-packages/pping_lang/ui
docker exec pvllm21 cp /work/src/pping_lang/ui/index.html  $D/index.html
docker exec pvllm21 cp /work/src/pping_lang/ui/dashboard.css $D/dashboard.css
docker exec pvllm21 cp /work/src/pping_lang/ui/dashboard.js  $D/dashboard.js
# 3) 干净重启 serve(先清干净,免 lingering 进程占端口/旧 HTML 闭包)
docker exec pvllm21 pkill -9 -f vllm; docker exec pvllm21 pkill -9 -f EngineCore; sleep 3
docker exec -d \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  pvllm21 bash -lc 'pping-vllm serve /models/models/Qwen/Qwen2___5-0___5B-Instruct \
    --served-model-name Qwen/Qwen2.5-0.5B-Instruct \
    --host 0.0.0.0 --port 8000 --gpu-memory-utilization 0.5 --max-model-len 2048 \
    > /tmp/vserve.log 2>&1'
# 4) 验证:轮询 :8000/v1/models 到 200(才是真载完),再看 :8765/ 是否服务新 HTML
```

### 这套里踩过的坑(都已在上面规避)

- **`build_app` 读 `包目录/ui`,不读 `/work`**(`Path(__file__).parent.parent/"ui"`)。pip 装的就得覆盖 pip
  那份;`/work` 的挂载对它无效。改完 **必须重启 serve**(HTML 启动时读进闭包)。
- **dashboard host**:旧默认 `PPING_LANG_API_HOST=127.0.0.1`,容器里 docker 端口转发够不到 → 面板
  "Empty reply"。**已修**:`pping-vllm` 现自动让 dashboard host 跟随 vllm `--host`(见 `cli.py`),所以
  `--host 0.0.0.0` 就够了,不用再手设 env。
- **离线盒子**:HF/ModelScope 即使有本地缓存也会先联网验 repo → `SSLError`/`OSError`。对策:
  `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` + **直接给本地模型目录**(`/models/models/<repo 的 ___ 转义名>`)+
  `--served-model-name` 保住 API id。
- **lingering 进程**:`pkill -f 'vllm serve'` 常杀不净(EngineCore 子进程残留 + 旧 server 仍占 :8765
  回旧 HTML)→ 用 `pkill -9 -f vllm; pkill -9 -f EngineCore` 清干净再起。
- **就绪信号看 `:8000` 不看 `:8765`**:`:8765` 可能被残留 server 顶着回 200(旧页),`:8000/v1/models`
  到 200 才代表 vLLM 真载完。
- **`deploy.sh` 的 pdash 路径离线起不来**:它在新容器里 `pip install duckdb` 需要网;离线必失败。
  离线就走上面"复用 pvllm21"这套,别建新容器。

> TODO(未做):把 `deploy/runw/` 统一成 `pping-vllm serve` 这套(复用 pip 装好的常驻容器 + 上面的
> 重启脚本),废掉 `dashboard.py`/`pdash` 这条岔路,免得再分裂。

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

- 本机:`ssh runw` 通(`~/.ssh/config` 配好)、有 `tar`、python 能跑 pytest。**不需要 GitHub**。
- runw:Docker + `vllm/vllm-openai` 镜像 + GPU(`--gpus all`)+ 能连 modelscope(下模型)。
- 目标机:GPU 性能计数器权限已开(见上)。
