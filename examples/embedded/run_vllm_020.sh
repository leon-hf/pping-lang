#!/bin/bash
# Real vLLM 0.20 + pping-lang. Mirrors run_real_vllm.sh but pointed at a
# /tmp/vllm020-venv. Used to validate perf_stats codepaths (MFU / measured
# Roofline / padding_ratio) that 0.13 cannot reach.
#
# Driver requirement:
#   vllm 0.20.0 hard-pins torch==2.11.0, which on PyPI is only built against
#   CUDA 13. Host NVIDIA driver must support CUDA 13 → version >= 580.
#   Older drivers fail at init with:
#     RuntimeError: The NVIDIA driver on your system is too old (found
#     version 12070)
#   Check with `nvidia-smi --query-gpu=driver_version --format=csv`.
#
# First-time setup (once driver is new enough):
#   python3 -m venv /tmp/vllm020-venv
#   /tmp/vllm020-venv/bin/pip install vllm==0.20.0 \
#       -i https://pypi.tuna.tsinghua.edu.cn/simple
#   /tmp/vllm020-venv/bin/pip install -e /mnt/d/GitCode/pping-lang \
#       -i https://pypi.tuna.tsinghua.edu.cn/simple

set -e

VENV=/tmp/vllm020-venv
PROJECT=/mnt/d/GitCode/pping-lang
LOCAL_QWEN=$HOME/.cache/modelscope/hub/models/Qwen/Qwen2___5-0___5B-Instruct
if [ -d "$LOCAL_QWEN" ]; then
    MODEL=$LOCAL_QWEN
else
    MODEL=Qwen/Qwen2.5-0.5B-Instruct
fi
SERVED_NAME=Qwen/Qwen2.5-0.5B-Instruct

export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_ENABLE_HF_TRANSFER=0

# pping-lang knobs
export PPING_LANG_DB_PATH=/tmp/pping-real-020.duckdb
export PPING_LANG_INSTANCE_ID=real-vllm-020
export PPING_LANG_FLUSH_INTERVAL_S=1.0
export PPING_LANG_RULE_EVAL_INTERVAL_S=1.0
export PPING_LANG_API_HOST=0.0.0.0
export PPING_LANG_API_PORT=8765

rm -f "$PPING_LANG_DB_PATH" "${PPING_LANG_DB_PATH}.wal"

echo "=== vllm 0.20.0 + pping-lang ==="
echo "Model: $MODEL"
echo "API: http://localhost:8000   dashboard: http://localhost:8765"
echo

# vLLM 0.20 args. NOTE: --enable-cudagraph-metrics is NOT a real flag in
# 0.20.0 (we keep trying it for nostalgia); cudagraph stats appear to be
# emitted unconditionally now. Re-test if a future release adds an opt-in.
exec $VENV/bin/vllm serve "$MODEL" \
    --served-model-name "$SERVED_NAME" \
    --gpu-memory-utilization 0.85 \
    --max-model-len 4096 \
    --max-num-seqs 32 \
    --port 8000
