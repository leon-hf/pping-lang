#!/usr/bin/env bash
# 一键把 pping-lang live dashboard 部署到 runw(远程 16GB GPU)并打印访问 URL。
# 用法:  bash deploy/runw/deploy.sh
# 跳过测试:RUN_TESTS=0 bash deploy/runw/deploy.sh
# 改目标: RUNW=other RUNW_IP=1.2.3.4 BRANCH=main bash deploy/runw/deploy.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)

RUNW=${RUNW:-runw}                                   # ssh 主机别名(~/.ssh/config)
RUNW_IP=${RUNW_IP:-100.97.8.55}                      # Tailscale IP,用于发布 URL
PORT=${PORT:-8765}
IMAGE=${IMAGE:-vllm/vllm-openai:v0.21.0}   # 跟 runw 实跑一致(v0.17.1 太旧、与当前 perf_stats/接口不匹配)
REPO=${REPO:-/home/leon/pping-work/pping-lang}       # runw 上的 repo 路径
MODELS=${MODELS:-/home/leon/pping-work/models}       # runw 上的本地模型缓存(挂进容器)
MODEL=${MODEL:-Qwen/Qwen2.5-0.5B-Instruct}
DEPLOY_TAG=${DEPLOY_TAG:-runw}                       # 部署标签,附在现读的 GPU 名后(如 "… (runw)")
BRANCH=${BRANCH:-$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)}
RUN_TESTS=${RUN_TESTS:-1}
URL="http://$RUNW_IP:$PORT"

echo "== deploy pping-lang dashboard -> $RUNW =="
echo "   branch=$BRANCH  image=$IMAGE  url=$URL"

# 1) CI:本地跑测试,绿了才部署
if [ "$RUN_TESTS" = "1" ]; then
  echo "[ci] pytest -q ..."
  ( cd "$ROOT" && python -m pytest -q ) || { echo "[ci] 测试失败,中止部署。RUN_TESTS=0 可跳过。"; exit 1; }
fi

# 2) 同步工作树到 runw —— 直接 tar over ssh,不走 GitHub(commit 与否都行)
echo "[sync] 推工作树 -> $RUNW:$REPO(不走 GitHub)..."
ssh "$RUNW" "mkdir -p '$REPO'"
tar czf - -C "$ROOT" \
  --exclude=.git --exclude=_scratch --exclude=_design-notes \
  --exclude=__pycache__ --exclude=.pytest_cache --exclude=node_modules \
  --exclude='*.duckdb' --exclude='*.so' . \
  | ssh "$RUNW" "tar xzf - -C '$REPO'"
echo "[sync] 完成"

# 3) runw 上部署(把配置变量注入到 remote_deploy.sh 前面再喂给远端 bash)
{
  echo "BRANCH='$BRANCH'"; echo "IMAGE='$IMAGE'"; echo "REPO='$REPO'"
  echo "MODELS='$MODELS'"; echo "PORT='$PORT'"; echo "MODEL='$MODEL'"; echo "DEPLOY_TAG='$DEPLOY_TAG'"
  cat "$HERE/remote_deploy.sh"
} | ssh "$RUNW" bash -s

# 4) 等就绪(首次下模型会久;命中缓存后 ~1 分钟)
echo "[wait] vLLM 载入 + pre-warm ..."
ready=0
for i in $(seq 1 90); do
  if ssh "$RUNW" 'docker exec pdash grep -q "steady sampling" /tmp/dash.log 2>/dev/null'; then ready=1; break; fi
  if ssh "$RUNW" 'docker exec pdash grep -qiE "Traceback|START_FAILED" /tmp/dash.log 2>/dev/null'; then
    echo "[err] 启动报错,末尾日志:"; ssh "$RUNW" 'docker exec pdash tail -20 /tmp/dash.log'; exit 2
  fi
  sleep 5
done
[ "$ready" = 1 ] || echo "[warn] 未在 ~7.5min 内就绪;查:ssh $RUNW docker exec pdash tail -30 /tmp/dash.log"

# 5) 验证 + 发布 URL(重试几次:pre-warm 刚完、首个窗口 drain 完前 POST 可能慢)
echo "[verify] POST deep_evidence ..."
for vtry in 1 2 3 4; do
  resp=$(curl -s --noproxy '*' --max-time 25 -X POST "$URL/api/kernels/deep_evidence?window=5" 2>/dev/null || true)
  line=$(printf '%s' "$resp" | python3 -c "import sys,json
try: d=json.load(sys.stdin)
except Exception: print('RETRY'); sys.exit(0)
st=d.get('stall_shares') or []
if not d.get('available'): print('NOPERM'); sys.exit(0)
top=(' | top: %s %.1f%%' % (st[0]['cls'], st[0]['pct'])) if st else ''
print('OK available=True sample_total=%d%s' % (int(d.get('sample_total') or 0), top))
" 2>/dev/null || echo RETRY)
  case "$line" in
    OK*)    echo "  $line"; break;;
    NOPERM) echo "  ⚠ PC sampling 不可用 —— GPU 性能计数器权限没开(NVIDIA 控制面板/App → 开发者 → 允许所有用户),开了重跑"; break;;
    *)      [ "$vtry" = 4 ] && echo "  (暂未拿到数据;可能仍在 warmup,打开页面 Kernel tab 会自动取证)" || sleep 5;;
  esac
done

echo ""
echo "============================================================"
echo "  ✅ DASHBOARD LIVE:  $URL   →  打开点 Kernel tab(自动取证)"
echo "  停止(释放 GPU):    bash $HERE/stop.sh"
echo "============================================================"
