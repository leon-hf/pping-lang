"""端到端 demo — 不需要真 vLLM/GPU，喂合成 stats 触发 marquee 诊断规则。

跑法:
    python examples/embedded/demo.py

预期输出（约 8 秒后开始出现）：
    [pping-lang] [!] WARNING: CUDA graph padding 过高
      CUDA graph padding 比例 70%, 约 70% 的 GPU 算力浪费在补 0
      -> 调小 max_num_seqs 或开启更细粒度的 cudagraph capture（PIECEWISE 模式）

    [pping-lang] [!] WARNING: MFU 偏低（计算资源浪费）
      MFU = 5% < 20%（理论峰值的小部分都没跑到）
      -> 检查 padding ratio / batch 大小 / dtype（应为 bf16/fp16）

完成后查询 DuckDB 看完整数据:
    duckdb ./demo.duckdb -c "SELECT rule_id, severity, message FROM diagnoses;"
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

# Demo 配置：缩短 flush / eval 间隔便于即时看到效果
DEMO_DB = Path(tempfile.gettempdir()) / "pping-lang-demo.duckdb"
if DEMO_DB.exists():
    DEMO_DB.unlink()
os.environ["PPING_LANG_DB_PATH"] = str(DEMO_DB)
os.environ["PPING_LANG_INSTANCE_ID"] = "demo"
os.environ["PPING_LANG_FLUSH_INTERVAL_S"] = "1.0"      # default 5s → 1s
os.environ["PPING_LANG_RULE_EVAL_INTERVAL_S"] = "1.0"
os.environ["PPING_LANG_DISABLE_NVML"] = "1"            # demo 跑在无 GPU 机器上

from pping_lang.hardware import GPUPeak  # noqa: E402
from pping_lang.plugin import PpingLangStatLogger  # noqa: E402

DEMO_DURATION_S = int(os.environ.get("DEMO_DURATION_S", "12"))

# H100 SXM peak — 用于 MFU 派生
DEMO_GPU_PEAK = GPUPeak(bf16_tflops=989.0, mem_bw_gbs=3350.0)


def make_unhealthy_scheduler_stats(step: int) -> SimpleNamespace:
    """合成 SchedulerStats: 高 padding (~70%) + 低 MFU (~5%)。"""
    # cudagraph: 1000 padded tokens, 300 unpadded → 70% padding ratio
    cudagraph = SimpleNamespace(
        num_unpadded_tokens=300,
        num_padded_tokens=1000,
        num_paddings=10,
        runtime_mode="PIECEWISE",
    )
    # perf: ~5% MFU 假设 step 间隔 ~100ms
    #   target: flops / (peak_flops * dt) ≈ 0.05
    #   peak_flops = 989e12, dt = 0.1s → flops_per_step = 0.05 * 989e12 * 0.1 ≈ 4.9e12
    perf = SimpleNamespace(
        num_flops_per_gpu=int(4.9e12),
        num_read_bytes_per_gpu=int(0.5 * 3350e9 * 0.1),  # 50% bw util
        num_write_bytes_per_gpu=0,
        debug_stats=None,
    )
    return SimpleNamespace(
        num_running_reqs=8,
        num_waiting_reqs=2,
        num_skipped_waiting_reqs=0,
        current_wave=step // 10,
        kv_cache_usage=0.45,
        prefix_cache_stats=SimpleNamespace(queries=100, hits=15),
        kv_cache_eviction_events=[],
        spec_decoding_stats=None,
        cudagraph_stats=cudagraph,
        perf_stats=perf,
        waiting_lora_adapters={},
        running_lora_adapters={},
    )


def make_iteration_stats(step: int) -> SimpleNamespace:
    return SimpleNamespace(
        num_generation_tokens=50 + step % 10,
        prompt_token_stats=SimpleNamespace(
            total=200, local_cache_hit=30, external_kv_transfer=0,
        ),
        num_preempted_reqs=0,
        num_corrupted_reqs=0,
        time_to_first_tokens_iter=[0.15, 0.18],
        inter_token_latencies_iter=[0.025, 0.026],
        finished_requests=[],
    )


def main() -> int:
    print("=" * 70)
    print("pping-lang demo — synthetic vLLM stats with high padding + low MFU")
    print("=" * 70)
    print(f"DB:       {DEMO_DB}")
    print(f"Duration: {DEMO_DURATION_S}s, step every 100ms")
    print("Expecting marquee rules to fire after ~6-8s of accumulated data...\n")

    plugin = PpingLangStatLogger(vllm_config=None, engine_index=0)
    plugin.log_engine_initialized()

    # Inject demo GPU peak for MFU derivation (since NVML disabled)
    if plugin._collector is not None:
        plugin._collector._gpu_peak = DEMO_GPU_PEAK

    start = time.monotonic()
    step = 0
    try:
        while time.monotonic() - start < DEMO_DURATION_S:
            plugin.record(
                make_unhealthy_scheduler_stats(step),
                make_iteration_stats(step),
            )
            time.sleep(0.1)
            step += 1
    finally:
        # Force-flush any pending data, then stop bg threads
        if plugin._sink is not None:
            plugin._sink._drain()
        if plugin._rule_engine is not None:
            plugin._rule_engine.stop()
        if plugin._sink is not None:
            plugin._sink.close()

    print(f"\n{'=' * 70}")
    print(f"Demo done. {step} synthetic steps pushed in {DEMO_DURATION_S}s.")
    if plugin._rule_engine is not None:
        print(f"Rule eval ran {plugin._rule_engine.eval_count} times, "
              f"fired {plugin._rule_engine.fire_count} times.")
    print(f"Inspect data: duckdb {DEMO_DB} -c 'SELECT * FROM diagnoses;'")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
