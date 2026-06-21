# deploy/runw — 一键把 pping-lang 部署到远程 GPU(runw),两套部署路

pping-lang 按**用户安装方式 `pip install` 烤进官方 vLLM 镜像**(`Dockerfile`,非 editable mount),
跑 `pping-vllm serve` → 插件随 vllm serve 自动加载:实时指标 + 事实规则诊断 + dashboard,能编 `.so`
则附带 PC sampling(编不出优雅降级)。同时发布 OpenAI server。目标机 = runw(RTX 5060 Ti 16GB /
Tailscale,IP 部署时从 runw 现读,当前 `100.107.244.84`)。

**两套部署路共用同一个烤好的镜像**(单 GPU,不同时跑;每套起前会停掉另一套):

| MODE | 形态 | 访问 URL |
|---|---|---|
| `docker`(默认) | 裸 Docker,`-p` 主机端口 | `http://<runw-ip>:8765` |
| `k8s` | k3s Deployment + Service + Ingress + **hostPort** | `http://<runw-ip>:8765`(hostPort,最稳)或 `http://pping.<runw-ip>.nip.io`(ingress 干净 URL) |

> **k8s 两种入口**:pod 同时开 **hostPort**(节点直接发布 :8765/:8001,像 docker,经 LAN/tailscale IP 都通)
> 和 **Traefik ingress**(`*.nip.io` 干净 URL)。ingress 受**客户端代理**(如 Clash 会拦 `*.nip.io` 域名)
> 和**节点 hairpin**影响时,用 hostPort 的 `IP:端口` 最稳(IP 字面量代理一般直连放行)。

## 一键用

```bash
bash deploy/runw/deploy.sh             # docker 路 → http://<runw-ip>:8765
MODE=k8s bash deploy/runw/deploy.sh    # k3s 路   → http://pping.<runw-ip>.nip.io
bash deploy/runw/stop.sh               # 停 docker 路(MODE=k8s bash stop.sh 停 k8s 路)
```

- 跳过测试:`RUN_TESTS=0 ...` | 不发预热:`WARM=0 ...` | 换模型:`MODEL=Qwen/Qwen2.5-7B-Instruct-AWQ ...`
- 换目标机:`RUNW=xxx RUNW_IP=1.2.3.4 IMAGE=vllm/vllm-openai:latest ...`

dashboard 实时 tab 出指标,诊断 tab 出事实规则触发(预热流量喂够数据后)。

## 它做了什么(`deploy.sh`)

1. **ssh + GPU 检查** + **现读 runw 的 Tailscale IP**(换机器/IP 变了也不打印错 URL)。
2. **bootstrap(幂等)**:runw 没 docker → 自动装 docker + NVIDIA 容器运行时 + 国内镜像源。
3. **CI**:本地 `pytest -q`,绿了才继续。
4. **sync**:`tar over ssh` 把工作树同步到 runw(**不走 GitHub**,commit 与否都行)。
5. **build 镜像**(`build_image.sh`):`docker build` 把 pping-lang `pip install` 进 `vllm` 镜像
   → `pping-lang:dev`(base 33GB 层全缓存,只重装 pping-lang 层 → 秒级重建)。
6. **按 MODE 分支**:
   - **docker**(`remote_deploy.sh`):停 k8s 那份 → 跑烤好的镜像(挂 /models,**不挂 /work**)
     → `pping-vllm serve` → 轮询 OpenAI `/v1/models` 就绪。
   - **k8s**(`remote_k8s.sh`):停 docker 那份 → **`buildctl` 直接 build 进 k3s 的 containerd**
     (`--output type=image`,无 `docker save | ctr import`,33GB 基础层一次不搬,只 build 变了的 pip 层)
     → 渲染 `k8s/*.yaml` → `kubectl apply` → `rollout restart` → `rollout status` 等就绪。
7. **warm + 发布 URL**:发几轮请求让面板出数据/诊断,打印 URL。

## 设计要点 / 踩过的坑(都已固化在脚本里)

- **烤镜像 = 用户安装方式**:`Dockerfile` 里 `pip install <源码目录>` 让 hatchling 出 wheel 装进
  site-packages,再删源码 → 镜像里就是个装好的 `pping_lang` 包 + `pping-vllm` 脚本 +
  `vllm.stat_logger_plugins` entry point。改代码 → 重 build(秒级)→ 新镜像。**不再 mount /work**。
- **dashboard host 跟随 `--host`**:`pping-vllm` 在用户给了 vllm `--host 0.0.0.0` 时自动
  `setdefault PPING_LANG_API_HOST=0.0.0.0`(见 `cli.py`),容器/pod 端口转发才够得到。
- **端口固定不漂(docker 路)**:redeploy 前先 `docker rm -f` 旧容器释放端口。dashboard `8765` /
  OpenAI `8001`(8000 在 runw 被别的服务占)。真冲突就 `DASH_PORT=/OAI_PORT=` 覆盖。
- **稳定 URL(k8s 路)**:Traefik ingress + `nip.io`(`<label>.<ip>.nip.io` 公网解析回该 IP)→
  经 Tailscale IP 出固定 URL,**不碰主机端口**。dashboard 与 OpenAI 各一个 host。
- **k3s 镜像用 buildkit 直 build,不搬 33GB**:`docker save | ctr import` 每次重灌整 33GB(管道不去重)→
  containerd 解包慢、甚至 `FailedCreatePodSandBox`。改用 **buildkitd(k3s containerd worker,k8s.io 命名空间)
  + `buildctl --output type=image`**:镜像直接落进 k3s 的库,base 层按摘要复用(解析 ~0.4s),只 build 变了的
  pip 层 → 部署从 ~30min 降到 ~30s build + rollout。manifest `imagePullPolicy: Never`。
  **坑**:buildkit 解析 base 走 `?ns=docker.io`,**daocloud 会 401** → 镜像源把 `docker.1panel.live` /
  `docker.actima.top` 放前面(见 `bootstrap.sh` 的 `/etc/buildkit/buildkitd.toml`)。
- **k8s GPU**:`runtimeClassName: nvidia` + `resources.limits.nvidia.com/gpu: 1`(runw 已装
  nvidia-device-plugin + runtimeclass)。vllm 要大 `/dev/shm` → emptyDir Memory 2Gi。
- **镜像 entrypoint**:`vllm/vllm-openai` 的 entrypoint 是 `vllm serve`,docker 路必须
  `--entrypoint /bin/bash` 覆盖;k8s 路用 `command: [bash,-lc]` 覆盖。
- **国内拉镜像**:Docker Hub 直连被墙。`bootstrap.sh` 把 `registry-mirrors`(daocloud/1panel/actima)
  写进 `/etc/docker/daemon.json`,`IMAGE` 用干净名即走镜像源。
- **模型缓存**:`-v $MODELS:/models`(k8s 用 hostPath 同路径,两路共享缓存)+ `MODELSCOPE_CACHE=/models`
  + `VLLM_USE_MODELSCOPE=True`,首次下载落盘,之后命中缓存。
- **就绪信号看 OpenAI `/v1/models` 不看 dashboard**:dashboard 可能先于 vLLM 起来。
- **PC sampling 是 best-effort**:`pping-vllm` 尝试编 `.so`,编不出/无权限则降级(仍有 KPI/roofline/
  NVML/**诊断**)。要 Kernel tab 真数据需 GPU 性能计数器权限。

## 前置

- 本机:`ssh runw` 通、有 `tar`、python 能跑 pytest。**不需要 GitHub**。
- runw:NVIDIA 驱动 + 免密 sudo;**docker 路**由 `bootstrap.sh` 自动装 docker;**k8s 路**需 runw 上
  k3s 已起且 GPU-ready(nvidia-device-plugin + `nvidia` runtimeclass + Traefik,runw 已具备)。
- 网络:目标机能连 modelscope(下模型)。

## 文件

`deploy.sh`(编排,MODE 分支)· `Dockerfile` + `build_image.sh`(烤镜像,两路共用)·
`remote_deploy.sh`(docker 路)· `remote_k8s.sh` + `k8s/*.yaml`(k8s 路)· `bootstrap.sh`(裸机装 docker)·
`stop.sh`(两路)。
