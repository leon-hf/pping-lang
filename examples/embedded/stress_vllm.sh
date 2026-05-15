#!/bin/bash
# Send concurrent inference requests to drive metrics + trigger diagnoses.
# Run AFTER vllm serve is up (waits for /v1/models to respond).

set -e

API=http://localhost:8000
DASHBOARD=http://localhost:8765
N_REQUESTS=${1:-30}      # total requests
CONCURRENCY=${2:-4}      # parallel curl workers

echo "Waiting for vLLM API at $API ..."
until curl -s -o /dev/null -w "%{http_code}" "$API/v1/models" | grep -q 200; do sleep 1; done
echo "vLLM ready."

# Get model name
MODEL=$(curl -s $API/v1/models | python3 -c "import json, sys; print(json.load(sys.stdin)['data'][0]['id'])")
echo "Model: $MODEL"

mkdir -p /tmp/pping-stress
ID=0
for batch_start in $(seq 0 $CONCURRENCY $((N_REQUESTS - 1))); do
    pids=()
    for offset in $(seq 0 $((CONCURRENCY - 1))); do
        i=$((batch_start + offset))
        [ $i -ge $N_REQUESTS ] && break
        ID=$((ID + 1))
        (
            curl -s -X POST "$API/v1/chat/completions" \
                -H "Content-Type: application/json" \
                -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Count from 1 to ${i}0. Reply concisely.\"}],\"max_tokens\":80}" \
                > /tmp/pping-stress/resp-$ID.json 2>&1 &
        )
        pids+=($!)
    done
    wait "${pids[@]}" 2>/dev/null || true
    printf "  batch done (req $ID/$N_REQUESTS)\n"
done

echo
echo "=== Done. Inspect dashboard: $DASHBOARD ==="
echo "Quick stats:"
curl -s $DASHBOARD/api/health | python3 -m json.tool
echo
echo "Recent diagnoses:"
curl -s "$DASHBOARD/api/diagnoses?seconds=300" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f'  count: {len(data[\"diagnoses\"])}')
seen = set()
for d in data['diagnoses']:
    if d['rule_id'] in seen: continue
    seen.add(d['rule_id'])
    print(f'  {d[\"severity\"]:>8}  {d[\"rule_id\"]:<28}  {d[\"message\"][:80]}')
"
