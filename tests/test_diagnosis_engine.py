"""4 瓶颈诊断求值核单测 —— 合成 metric_fn 验逻辑(复合 all / any / 配置阈值 / 署名)。"""
from __future__ import annotations

import dataclasses

from pping_lang.rules.diagnosis_config import default_config
from pping_lang.rules.diagnosis_engine import evaluate


def _fn(values: dict[tuple[str, str], float]):
    """key = (metric, aggregation);其余返回 None(无数据)。"""
    def fn(metric: str, window_s: int, agg: str):
        return values.get((metric, agg))
    return fn


CFG = default_config("custom")  # mfu_low 0.2 / mfu_high 0.5 / mbu_low 50 / mbu_high 85 / kv 0.9


def _ids(findings):
    return {f.rule_id for f in findings}


def test_A_underfed_with_work():
    # 有请求在跑(2>0.5)、且双低(mfu<0.2、HBM 繁忙<50%)→ A
    f = _fn({("vllm.scheduler.running_reqs", "avg"): 2.0,
             ("vllm.perf.mfu_ratio", "avg"): 0.05,
             ("gpu.mem_util_pct", "avg"): 30.0})
    assert "A" in _ids(evaluate(f, CFG))


def test_A_idle_guard_no_running_reqs():
    # 双低但没请求在跑(空载)→ A 不报(空载守卫挡掉误报)
    f = _fn({("vllm.perf.mfu_ratio", "avg"): 0.05, ("gpu.mem_util_pct", "avg"): 30.0})
    assert "A" not in _ids(evaluate(f, CFG))


def test_A_needs_both_roofs_low_match_all():
    # 有活、mfu 低但 HBM 繁忙不低 → A 不触发(all);HBM 90% 反而触 B
    f = _fn({("vllm.scheduler.running_reqs", "avg"): 2.0,
             ("vllm.perf.mfu_ratio", "avg"): 0.05,
             ("gpu.mem_util_pct", "avg"): 90.0})
    ids = _ids(evaluate(f, CFG))
    assert "A" not in ids and "B" in ids


def test_B_bandwidth_wall_hbm_busy():
    # NVML HBM 控制器繁忙 90% > 85% → B
    assert "B" in _ids(evaluate(_fn({("gpu.mem_util_pct", "avg"): 90.0}), CFG))


def test_B_bandwidth_wall_kernel_stall():
    # MBU 没给,但内核 memory_throttle 占 stall 40% > 25% → B(any-match 内核佐证)
    assert "B" in _ids(evaluate(_fn({("kernel.stall.memory_throttle_pct", "avg"): 40.0}), CFG))


def test_C_compute_wall_measured_mfu():
    assert "C" in _ids(evaluate(_fn({("vllm.perf.mfu_ratio", "avg"): 0.7}), CFG))


def test_C_compute_wall_kernel_stall():
    # MFU 没给,但内核 math_pipe 占 stall 40% > 25% → C(any-match 内核佐证)
    assert "C" in _ids(evaluate(_fn({("kernel.stall.math_pipe_pct", "avg"): 40.0}), CFG))


def test_D_any_match_kv_or_preempt():
    # kv 没到阈值,但 preempt>0 → D 触发(any)
    fire = _fn({
        ("vllm.scheduler.kv_cache_usage_ratio", "avg"): 0.5,
        ("vllm.iter.preempted_reqs", "sum"): 2.0,
    })
    assert "D" in _ids(evaluate(fire, CFG))
    # 都不满足 → 不触发
    quiet = _fn({
        ("vllm.scheduler.kv_cache_usage_ratio", "avg"): 0.5,
        ("vllm.iter.preempted_reqs", "sum"): 0.0,
    })
    assert "D" not in _ids(evaluate(quiet, CFG))


def test_threshold_comes_from_config():
    cfg = dataclasses.replace(CFG, mbu_high_pct=95.0)
    f = _fn({("gpu.mem_util_pct", "avg"): 90.0})
    assert "B" not in _ids(evaluate(f, cfg))   # 90 < 95 → 不触发


def test_finding_carries_signed_inference():
    fs = evaluate(_fn({("vllm.perf.mfu_ratio", "avg"): 0.7}), CFG)
    c = next(x for x in fs if x.rule_id == "C")
    assert c.hypothesis and c.suggestion
    assert c.name and c.severity == "warning"


def test_no_data_no_fire():
    assert evaluate(_fn({}), CFG) == []
