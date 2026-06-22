# remote_deploy.sh — runw 上的 **docker 路径**(由 deploy.sh 注入变量,并在前面拼 build_image.sh
# 先把镜像烤好)。跑**烤好的镜像**(pping-lang 已 pip 装进镜像,非 /work mount)→ `pping-vllm serve`
# → 插件随 vllm serve 自动加载(DiagnosisEngine + dashboard;.so 能编则 PC sampling,否则降级)。
# 不要单独跑。
set -e

# 单 GPU:先停掉 k8s 路那份(若在跑),免抢卡。
sudo k3s kubectl delete deploy pping-lang -n default --ignore-not-found --wait=false >/dev/null 2>&1 || true

# (重)建容器:挂模型缓存→/models(镜像已自带 pping-lang,不挂 /work);发布 dashboard + OpenAI。
# 镜像 entrypoint 是 vllm serve,必须 --entrypoint /bin/bash 覆盖,否则 sleep infinity 被当参数。
sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
sudo docker run -d --name "$CONTAINER" --restart unless-stopped --gpus all \
  --cap-add SYS_ADMIN \
  -v "$MODELS":/models \
  -p "$DASH_PORT":"$DASH_PORT" -p "$OAI_PORT":"$OAI_PORT" \
  -e VLLM_USE_MODELSCOPE=True -e MODELSCOPE_CACHE=/models -e HF_HOME=/models \
  -e PPING_LANG_API_PORT="$DASH_PORT" -e PPING_DEPLOY_TAG="$DEPLOY_TAG" \
  --entrypoint /bin/bash "$IMAGE_TAG" -c "sleep infinity" >/dev/null
sleep 2
echo "[runw] container up: $(sudo docker ps --filter name=$CONTAINER --format '{{.Status}}')"

# 起 pping-vllm serve(后台)。--host 0.0.0.0 让 OpenAI 与 dashboard 都对外(host 跟随 --host)。
sudo docker exec -d \
  -e VLLM_USE_MODELSCOPE=True -e MODELSCOPE_CACHE=/models -e HF_HOME=/models \
  -e PPING_LANG_API_PORT="$DASH_PORT" -e PPING_DEPLOY_TAG="$DEPLOY_TAG" \
  "$CONTAINER" bash -lc "pping-vllm serve '$MODEL' --host 0.0.0.0 --port $OAI_PORT \
     --gpu-memory-utilization 0.5 --max-model-len 2048 --enforce-eager > /tmp/pvllm.log 2>&1"
# --enforce-eager:跳过 CUDA graph 捕获 —— vLLM 0.21 + Blackwell(sm_120)上反复在捕获阶段挂死
# (日志停在 KV cache、GPU 0% 空转)。eager 模式略慢但启动稳;指标/诊断/dashboard 全可用。
echo "[runw] pping-vllm serve launching(首次下模型到 /models;之后命中缓存)"
