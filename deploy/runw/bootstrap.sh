#!/usr/bin/env bash
# bootstrap.sh — 在目标 GPU 机上一次性装好 Docker + NVIDIA 容器运行时 + 国内镜像源。
# 幂等:已装的跳过。由 deploy.sh 在「runw 上没有 docker」时自动 ssh 进来跑;也可单独跑。
#
#   ssh runw 'bash -s' < deploy/runw/bootstrap.sh
#
# 前置:目标机已装 NVIDIA 驱动(nvidia-smi 能用)、当前用户有免密 sudo。
set -e

echo "[bootstrap] 目标:$(hostname)  user=$(whoami)"

# 1) Docker engine
if ! command -v docker >/dev/null 2>&1; then
  echo "[bootstrap] apt install docker.io ..."
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
else
  echo "[bootstrap] docker 已装:$(docker --version)"
fi
# 当前用户加入 docker 组(免 sudo;需重新登录生效。脚本本身仍用 sudo,无依赖)
sudo usermod -aG docker "$(whoami)" 2>/dev/null || true

# 2) NVIDIA Container Toolkit(--gpus all 的前提)
if ! command -v nvidia-ctk >/dev/null 2>&1; then
  echo "[bootstrap] 装 nvidia-container-toolkit ..."
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nvidia-container-toolkit
else
  echo "[bootstrap] nvidia-ctk 已装"
fi

# 3) /etc/docker/daemon.json:nvidia runtime + 国内镜像源(Docker Hub 直连被墙)
echo "[bootstrap] 写 daemon.json(nvidia runtime + 镜像加速)"
sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{
    "runtimes": { "nvidia": { "args": [], "path": "nvidia-container-runtime" } },
    "registry-mirrors": [
        "https://docker.m.daocloud.io",
        "https://docker.1panel.live",
        "https://docker.actima.top"
    ]
}
JSON
sudo nvidia-ctk runtime configure --runtime=docker >/dev/null 2>&1 || true
sudo systemctl restart docker
sleep 2

# 3b) nerdctl + buildkit:k8s 路用 buildkit 直接 build 进 k3s 的 containerd
#     (免 docker save | ctr import 反复搬 33GB,redeploy 只 build 变了的 pip 层)。
if ! command -v buildctl >/dev/null 2>&1; then
  echo "[bootstrap] 装 nerdctl-full(含 buildkitd/buildctl)..."
  NVER=$(curl -sI -m 12 https://github.com/containerd/nerdctl/releases/latest 2>/dev/null \
         | grep -i '^location:' | grep -o 'tag/v[0-9.]*' | sed 's|tag/v||' | tr -d '\r')
  [ -z "$NVER" ] && NVER=2.3.3
  curl -fsSL -m 360 -o /tmp/nerdctl-full.tgz \
    "https://github.com/containerd/nerdctl/releases/download/v${NVER}/nerdctl-full-${NVER}-linux-amd64.tar.gz"
  sudo tar -xzf /tmp/nerdctl-full.tgz -C /usr/local && rm -f /tmp/nerdctl-full.tgz
fi
# buildkitd:用 k3s 的 containerd 当 worker(k8s.io 命名空间)+ 国内镜像源。
# 注意:buildkit 解析 base 走 `?ns=docker.io`,daocloud 会 401 → 必须把 1panel/actima 放前面。
echo "[bootstrap] 配 buildkitd(k3s containerd worker + 镜像源)"
sudo mkdir -p /etc/buildkit
sudo tee /etc/buildkit/buildkitd.toml >/dev/null <<'TOML'
[worker.oci]
  enabled = false
[worker.containerd]
  enabled = true
  address = "/run/k3s/containerd/containerd.sock"
  namespace = "k8s.io"
[registry."docker.io"]
  mirrors = ["docker.1panel.live", "docker.actima.top", "docker.m.daocloud.io"]
TOML
sudo tee /etc/systemd/system/buildkit.service >/dev/null <<'UNIT'
[Unit]
Description=BuildKit (k3s containerd worker)
After=k3s.service
[Service]
ExecStart=/usr/local/bin/buildkitd --config /etc/buildkit/buildkitd.toml
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now buildkit >/dev/null 2>&1 || true

# 4) 冒烟:容器内能看到 GPU
echo "[bootstrap] 冒烟测试 GPU 直通 ..."
sudo docker run --rm --gpus all docker.m.daocloud.io/library/ubuntu:24.04 \
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1
echo "[bootstrap] ✅ docker + GPU 就绪"
