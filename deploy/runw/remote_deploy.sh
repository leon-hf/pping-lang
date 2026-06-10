# 在 runw 上执行(由 deploy.sh 在前面注入 BRANCH/IMAGE/REPO/MODELS/PORT/MODEL/GPU_NAME 变量)。
# 不要单独跑这个文件。
set -e
REPO_PARENT=$(dirname "$REPO")
mkdir -p "$REPO_PARENT" "$MODELS"

# 1) 同步代码(runw 从 GitHub 拉)
if [ -d "$REPO/.git" ]; then
  cd "$REPO"; git fetch -q origin "$BRANCH"; git checkout -q "$BRANCH"; git reset -q --hard "origin/$BRANCH"
else
  git clone -q -b "$BRANCH" https://github.com/leon-hf/pping-lang.git "$REPO"
fi
echo "[runw] repo @ $(git -C "$REPO" rev-parse --short HEAD) ($BRANCH)"

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
  -e PPING_GPU_NAME="$GPU_NAME" -e PPING_PORT="$PORT" -e PPING_MODEL="$MODEL" \
  pdash bash -lc 'source /tmp/pping_env.sh; PYTHONPATH=/work/src python3 /work/deploy/runw/dashboard.py > /tmp/dash.log 2>&1'
echo "[runw] dashboard launching (首次会下模型到 /models,之后命中缓存)"
