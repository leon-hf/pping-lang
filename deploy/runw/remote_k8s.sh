# remote_k8s.sh — runw 上的 **k3s 路径**。**用 buildkit 直接 build 进 k3s 的 containerd**
# (无 docker save | ctr import,33GB 基础层一次都不搬;只 build 变了的 pip 层)→ 渲染
# manifests → kubectl apply → rollout。不要单独跑。
set -e
KC="sudo k3s kubectl"

# 单 GPU:先停掉 docker 路那份容器(若在跑),免抢卡。
sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

# 1) buildctl 直接 build 进 k8s.io containerd。`--output type=image` 直接给 tag,k3s 立即可用。
#    base 层已在 containerd(按摘要去重,解析 ~0.4s);只重 build 变了的 pip 层(~2min,不变则秒过)。
echo "[k8s] buildctl build → k3s containerd(无 33GB 搬运)..."
sudo buildctl build \
  --frontend dockerfile.v0 \
  --opt build-arg:BASE="docker.io/$BASE_IMAGE" \
  --local context="$REPO" \
  --local dockerfile="$REPO/deploy/runw" \
  --output type=image,name="docker.io/library/$IMAGE_TAG" 2>&1 \
  | grep -iE "plugin entry point OK|naming to|ERROR|error:" | tail -4
echo "[k8s] containerd 镜像: $(sudo k3s ctr -n k8s.io images ls -q 2>/dev/null | grep -F "$IMAGE_TAG" | head -1)"

# 2) 渲染 manifests(sed 替换占位符)→ apply
TMP=$(mktemp -d)
for f in deployment service ingress; do
  sed -e "s|__IMAGE__|$IMAGE_TAG|g" \
      -e "s|__MODEL__|$MODEL|g" \
      -e "s|__DASH_PORT__|$DASH_PORT|g" \
      -e "s|__OAI_PORT__|$OAI_PORT|g" \
      -e "s|__MODELS__|$MODELS|g" \
      -e "s|__DASH_HOST__|$DASH_HOST|g" \
      -e "s|__OAI_HOST__|$OAI_HOST|g" \
      "$REPO/deploy/runw/k8s/$f.yaml" > "$TMP/$f.yaml"
done
$KC apply -f "$TMP/"
rm -rf "$TMP"

# 镜像 tag 固定(pping-lang:dev)+ imagePullPolicy:Never → 重导镜像但 Deployment spec 没变时
# k8s 不会重建 pod,旧 pod 仍跑旧镜像。**强制 rollout restart** 让 pod 用上刚导入的新镜像。
$KC rollout restart deploy/pping-lang -n default
echo "[k8s] rollout restart(拉起新镜像)。等就绪(首次下模型久,readinessProbe 探 /v1/models)..."

# 3) 等 Deployment 就绪(pod Ready = vllm 真载完)
$KC rollout status deploy/pping-lang -n default --timeout=900s || {
  echo "[k8s] rollout 未就绪,末尾日志:"; $KC logs deploy/pping-lang -n default --tail=40 2>/dev/null || true; }
