#!/bin/bash
# Real vLLM + pping-lang integration test in WSL.
# Run from inside WSL: bash /mnt/d/GitCode/pping-lang/examples/embedded/run_real_vllm.sh

set -e

VENV=/tmp/vllm-venv
PROJECT=/mnt/d/GitCode/pping-lang
# Prefer locally cached modelscope path (avoids HF download dance); fall back to HF id
LOCAL_QWEN=$HOME/.cache/modelscope/hub/models/Qwen/Qwen2___5-0___5B-Instruct
if [ -d "$LOCAL_QWEN" ]; then
    MODEL=$LOCAL_QWEN
else
    MODEL=Qwen/Qwen2.5-0.5B-Instruct
fi
SERVED_NAME=Qwen/Qwen2.5-0.5B-Instruct   # so client requests by HF id still work

# Use HF mirror for China network (5-10x faster); fall back to offline if cached
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_ENABLE_HF_TRANSFER=0

# pping-lang env (override defaults for visibility)
export PPING_LANG_DB_PATH=/tmp/pping-real.duckdb
export PPING_LANG_INSTANCE_ID=real-vllm-test
export PPING_LANG_FLUSH_INTERVAL_S=1.0
export PPING_LANG_RULE_EVAL_INTERVAL_S=1.0
export PPING_LANG_API_HOST=0.0.0.0
export PPING_LANG_API_PORT=8765

# Clean prior run
rm -f "$PPING_LANG_DB_PATH"

echo "=== Install pping-lang from source ==="
$VENV/bin/pip install -e "$PROJECT" --quiet 2>&1 | tail -3

echo "=== Verify entry point registered ==="
$VENV/bin/python -c "
import importlib.metadata
eps = importlib.metadata.entry_points(group='vllm.stat_logger_plugins')
names = [ep.name for ep in eps]
print(f'Registered: {names}')
assert 'pping_lang' in names, 'pping_lang plugin NOT registered'
"

echo "=== Start vllm serve (downloads model on first run) ==="
echo "Model: $MODEL"
echo "vLLM API: http://localhost:8000  |  pping-lang dashboard: http://localhost:8765"
echo

# vLLM args:
#  --gpu-memory-utilization 0.85: leave headroom on 8GB laptop
#  --max-model-len 4096: small KV cache, sufficient for tests
#  --max-num-seqs 32: small batch
# NOTE: --enable-cudagraph-metrics is a 0.20+ flag; 0.13.0 emits padding stats
# unconditionally so we drop it here.
exec $VENV/bin/vllm serve "$MODEL" \
    --served-model-name "$SERVED_NAME" \
    --gpu-memory-utilization 0.85 \
    --max-model-len 4096 \
    --max-num-seqs 32 \
    --port 8000
