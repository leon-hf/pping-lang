"""诊断求值核单测 —— 用合成 metric_fn 验逻辑(前置/复合/比值/regime/配置阈值)。"""
from __future__ import annotations

import dataclasses

from pping_lang.rules.diagnosis_config import default_config
from pping_lang.rules.diagnosis_engine import evaluate


def _fn(values: dict[tuple[str, str], float]):
    """key = (metric, aggregation);其余返回 None(无数据)。"""
    def fn(metric: str, window_s: int, agg: str):
        return values.get((metric, agg))
    return fn


CFG = default_config("custom")  # ttft 2000 / tpot 50 / waiting 50 / mfu 0.2 / mbu_high 85 / mbu_low 50 / tail 5


def _ids(findings):
    return {f.rule_id for f in findings}


def test_symptom_fires_on_threshold():
    hot = _fn({("vllm.req.ttft_ms", "p99"): 3000.0})
    assert "S1" in _ids(evaluate(hot, CFG))
    cold = _fn({("vllm.req.ttft_ms", "p99"): 1000.0})
    assert "S1" not in _ids(evaluate(cold, CFG))


def test_precondition_gates_discriminator():
    # D1a 的 check 成立(prompt 长),但 S1/S5 都不成立 → D1a 不触发(前置守卫)
    only_prompt = _fn({("vllm.req.prompt_tokens", "avg"): 4000.0})
    assert "D1a" not in _ids(evaluate(only_prompt, CFG))
    # S1 也成立 → D1a 触发
    with_s1 = _fn({
        ("vllm.req.ttft_ms", "p99"): 3000.0,
        ("vllm.req.prompt_tokens", "avg"): 4000.0,
    })
    fired = _ids(evaluate(with_s1, CFG))
    assert {"S1", "D1a"} <= fired


def test_match_any_vs_all():
    # S4 = any:kv 低但 preempt>0 → S4 触发;D4a = all:kv 没到阈值 → 不触发
    f = _fn({
        ("vllm.scheduler.kv_cache_usage_ratio", "avg"): 0.5,
        ("vllm.iter.preempted_reqs", "sum"): 2.0,
    })
    got = _ids(evaluate(f, CFG))
    assert "S4" in got
    assert "D4a" not in got


def test_match_all_d3a_both_low():
    both_low = _fn({
        ("vllm.perf.mfu_ratio", "avg"): 0.1,
        ("gpu.mem_util_pct", "avg"): 30.0,
    })
    assert "D3a" in _ids(evaluate(both_low, CFG))
    # MBU 不低 → 不是双低 → 不触发
    one_low = _fn({
        ("vllm.perf.mfu_ratio", "avg"): 0.1,
        ("gpu.mem_util_pct", "avg"): 70.0,
    })
    assert "D3a" not in _ids(evaluate(one_low, CFG))


def test_p99_over_p50_ratio_s5():
    # 绝对值不高(p99 300 < 2000,S1 不触发),但 p99/p50 = 6 > 5 → S5 触发
    f = _fn({
        ("vllm.req.ttft_ms", "p99"): 300.0,
        ("vllm.req.ttft_ms", "p50"): 50.0,
    })
    got = _ids(evaluate(f, CFG))
    assert "S5" in got
    assert "S1" not in got


def test_regime_gates_d1c():
    f = _fn({
        ("vllm.req.ttft_ms", "p99"): 3000.0,   # S1
        ("vllm.perf.mfu_ratio", "avg"): 0.1,    # MFU 低
    })
    # regime 未知 → D1c 不触发(requires compute_bound)
    assert "D1c" not in _ids(evaluate(f, CFG, regime=None))
    # 计算受限 → 触发
    assert "D1c" in _ids(evaluate(f, CFG, regime="compute_bound"))
    # 访存受限 → 不触发
    assert "D1c" not in _ids(evaluate(f, CFG, regime="memory_bound"))


def test_threshold_comes_from_config():
    f = _fn({
        ("vllm.req.ttft_ms", "p99"): 3000.0,           # S1 hold
        ("vllm.scheduler.waiting_reqs", "avg"): 60.0,   # D1b
    })
    assert "D1b" in _ids(evaluate(f, CFG))               # 60 > 50(默认)
    strict = dataclasses.replace(CFG, waiting_reqs=100.0)
    assert "D1b" not in _ids(evaluate(f, strict))        # 60 > 100 假 → 不触发


def test_missing_metric_no_fire():
    assert evaluate(_fn({}), CFG) == []   # 全无数据 → 啥都不报(优雅)


def test_db_path_realistic_scenario(tmp_path):
    """端到端:真 DuckDB(LocalSink 灌真实场景点)→ db_metric_fn(真 SQL 聚合)→ evaluate。

    场景 = 过载服务器:长尾 TTFT + 长 prompt + 队列堆积 + KV 满并抢占;
    MFU/MBU 无数据(v0.21 perf_stats 缺)→ 相关规则优雅不报、不乱报。
    """
    import time

    import duckdb

    from pping_lang.metrics_catalog import M
    from pping_lang.rules.diagnosis_engine import db_metric_fn
    from pping_lang.sink.local import LocalSink
    from pping_lang.types import MetricPoint

    db = tmp_path / "diag.duckdb"
    sink = LocalSink(db_path=db, instance_id="t", flush_interval_s=10.0)
    base = time.monotonic_ns()

    def push(name, vals):
        for i, v in enumerate(vals):
            sink.push_metric(MetricPoint(ts_ns=base + i, name=name, value=float(v)))

    push(M.VLLM_REQ_TTFT_MS, [80] * 40 + [900, 950, 1000, 1100])   # p50~80 p99~1050 → ratio>5
    push(M.VLLM_REQ_PROMPT_TOKENS, [3000] * 20)                     # avg 3000 > 2048
    push(M.VLLM_SCHEDULER_WAITING_REQS, [60] * 10)                  # avg 60 > 50
    push(M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO, [0.95] * 10)        # >= 0.9
    push(M.VLLM_ITER_PREEMPTED_REQS, [1, 2, 1] + [0] * 7)           # sum 4 > 0
    sink.close()

    conn = duckdb.connect(str(db))
    now = base + 5 * 10**9   # 5s 后:所有点都在窗口内
    fn = db_metric_fn(conn, now)

    # 默认 SLA(ttft 2000):S1 不触发(p99~1050<2000),尾部/KV/长输入/队列仍触发(前置 S5)
    default_ids = {f.rule_id for f in evaluate(fn, default_config("custom"))}
    assert "S1" not in default_ids
    assert {"S4", "S5", "D1a", "D1b", "D4a"} <= default_ids

    # 严格 SLA(code: ttft 100):S1 触发
    strict_ids = {f.rule_id for f in evaluate(fn, default_config("code"))}
    assert {"S1", "S4", "S5", "D1a", "D1b", "D4a"} <= strict_ids
    # MFU/MBU 无数据 → 优雅不报
    assert {"D1c", "D3a", "D2a"} & strict_ids == set()
    conn.close()


def test_finding_carries_signed_inference():
    f = _fn({
        ("vllm.req.ttft_ms", "p99"): 3000.0,
        ("vllm.req.prompt_tokens", "avg"): 4000.0,
    })
    d1a = next(x for x in evaluate(f, CFG) if x.rule_id == "D1a")
    assert d1a.hypothesis and d1a.suggestion     # 根因/处方署名俱在
    assert d1a.name == "平均 prompt 偏长"          # 名字是事实
    assert d1a.values  # 触发时带实测值
