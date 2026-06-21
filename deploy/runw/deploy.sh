#!/usr/bin/env bash
# 一键把 pping-lang 部署到 runw(远程 GPU 机)。pping-lang 按**用户安装方式 pip install 烤进镜像**,
# 跑 `pping-vllm serve` → 插件随 vllm serve 自动加载(指标 + 诊断 + dashboard;能编 .so 则 PC sampling)。
#
# 两套部署路(单 GPU,不同时跑;每套起前会停掉另一套):
#   MODE=docker (默认)  裸 Docker,主机端口发布,URL = http://<runw-ip>:8765
#   MODE=k8s           k3s Deployment+Service+Ingress(Traefik),稳定 URL = http://pping.<runw-ip>.nip.io
#
# 用法:  bash deploy/runw/deploy.sh            # docker 路
#        MODE=k8s bash deploy/runw/deploy.sh   # k3s 路
# 选项:  RUN_TESTS=0 跳过测试 | WARM=0 不发预热 | MODEL=... 换模型 | RUNW=/RUNW_IP=/IMAGE=... 改目标
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)

MODE=${MODE:-docker}                                 # docker | k8s
RUNW=${RUNW:-runw}                                   # ssh 主机别名(~/.ssh/config)
RUNW_IP=${RUNW_IP:-}                                 # 发布 URL 用;留空则部署时从 runw 现读 Tailscale IP
DASH_PORT=${DASH_PORT:-8765}                         # dashboard 端口
OAI_PORT=${OAI_PORT:-8001}                           # vLLM OpenAI server 端口(8000 常被别的服务占)
BASE_IMAGE=${IMAGE:-vllm/vllm-openai:v0.21.0}        # 基础镜像(registry-mirrors 走国内源)
IMAGE_TAG=${IMAGE_TAG:-pping-lang:dev}               # 烤好的镜像(pping-lang 已 pip 装进去)
CONTAINER=${CONTAINER:-pvllm}                        # docker 路容器名
REPO=${REPO:-/home/leon/pping-work/pping-lang}       # runw 上的 repo 路径
MODELS=${MODELS:-/home/leon/pping-work/models}       # runw 上的模型缓存(挂进容器)
MODEL=${MODEL:-Qwen/Qwen2.5-0.5B-Instruct}
DEPLOY_TAG=${DEPLOY_TAG:-runw}
RUN_TESTS=${RUN_TESTS:-1}
WARM=${WARM:-1}

echo "== deploy pping-lang [MODE=$MODE] -> $RUNW =="

# 0) ssh 通 + GPU 在
ssh -o BatchMode=yes -o ConnectTimeout=10 "$RUNW" 'nvidia-smi -L | head -1' \
  || { echo "[err] ssh $RUNW 不通 / 无 GPU。先确认 runw 起着、Tailscale 在线。"; exit 1; }

# RUNW_IP 没显式给 → 从 runw 现读 Tailscale IP(换机器/IP 变了也不会打印错 URL)
if [ -z "$RUNW_IP" ]; then
  RUNW_IP=$(ssh "$RUNW" 'tailscale ip -4 2>/dev/null | head -1' || true)
  [ -z "$RUNW_IP" ] && RUNW_IP=$(ssh "$RUNW" "hostname -I | tr ' ' '\n' | grep -E '^100\.' | head -1" || true)
  [ -z "$RUNW_IP" ] && { echo "[warn] 读不到 runw Tailscale IP,用 localhost 占位"; RUNW_IP="127.0.0.1"; }
fi
DASH_HOST="pping.$RUNW_IP.nip.io"                    # k8s ingress host(nip.io 解析回该 IP)
OAI_HOST="api-pping.$RUNW_IP.nip.io"
echo "[net] runw IP = $RUNW_IP"

# 1) bootstrap:k8s 路需 buildctl(buildkit),docker 路需 docker —— 缺则装齐(幂等)
NEED="docker"; [ "$MODE" = "k8s" ] && NEED="buildctl"
if ! ssh "$RUNW" "command -v $NEED >/dev/null 2>&1"; then
  echo "[bootstrap] runw 缺 $NEED,安装依赖(docker + nvidia + buildkit) ..."
  ssh "$RUNW" 'bash -s' < "$HERE/bootstrap.sh"
fi

# 2) CI:本地跑测试,绿了才部署
if [ "$RUN_TESTS" = "1" ]; then
  echo "[ci] pytest -q ..."
  ( cd "$ROOT" && python -m pytest -q ) || { echo "[ci] 测试失败,中止。RUN_TESTS=0 可跳过。"; exit 1; }
fi

# 3) 同步工作树到 runw —— tar over ssh,不走 GitHub(commit 与否都行)
echo "[sync] 推工作树 -> $RUNW:$REPO ..."
ssh "$RUNW" "mkdir -p '$REPO'"
tar czf - -C "$ROOT" \
  --exclude=.git --exclude=_scratch --exclude=_design-notes \
  --exclude=__pycache__ --exclude=.pytest_cache --exclude=node_modules \
  --exclude='*.duckdb' --exclude='*.jsonl' --exclude='*.so' . \
  | ssh "$RUNW" "tar xzf - -C '$REPO'"
echo "[sync] 完成"

# 注入给远端脚本的配置变量(build_image.sh + remote_*.sh 共用)
remote_vars() {
  echo "BASE_IMAGE='$BASE_IMAGE'"; echo "IMAGE_TAG='$IMAGE_TAG'"; echo "REPO='$REPO'"
  echo "MODELS='$MODELS'"; echo "MODEL='$MODEL'"; echo "CONTAINER='$CONTAINER'"
  echo "DASH_PORT='$DASH_PORT'"; echo "OAI_PORT='$OAI_PORT'"; echo "DEPLOY_TAG='$DEPLOY_TAG'"
  echo "DASH_HOST='$DASH_HOST'"; echo "OAI_HOST='$OAI_HOST'"
}

if [ "$MODE" = "k8s" ]; then
  # ── k3s 路:build 镜像 → 导进 containerd → apply manifests → 等 rollout ──
  # ingress host 用 Traefik 的 LB IP(它真正监听的 IP),不是 tailscale IP —— k3s servicelb 把
  # traefik 绑在节点主网卡 IP 上,不一定在 tailscale 接口。nip.io 解析回该 IP 才打得通。
  LB_IP=$(ssh "$RUNW" "sudo k3s kubectl get svc traefik -n kube-system -o jsonpath='{.status.loadBalancer.ingress[0].ip}'" 2>/dev/null || true)
  [ -z "$LB_IP" ] && LB_IP="$RUNW_IP"
  DASH_HOST="pping.$LB_IP.nip.io"; OAI_HOST="api-pping.$LB_IP.nip.io"
  echo "[k8s] Traefik LB IP = $LB_IP → ingress host $DASH_HOST"
  # k8s 路用 buildkit 直 build 进 containerd(remote_k8s 内部做),不再 docker build + save|import。
  { remote_vars; cat "$HERE/remote_k8s.sh"; } | ssh "$RUNW" bash -s
  # 预热 + 验证走 hostPort(localhost,可靠);ingress 经 traefik 受客户端代理/节点 hairpin 影响,不用来自检。
  if [ "$WARM" = "1" ]; then
    echo "[warm] 经 hostPort 发 20 轮请求 ..."
    ssh "$RUNW" "for n in \$(seq 1 20); do curl -s -m 25 http://127.0.0.1:$OAI_PORT/v1/chat/completions \
      -H 'Content-Type: application/json' \
      -d '{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Explain how a transformer works, in detail.\"}],\"max_tokens\":128}' \
      -o /dev/null; done" >/dev/null 2>&1 || true
  fi
  health=$(ssh "$RUNW" "curl -s -m 5 http://127.0.0.1:$DASH_PORT/api/health 2>/dev/null" || true)
  echo ""
  echo "============================================================"
  echo "  ✅ DASHBOARD (IP:端口直达,hostPort,最稳):  http://$RUNW_IP:$DASH_PORT"
  echo "     OpenAI:                                http://$RUNW_IP:$OAI_PORT/v1   (model: $MODEL)"
  echo "  ── 或 ingress 干净 URL(需客户端代理放行 *.nip.io / Traefik 在该 IP 可达)──"
  echo "     DASHBOARD:  http://$DASH_HOST"
  echo "     OpenAI:     http://$OAI_HOST/v1"
  [ -n "$health" ] && echo "     health: ${health:0:90}"
  echo "  停止(释放 GPU):  MODE=k8s bash $HERE/stop.sh"
  echo "============================================================"
else
  # ── docker 路:build 镜像 → 跑烤好的镜像 → pping-vllm serve ──
  { remote_vars; cat "$HERE/build_image.sh"; cat "$HERE/remote_deploy.sh"; } | ssh "$RUNW" bash -s
  echo "[wait] vLLM 载入中(首次下模型 ~1-2min,命中缓存 ~30s)..."
  ready=0
  for i in $(seq 1 120); do
    if ssh "$RUNW" "curl -sf -m 3 http://localhost:$OAI_PORT/v1/models -o /dev/null 2>/dev/null"; then ready=1; break; fi
    if ssh "$RUNW" "sudo docker exec $CONTAINER grep -qiE 'Traceback|Error:|raise SystemExit' /tmp/pvllm.log 2>/dev/null"; then
      echo "[err] 启动疑似报错,末尾日志:"; ssh "$RUNW" "sudo docker exec $CONTAINER tail -25 /tmp/pvllm.log"; exit 2
    fi
    sleep 5
  done
  [ "$ready" = 1 ] || echo "[warn] 未在 ~10min 内就绪。查:ssh $RUNW sudo docker exec $CONTAINER tail -40 /tmp/pvllm.log"
  if [ "$WARM" = "1" ] && [ "$ready" = "1" ]; then
    echo "[warm] 发 20 轮请求让面板出数据 + 诊断 ..."
    ssh "$RUNW" "for n in \$(seq 1 20); do curl -s -m 25 http://localhost:$OAI_PORT/v1/chat/completions \
      -H 'Content-Type: application/json' \
      -d '{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Explain how a transformer works, in detail.\"}],\"max_tokens\":128}' \
      -o /dev/null; done" >/dev/null 2>&1 || true
  fi
  health=$(ssh "$RUNW" "curl -s -m 5 http://localhost:$DASH_PORT/api/health 2>/dev/null" || true)
  echo ""
  echo "============================================================"
  echo "  ✅ DASHBOARD LIVE:  http://$RUNW_IP:$DASH_PORT"
  echo "     OpenAI API:      http://$RUNW_IP:$OAI_PORT/v1   (model: $MODEL)"
  [ -n "$health" ] && echo "     health: ${health:0:90}"
  echo "  停止(释放 GPU):  bash $HERE/stop.sh"
  echo "============================================================"
fi
