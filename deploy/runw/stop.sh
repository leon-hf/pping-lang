#!/usr/bin/env bash
# 停掉 runw 上的 dashboard,释放 GPU(容器保留,下次 deploy 直接复用/重建)。
set -u
RUNW=${RUNW:-runw}
ssh "$RUNW" 'docker stop pdash >/dev/null 2>&1 && echo "[runw] pdash 已停,GPU 已释放" || echo "[runw] pdash 未在运行"'
