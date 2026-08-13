#!/usr/bin/env python3
"""#4 标定负载驱动:在 runw 侧车容器里由 ncu 包住运行,产生 decode 内核流。

用法(由 deploy/runw/calibrate_l2.sh 编排,不直接手跑):
    ncu ... python3 scripts/calibrate_l2/bench_decode.py --model <hf型号>

设计:
- enforce_eager=True:不建 CUDA graph,让每个 kernel 单独 launch,ncu 才能按
  launch 采。kernel 选择(marlin/cutlass/gemv)与 graph 模式一致,L2/DRAM 行为
  不受 eager 影响(kernel replay 会隔离前后 kernel 的缓存状态)。
- 先 warmup(被 ncu --launch-skip 跳过),再单请求长 decode(M=1,生产 decode 口径)。
- pvllm 必须先停:单卡 16GB,标定 vLLM 要独占 GPU;这也是为什么这是一次性维护操作。
"""
from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--max-model-len", type=int, default=2048)
    args = ap.parse_args()

    from vllm import LLM, SamplingParams  # noqa: PLC0415 — 容器内才有 vllm

    llm = LLM(model=args.model, enforce_eager=True,
              max_model_len=args.max_model_len,
              gpu_memory_utilization=0.85, disable_log_stats=True)
    # warmup:驱动 JIT/lazy init,这些 launch 由 ncu --launch-skip 跳过
    llm.generate(["hello"], SamplingParams(temperature=0, max_tokens=32))
    # 被采段:单请求 decode,M=1 —— 与生产 decode 同口径
    llm.generate(["请写一段关于秋天的散文,不少于两百字。"],
                 SamplingParams(temperature=0, max_tokens=args.max_tokens))
    print("[calib] bench done", flush=True)


if __name__ == "__main__":
    main()
