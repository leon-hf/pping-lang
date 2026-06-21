# build_image.sh — 在 runw 上把 pping-lang 烤进 vllm 镜像(docker 与 k8s 两路共用)。
# 由 deploy.sh 注入 BASE_IMAGE / IMAGE_TAG / REPO 后,作为前缀拼到 remote_*.sh 前面一起跑。
# 不要单独跑。产物 = 本地 docker 镜像 $IMAGE_TAG。
set -e

# 基础镜像就位(daemon.json 的 registry-mirrors 走国内源;已有则秒过)
sudo docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 || { echo "[build] pull $BASE_IMAGE ..."; sudo docker pull "$BASE_IMAGE"; }

# build:只有 src/pyproject 变了才重装 pping-lang 层,base 33GB 层全程缓存命中 → 秒级重建。
echo "[build] docker build $IMAGE_TAG (FROM $BASE_IMAGE) ..."
sudo docker build --build-arg BASE="$BASE_IMAGE" -t "$IMAGE_TAG" -f "$REPO/deploy/runw/Dockerfile" "$REPO"
echo "[build] done: $(sudo docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -F "$IMAGE_TAG" | head -1)"
