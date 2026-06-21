#!/usr/bin/env bash
# 停掉 runw 上的部署,释放 GPU。
#   bash deploy/runw/stop.sh             # 停 docker 路容器 pvllm
#   MODE=k8s bash deploy/runw/stop.sh    # 停 k3s 路 Deployment(scale 0,留 Service/Ingress)
set -u
RUNW=${RUNW:-runw}
MODE=${MODE:-docker}
CONTAINER=${CONTAINER:-pvllm}
if [ "$MODE" = "k8s" ]; then
  ssh "$RUNW" "sudo k3s kubectl scale deploy/pping-lang -n default --replicas=0 2>/dev/null \
    && echo '[runw] k8s pping-lang 已 scale 0,GPU 已释放(Service/Ingress 保留)' \
    || echo '[runw] k8s pping-lang 未部署'"
else
  ssh "$RUNW" "sudo docker stop '$CONTAINER' >/dev/null 2>&1 \
    && echo '[runw] $CONTAINER 已停,GPU 已释放' || echo '[runw] $CONTAINER 未在运行'"
fi
