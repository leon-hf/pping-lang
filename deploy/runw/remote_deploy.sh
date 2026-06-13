# 在 runw 上执行(由 deploy.sh 在前面注入 IMAGE/REPO/MODELS/PORT/MODEL/DEPLOY_TAG 变量)。
# 代码已由 deploy.sh 用 tar over ssh 直接同步到 $REPO(不走 GitHub)。不要单独跑这个文件。
set -e
mkdir -p "$MODELS"

# 1) 代码已同步,确认在
[ -f "$REPO/deploy/runw/build_so.sh" ] || { echo "FATAL: $REPO 没同步到位"; exit 1; }
echo "[runw] 使用已同步工作树 @ $REPO"

# 2) (重)建容器:挂 repo→/work、本地模型目录→/models(模型缓存持久,免每次重下)
docker rm -f pdash >/dev/null 2>&1 || true
docker run -d --name pdash --restart unless-stopped --gpus all \
  -v "$REPO":/work -v "$MODELS":/models -p "$PORT":"$PORT" \
  -e MODELSCOPE_CACHE=/models -e HF_HOME=/models \
  --entrypoint /bin/bash "$IMAGE" -c "sleep infinity" >/dev/null
sleep 3
echo "[runw] container up: $(docker ps --filter name=pdash --format '{{.Status}}')"

# 3) 编 .so(自动探测 cu12/cu13)+ 装 duckdb(镜像缺)
docker exec pdash bash /work/deploy/runw/build_so.sh
docker exec pdash pip install -q duckdb -i https://pypi.tuna.tsinghua.edu.cn/simple >/dev/null 2>&1 \
  || docker exec pdash pip install -q duckdb >/dev/null 2>&1 || echo "[runw] WARN: duckdb 装失败"

# 4) 起 dashboard(注入 + cu库路径 + pre-warm,后台)
docker exec -d \
  -e CUDA_INJECTION64_PATH=/tmp/libppingcupti.so \
  -e PPING_DEPLOY_TAG="$DEPLOY_TAG" -e PPING_PORT="$PORT" -e PPING_MODEL="$MODEL" \
  pdash bash -lc 'source /tmp/pping_env.sh; PYTHONPATH=/work/src python3 /work/deploy/runw/dashboard.py > /tmp/dash.log 2>&1'
echo "[runw] dashboard launching (首次会下模型到 /models,之后命中缓存)"
