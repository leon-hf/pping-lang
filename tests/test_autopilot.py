"""Autopilot M0 决策核 + sim 闭环单测(无 GPU、无网络)。"""
from __future__ import annotations

import json

import pytest

from pping_lang.autopilot.action_space import (
    action_space_stats,
    bottleneck_label,
    knobs_helping,
    propose_candidates,
    render_command,
)
from pping_lang.autopilot.agent import StubAgent
from pping_lang.autopilot.api import AutopilotController, build_objective
from pping_lang.autopilot.objective import (
    SLA,
    ObjectiveSpec,
    Scorecard,
    decide,
    objective_score,
    sla_ok,
)
from pping_lang.autopilot.runner import Runner, diagnose
from pping_lang.autopilot.sandbox import BENCH_SPEC, LaunchError, SimSandbox
from pping_lang.autopilot.session_store import Round, SessionStore

OBJ = ObjectiveSpec(target="throughput", sla=SLA(ttft_p99_ms=1000.0))


# ---- objective ----

def test_sla_ok_and_score():
    good = Scorecard(output_tps=2000, ttft_p99_ms=500, tpot_p99_ms=20)
    bad = Scorecard(output_tps=2000, ttft_p99_ms=1500, tpot_p99_ms=20)   # 破 TTFT SLA
    assert sla_ok(good, OBJ) and not sla_ok(bad, OBJ)
    assert objective_score(good, OBJ) == 2000
    assert objective_score(bad, OBJ) == float("-inf")


def test_sla_ok_e2e_is_monitoring_only_not_a_gate():
    """E2E p99 不进闸门,只监控上报,不影响 sla_ok/objective_score(2026-07-23)。

    E2E ≈ TTFT + TPOT×输出 token 数,后者是负载形态的固定属性,不受 vLLM 参数影响——
    真机复现:7B-AWQ 上 chat/code 形态的默认 E2E 阈值比基线还紧,基线一开局就 -inf,
    后续候选无论怎么改都过不了这道硬门槛,session 全程"无改进",但 TTFT/TPOT 实际都在
    变好,收益被硬门槛整个盖住。只要 TTFT/TPOT 仍达标,E2E 超再多也不该判负。"""
    o = ObjectiveSpec(target="throughput", sla=SLA(e2e_p99_ms=5000.0))
    within = Scorecard(output_tps=2000, ttft_p99_ms=500, tpot_p99_ms=20, e2e_p99_ms=4000)
    over = Scorecard(output_tps=2000, ttft_p99_ms=500, tpot_p99_ms=20, e2e_p99_ms=6000)
    assert sla_ok(within, o) and sla_ok(over, o)
    assert objective_score(over, o) == 2000


def test_score_high_error_rejected():
    sc = Scorecard(output_tps=9999, ttft_p99_ms=100, error_rate=0.6)
    assert objective_score(sc, OBJ) == float("-inf")


def test_decide_kept_revert_tie():
    assert decide(2000, 1240, 0.03) == "kept"            # 超噪声边界
    assert decide(float("-inf"), 1240) == "reverted"     # 破 SLA
    assert decide(2000, float("-inf")) == "kept"         # 基线不达标,首个可行候选
    assert decide(1250, 1240, 0.03) == "tie"             # 噪声内


def test_decide_worse_than_noise_margin_is_reverted_not_tie():
    """真机复现(2026-07-16 dogfood)：候选 1682.9 vs best 1784.1(-5.7%,远超 3% 噪声边界)
    被判成了 tie(UI 显示"≈持平")——decide() 曾经只挡"赢得不够多"的上界,没挡"输得
    不是噪声"的下界,任何没到 -inf 的退化候选都会落进 tie,把真实退化说成没变化。"""
    assert decide(1682.9, 1784.1, 0.03) == "reverted"
    assert decide(1240 * 0.9, 1240, 0.03) == "reverted"  # -10%,远超噪声边界
    assert decide(1240 * 0.98, 1240, 0.03) == "tie"      # -2%,仍在噪声边界内


def test_latency_target_minimizes():
    o = ObjectiveSpec(target="latency", latency_metric="tpot", sla=SLA())
    fast = Scorecard(output_tps=100, ttft_p99_ms=300, tpot_p99_ms=15)
    slow = Scorecard(output_tps=100, ttft_p99_ms=300, tpot_p99_ms=40)
    assert objective_score(fast, o) > objective_score(slow, o)   # 延迟越低分越高


def test_aggregate_scorecards_records_repeat_stats():
    from pping_lang.autopilot.repeat import aggregate_scorecards
    samples = [
        Scorecard(output_tps=100, ttft_p99_ms=10, tpot_p99_ms=2, e2e_p99_ms=20),
        Scorecard(output_tps=120, ttft_p99_ms=20, tpot_p99_ms=4, e2e_p99_ms=30),
        Scorecard(output_tps=110, ttft_p99_ms=30, tpot_p99_ms=6, e2e_p99_ms=40),
    ]
    sc = aggregate_scorecards(samples)
    assert sc.output_tps == 110.0
    assert sc.ttft_p99_ms == 20
    assert sc.tpot_p99_ms == 4.0
    assert sc.run_meta["bench_repeats"] == 3
    assert sc.run_meta["repeat_stats"]["output_tps"]["median"] == 110
    assert len(sc.run_meta["repeat_samples"]) == 3


def test_config_review_flags_risks():
    from pping_lang.autopilot.config_review import review_config_diff
    r = review_config_diff(
        {"max_num_seqs": 32, "gpu_memory_utilization": 0.7},
        {"max_num_seqs": 64, "gpu_memory_utilization": 0.9, "kv_cache_dtype": "fp8"})
    by_key = {c["key"]: c for c in r["changes"]}
    assert by_key["max_num_seqs"]["flag"] == "--max-num-seqs"
    assert any("KV pressure" in n for n in by_key["max_num_seqs"]["risk_notes"])
    assert r["requires_quality_gate"] is True


def test_promote_package_is_manual_with_rollback():
    from pping_lang.autopilot.config_review import review_config_diff
    from pping_lang.autopilot.promote import build_promote_package
    baseline = {"max_num_seqs": 32, "gpu_memory_utilization": 0.7}
    best = {"max_num_seqs": 64, "gpu_memory_utilization": 0.7}
    pkg = build_promote_package(
        model="M",
        baseline_config=baseline,
        best_config=best,
        applies_to={"model": "M", "gpu": "sim-GPU"},
        config_review=review_config_diff(baseline, best),
    )
    assert pkg["state"] == "ready"
    assert pkg["manual_only"] is True and pkg["applied"] is False
    assert pkg["requires_confirmation"] is True
    assert pkg["production_command"] == "vllm serve M --max-num-seqs 64 --gpu-memory-utilization 0.7"
    assert pkg["rollback_command"] == "vllm serve M --max-num-seqs 32 --gpu-memory-utilization 0.7"
    assert any("KV pressure" in n for n in pkg["risk_notes"])


# ---- action_space ----

def test_propose_for_underfed_raises_concurrency():
    cands = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    knobs = {c["knob"]: c for c in cands}
    assert "max_num_seqs" in knobs and knobs["max_num_seqs"]["to"] == 64   # 翻倍
    assert "gpu_memory_utilization" not in knobs                          # 对 A 无用,不提


def test_propose_excludes_hurting_knob():
    # 容量瓶颈 D:max_num_seqs 伤 D(hurts),不能"提并发";给"降并发"或"扩 KV 池"
    cands = propose_candidates("D", {"max_num_seqs": 128, "gpu_memory_utilization": 0.70})
    knobs = {c["knob"]: c for c in cands}
    assert knobs["max_num_seqs"]["to"] == 64           # 降并发缓解 KV
    assert "降并发" in knobs["max_num_seqs"]["lever"]
    assert "gpu_memory_utilization" in knobs           # 扩 KV 池利于 D


def test_render_command():
    cmd = render_command("M", {"max_num_seqs": 128, "gpu_memory_utilization": 0.9})
    assert cmd == "vllm serve M --max-num-seqs 128 --gpu-memory-utilization 0.9"


def test_propose_skips_default_on_knobs():
    # 分类(一)默认已开(chunked-prefill/async-sched/...)→ 提议时不出现(§4.4,别开已开的开关)
    cands = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    keys = {c["knob"] for c in cands}
    assert "enable_chunked_prefill" not in keys and "async_scheduling" not in keys


def test_bottleneck_label_gives_context_not_bare_letter():
    """用户反馈(2026-07-21,分两轮):① "诊断命中 B"/"B:live" 这种裸字母没上下文,不知道
    B 是什么,"墙"的比喻也要换成"瓶颈";② 后续追加——连"(B)"这种带字母的括注也别留,
    字母本身不该出现在任何用户可见文本里,一律换成具体人话。"""
    assert bottleneck_label("A") == "双低"
    assert bottleneck_label("B") == "带宽瓶颈"
    assert bottleneck_label("C") == "算力瓶颈"
    assert bottleneck_label("D") == "容量瓶颈"
    assert bottleneck_label(None) == "症状/其它"
    assert bottleneck_label("Z") == "症状/其它"


def test_action_space_stats_categorizes_all_knobs():
    """用户反馈(2026-07-22)：每次 session 只调 2-3 个参数,看不出剩下的参数是"不该调"
    还是"没顾上调"。action_space_stats() 给出全量分类计数,四类互斥且总数对得上。"""
    s = action_space_stats()
    assert s["total"] == (s["default_on_count"] + s["unsupported_count"]
                           + s["precision_excluded_count"] + s["considerable_count"])
    assert s["considerable_count"] == len(s["considerable_knobs"])
    assert "max_num_seqs" in s["considerable_knobs"]           # T1 常规杠杆
    assert "quantization" not in s["considerable_knobs"]       # T2 降精度,已排除
    assert "num_scheduler_steps" not in s["considerable_knobs"]  # 当前 vLLM build 不支持


def test_knobs_helping_includes_bd_coupling():
    """knobs_helping 要跟 propose_candidates 的 B↔D 共生逻辑(§4.3)对齐,否则耦合参数
    (如 cpu_offload_gb)被试过却不在"相关参数"列表里,总结文案会自相矛盾。"""
    helping_b = knobs_helping("B")
    assert "gpu_memory_utilization" in helping_b      # 直接对症 B
    assert "cpu_offload_gb" in helping_b              # D 缓解类,因 B↔D 耦合并入
    assert "quantization" not in helping_b            # T2,candidate 池本就排除
    helping_c = knobs_helping("C")
    assert "cpu_offload_gb" not in helping_c          # C 不耦合 D,不应该混进来


def test_propose_t2_gated_by_quality_gate():
    # 质量门关(默认)→ 只 T1;开 → 放 T2(kv_cache_dtype 等),给 B 瓶颈
    cfg = {"max_num_seqs": 128, "gpu_memory_utilization": 0.92}
    off = {c["knob"] for c in propose_candidates("B", cfg, quality_gate=False)}
    on = {c["knob"] for c in propose_candidates("B", cfg, quality_gate=True)}
    assert "kv_cache_dtype" not in off and "kv_cache_dtype" in on


def test_propose_load_limited_still_has_scheduler_and_prefill_knobs():
    """load_binding=False 时 max_num_seqs 被剪;prefill_chunk_size / num_scheduler_steps /
    max_seq_len_to_capture 均已标 unsupported(vLLM 0.21 无此 flag,真机 LaunchError 复现),
    不应出现在候选集。"""
    cfg = {"max_num_seqs": 64, "gpu_memory_utilization": 0.9,
           "prefill_chunk_size": 512, "max_seq_len_to_capture": 8192}
    cands = propose_candidates("A", cfg, load_binding=False)
    keys = {c["knob"] for c in cands}
    assert "max_num_seqs" not in keys
    assert "prefill_chunk_size" not in keys
    assert "max_seq_len_to_capture" not in keys


def test_propose_quality_gate_opens_mtp_and_speculative_knobs():
    """B 瓶颈 + quality_gate 应放出 num_lookahead_slots / ngram_prompt_lookup_max。"""
    cfg = {"max_num_seqs": 128, "gpu_memory_utilization": 0.9}
    off = {c["knob"] for c in propose_candidates("B", cfg, quality_gate=False)}
    on = {c["knob"] for c in propose_candidates("B", cfg, quality_gate=True)}
    assert "num_lookahead_slots" not in off and "num_lookahead_slots" in on
    assert "ngram_prompt_lookup_max" not in off and "ngram_prompt_lookup_max" in on


def test_runner_does_not_auto_escalate_to_quality_gate_when_t1_exhausted():
    """曾经 T1(不降精度)候选耗尽时,runner 会自动开 quality_gate 找 T2 候选
    (quantization/kv-cache-dtype 等)凑轮次续跑——用户反馈(2026-07-21)明确不要
    碰这些会降精度的参数、不提供这些动作,所以耗尽就该老实停(cause=no_candidates),
    不该悄悄换个更激进的候选池接着凑。真机上这条路径已经被 runw bridge 的硬编码
    --quality-gate 长期掩盖过(见 deploy/runw/autopilot_bridge.py),这里锁死源码
    层面不会再有这条自动切换。"""
    import inspect

    from pping_lang.autopilot import runner as runner_mod
    src = inspect.getsource(runner_mod)
    assert "self._quality_gate = True" not in src
    assert "T1 候选耗尽" not in src


def test_render_flags_skips_zero_default_overrides():
    """num_gpu_blocks_override=0 等 0 默认值应跳过渲染,避免 vLLM 报错。"""
    from pping_lang.autopilot.action_space import render_flags
    flags = render_flags({"max_num_seqs": 64, "num_gpu_blocks_override": 0,
                          "ngram_prompt_lookup_max": 0})
    assert "--max-num-seqs" in flags
    assert "--num-gpu-blocks-override" not in flags
    assert "--ngram-prompt-lookup-max" not in flags


def test_introspect_engine_args_reads_defaults_from_vllm_source(tmp_path):
    """半自动 introspect：从 vLLM 源码读取 EngineArgs 默认值,不导入 torch/vllm。

    用 tmp_path 现搭一份最小 EngineArgs 源码骨架驱动同一段正则解析逻辑,而不是指向
    某台机器上才存在的真实 vllm checkout——原先硬编码 D:\\GitCode\\vllm,只在原作者
    自己的 Windows 开发机上能过,其它机器/CI 上 arg_utils.py 不存在只会拿到 {}。"""
    from pping_lang.autopilot.action_space import _introspect_engine_args

    arg_utils = tmp_path / "vllm" / "engine" / "arg_utils.py"
    arg_utils.parent.mkdir(parents=True)
    arg_utils.write_text(
        "class EngineArgs:\n"
        "    gpu_memory_utilization: float = 0.9\n"
        "    num_scheduler_steps: int = 1\n"
        "    max_seq_len_to_capture: int = 8192\n"
        "    num_lookahead_slots: int = 0\n"
        "    max_num_seqs: int = SchedulerConfig.max_num_seqs\n",
        encoding="utf-8",
    )
    # EngineArgs 里不少字段默认值写成 SomeConfig.field(如上面的 max_num_seqs),
    # 需要额外一份 config.py 供 introspect 先解析出各 Config 类的字段默认值。
    (tmp_path / "vllm" / "config.py").write_text(
        "class SchedulerConfig:\n"
        "    max_num_seqs: int = None\n",
        encoding="utf-8",
    )
    defaults = _introspect_engine_args(str(tmp_path))
    assert isinstance(defaults, dict)
    # 这些字段能从源码字面量直接解析
    assert defaults.get("gpu_memory_utilization") == 0.9
    assert defaults.get("num_scheduler_steps") == 1
    assert defaults.get("max_seq_len_to_capture") == 8192
    assert defaults.get("num_lookahead_slots") == 0
    # SchedulerConfig 里 max_num_seqs 默认 None(运行时后处理),允许解析为 None
    assert "max_num_seqs" in defaults


def test_propose_d_headroom_guard():
    # 推大-batch 类(max_num_seqs↑)在 KV 余量不足时被守卫挡掉(§4.4),改给治 D 的参数
    cfg = {"max_num_seqs": 8, "gpu_memory_utilization": 0.70}
    tight = {c["knob"] for c in propose_candidates("A", cfg, kv_headroom=0.05)}
    loose = {c["knob"] for c in propose_candidates("A", cfg, kv_headroom=0.9)}
    assert "max_num_seqs" in loose and "max_num_seqs" not in tight


def test_propose_b_unions_d_relief_knobs():
    # B↔D 共生(§4.3)：诊断 B 时并入 D 缓解类参数(治 B 要推大 batch,batch 要 KV 空间)
    cfg = {"max_num_seqs": 64, "gpu_memory_utilization": 0.70}
    cands = propose_candidates("B", cfg)
    knobs = [c["knob"] for c in cands]
    assert len(knobs) == len(set(knobs))                       # 并集不产生重复参数
    secondary = {c["knob"]: c for c in cands if c.get("secondary_regime") == "D"}
    assert secondary, "B 诊断应并入 D 缓解类参数"
    for key in ("cpu_offload_gb", "num_gpu_blocks_override", "block_size"):
        assert key in secondary
    # 主 regime 版优先：gpu_memory_utilization 同属 B/D,只出现一次且不带并集标记
    gpu = [c for c in cands if c["knob"] == "gpu_memory_utilization"]
    assert len(gpu) == 1 and "secondary_regime" not in gpu[0]
    # 并集排在主 regime 候选之后
    first_secondary = next(i for i, c in enumerate(cands) if c.get("secondary_regime"))
    assert all("secondary_regime" not in c for c in cands[:first_secondary])
    # 方向按 D 语义：max_model_len 往下(腾容量)——需 config 带真实值才有降的空间
    cfg_len = {**cfg, "max_model_len": 32768}
    sec_len = {c["knob"]: c for c in propose_candidates("B", cfg_len)
               if c.get("secondary_regime") == "D"}
    assert sec_len["max_model_len"]["to"] < sec_len["max_model_len"]["from"]


def test_propose_b_union_respects_d_guard_and_disable():
    # 并集候选同样受 D 余量守卫硬约束：KV 紧张时 big_batch 照剪,容量缓解类照给
    cfg = {"max_num_seqs": 64, "gpu_memory_utilization": 0.70}
    tight = {c["knob"] for c in propose_candidates("B", cfg, kv_headroom=0.05)}
    assert "max_num_seqs" not in tight                        # 推大 batch 被剪
    assert "gpu_memory_utilization" in tight                  # 扩 KV 池照给
    assert "num_gpu_blocks_override" in tight                 # 并集的 D 缓解照给
    # couple_regimes 关掉 → 退回单 regime 交集
    solo = {c["knob"] for c in propose_candidates("B", cfg, couple_regimes=False)}
    assert "num_gpu_blocks_override" not in solo and "cpu_offload_gb" not in solo
    # 其他 regime 不受影响：D 诊断不并 B 的参数(无 secondary 标记)
    d_cands = propose_candidates("D", {"max_num_seqs": 128, "gpu_memory_utilization": 0.70})
    assert not any(c.get("secondary_regime") for c in d_cands)


def test_kvfit_prunes_obvious_capacity_failure():
    from pping_lang.autopilot.kvfit import apply_kvfit, evaluate_kvfit
    sc = Scorecard(output_tps=1000, ttft_p99_ms=100, tpot_p99_ms=10,
                   run_meta={"prompt_tokens": 7500, "output_tokens": 16,
                             "runtime_probe": {
                                 "kv_cache_usage": {"max": 1.0},
                                 "running_reqs": {"max": 160},
                             }})
    cands = [
        {"knob": "max_num_seqs", "config": {"max_num_seqs": 1024, "gpu_memory_utilization": 0.5}},
        {"knob": "max_num_seqs", "config": {"max_num_seqs": 96, "gpu_memory_utilization": 0.5}},
    ]
    kept = apply_kvfit(cands, {"max_num_seqs": 512, "gpu_memory_utilization": 0.5}, sc)
    assert [c["config"]["max_num_seqs"] for c in kept] == [96]
    assert kept[0]["p0"]["verdict"] == "allow"
    result = evaluate_kvfit(cands, {"max_num_seqs": 512, "gpu_memory_utilization": 0.5}, sc)
    assert result.summary()["pruned"] == 1
    assert result.pruned[0]["to"] == 1024
    assert result.pruned[0]["p0"]["verdict"] == "reject"


def test_kvfit_does_not_prune_capacity_relief():
    from pping_lang.autopilot.kvfit import predict_kv_fit
    sc = Scorecard(output_tps=1000, ttft_p99_ms=100, tpot_p99_ms=10,
                   run_meta={"prompt_tokens": 7500, "output_tokens": 16,
                             "runtime_probe": {
                                 "kv_cache_usage": {"max": 1.0},
                                 "running_reqs": {"max": 160},
                             }})
    est = predict_kv_fit(
        {"max_num_seqs": 512, "gpu_memory_utilization": 0.5},
        {"max_num_seqs": 256, "gpu_memory_utilization": 0.5},
        sc)
    assert est.verdict == "allow"
    assert "does not raise" in est.reason


def test_kvfit_rejects_max_model_len_below_workload():
    from pping_lang.autopilot.kvfit import predict_kv_fit
    sc = Scorecard(output_tps=1000, ttft_p99_ms=100, tpot_p99_ms=10,
                   run_meta={"prompt_tokens": 7500, "output_tokens": 16})
    est = predict_kv_fit(
        {"max_num_seqs": 32, "gpu_memory_utilization": 0.7},
        {"max_num_seqs": 32, "gpu_memory_utilization": 0.7, "max_model_len": 4096},
        sc)
    assert est.verdict == "reject"
    assert "max_model_len" in est.reason


def test_propose_skips_unsupported_knob():
    # vLLM 0.21 硬拒非默认 max_num_partial_prefills(Concurrent Partial Prefill 未支持)
    # → 永不提议,不管约束图是否满足(真机连烧两轮 LaunchError 的教训)。
    on = {c["knob"] for c in propose_candidates("A", {"max_num_seqs": 8, "enable_chunked_prefill": True})}
    assert "max_num_partial_prefills" not in on


def test_propose_skips_removed_v0_scheduler_knobs():
    """真机复现(2026-07-16 dogfood,两次不同 session):`num_scheduler_steps`/
    `scheduler_delay_factor` 在这个 vLLM 版本的 `vllm serve --help` 里根本不存在
    (V1 引擎砍掉的 V0 遗留 multi-step 调度参数),每次被选中都是 `unrecognized
    arguments` 崩溃回滚,白烧一轮——标 unsupported 后不该再被提议。"""
    on = {c["knob"] for c in propose_candidates("A", {"max_num_seqs": 8})}
    assert "num_scheduler_steps" not in on
    assert "scheduler_delay_factor" not in on


def test_constraint_graph_needs_mechanism():
    # 约束图 needs 机制本身(§4.5)：前置 flag 关着 → 不可行。
    from pping_lang.autopilot.action_space import Knob, _feasible
    k = Knob("x", "--x", "int", "test", helps=("A",), hurts=(), primary_slo="ttft",
             default=1, lo=1, hi=8, needs=("enable_chunked_prefill",))
    assert _feasible(k, {"enable_chunked_prefill": True})
    assert not _feasible(k, {"enable_chunked_prefill": False})


def test_propose_constraint_graph_conflicts():
    # async scheduling 开着时不提 speculative;kv fp8 与 FLASHINFER 互斥,auto 不算启用。
    cfg = {"max_num_seqs": 128, "gpu_memory_utilization": 0.92,
           "async_scheduling": True, "attention_backend": "auto"}
    keys = {c["knob"] for c in propose_candidates("B", cfg, quality_gate=True)}
    assert "speculative" not in keys and "kv_cache_dtype" in keys
    cfg["attention_backend"] = "FLASHINFER"
    keys = {c["knob"] for c in propose_candidates("B", cfg, quality_gate=True)}
    assert "kv_cache_dtype" not in keys


def test_p2_grid_expands_candidate_values():
    from pping_lang.autopilot.search import expand_grid_candidates
    base = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    grid = expand_grid_candidates(base, {"max_num_seqs": 32, "gpu_memory_utilization": 0.70},
                                  "A", max_values_per_knob=3)
    vals = [c["to"] for c in grid if c["knob"] == "max_num_seqs"]
    assert vals[:3] == [64, 128, 256]
    assert all(c["p2"]["strategy"] == "grid" for c in grid)


def test_bo_ranking_uses_history_rewards():
    from pping_lang.autopilot.search import bo_rank_candidates, expand_grid_candidates
    base = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70,
                                    "enable_chunked_prefill": True})
    grid = expand_grid_candidates(base, {"max_num_seqs": 32, "gpu_memory_utilization": 0.70,
                                         "enable_chunked_prefill": True}, "A")
    ranked = bo_rank_candidates(grid, [{"knob": "max_num_batched_tokens", "decision": "kept"},
                                       {"knob": "max_num_seqs", "decision": "reverted"}])
    assert ranked[0]["knob"] == "max_num_batched_tokens"
    assert ranked[0]["p2"]["strategy"] == "bo"
    assert ranked[0]["p2"]["acquisition"] > ranked[-1]["p2"]["acquisition"]


def test_introspect_does_not_crash():
    from pping_lang.autopilot.action_space import introspect_defaults, param_surface_size
    assert isinstance(introspect_defaults(), dict)
    assert param_surface_size() > 0          # vllm 在则实数,否则约数 258


# ---- sandbox(sim 曲线)----

def test_sim_throughput_rises_with_concurrency():
    sb = SimSandbox()
    sb.apply({"max_num_seqs": 32, "gpu_memory_utilization": 0.70}); a = sb.measure(OBJ)
    sb.apply({"max_num_seqs": 128, "gpu_memory_utilization": 0.70}); b = sb.measure(OBJ)
    assert b.output_tps > a.output_tps and b.ttft_p99_ms > a.ttft_p99_ms
    assert a.run_meta["concurrency"] == BENCH_SPEC["concurrency"]


def test_sim_oversubscribe_breaks_sla():
    sb = SimSandbox()
    sb.apply({"max_num_seqs": 512, "gpu_memory_utilization": 0.70})
    sc = sb.measure(OBJ)
    assert sc.ttft_p99_ms > 1000                        # 过订阅 → TTFT 飙破 SLA
    assert objective_score(sc, OBJ) == float("-inf")


def test_sim_launch_error_on_oom():
    sb = SimSandbox()
    try:
        sb.apply({"gpu_memory_utilization": 0.99}); assert False
    except LaunchError:
        pass


def test_diagnose_maps_pressure_to_bottleneck():
    sc = SimSandbox(); sc.apply({"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    assert diagnose({"max_num_seqs": 32, "gpu_memory_utilization": 0.70}, sc.measure(OBJ))["bottleneck"] == "A"
    assert diagnose({"max_num_seqs": 300, "gpu_memory_utilization": 0.70}, sc.measure(OBJ))["bottleneck"] == "D"


# ---- session_store ----

def test_session_store_roundtrip(tmp_path):
    st = SessionStore(tmp_path / "s.jsonl")
    st.new_session("ap-x", {"target": "throughput"}, {"rounds": 6})
    st.append_event("observe", "读取诊断", round=1, detail={"candidate_count": 3})
    st.append_round(Round(round=0, kind="baseline", decision="baseline"))
    st.update_best(0, {"max_num_seqs": 32}, 1240.0, "vllm serve ...")
    d = st.status_dict()
    assert d["session_id"] == "ap-x" and len(d["rounds"]) == 1
    assert d["events"][0]["phase"] == "observe"
    assert d["events"][0]["detail"]["candidate_count"] == 3
    assert d["best"]["score"] == 1240.0
    assert (tmp_path / "s.jsonl").exists()
    st.close()
    loaded = SessionStore.load_status(tmp_path / "s.jsonl")
    assert loaded["events"][0]["message"] == "读取诊断"


def test_status_dict_sanitizes_infinite_scores_for_strict_json(tmp_path):
    """objective_score() 用 float('-inf') 当"SLA 破/候选起不来"的合法内部哨兵——
    这条一旦真机复现：tight SLA 形态(如代码补全 ttft<=100ms)下 baseline 自己都可能
    破线,round 0 起就带 -inf 分。Python 的 json.dumps 默认把它原样写成 -Infinity,
    Python 自己的 json.loads 认,但浏览器原生 fetch().json() 直接 SyntaxError——
    dashboard 每 2s 轮询 /api/autopilot/status 从此永久解析失败,UI 误报"失败"且
    不会自愈(数据本身坏了,不是网络抖动)。status_dict() 和落盘的 JSONL 都必须
    把 ±inf/NaN 换成 null 才是合法 JSON。"""
    store = SessionStore(tmp_path / "inf.jsonl")
    store.new_session("ap-inf", {"target": "throughput"}, {"rounds": 1})
    store.append_round(Round(round=0, kind="baseline", decision="baseline",
                              objective_score_after=float("-inf"),
                              scorecard_after={"ttft_p99_ms": float("inf")}))
    text = json.dumps(store.status_dict())
    assert "Infinity" not in text and "NaN" not in text
    data = json.loads(text)
    assert data["rounds"][0]["objective_score_after"] is None
    assert data["rounds"][0]["scorecard_after"]["ttft_p99_ms"] is None
    store.close()
    # JSONL 落盘同样干净(bridge/CLI 从磁盘回放走的是这份数据)
    raw = (tmp_path / "inf.jsonl").read_text(encoding="utf-8")
    assert "Infinity" not in raw and "NaN" not in raw
    loaded = SessionStore.load_status(tmp_path / "inf.jsonl")
    assert loaded["rounds"][0]["objective_score_after"] is None


# ---- runner 闭环(sim + stub)----

def test_runner_full_session_improves(tmp_path):
    store = SessionStore(tmp_path / "run.jsonl")
    store.new_session("ap-run", {"target": "throughput"}, {"rounds": 6})
    Runner(store=store, sandbox=SimSandbox(), agent=StubAgent(), obj=OBJ,
           budget={"rounds": 6, "seconds": 900}, model="M", step_delay_s=0.0).run()
    d = store.status_dict()
    assert d["state"] == "done"
    assert d["rounds"][0]["decision"] == "baseline"
    kept = [r for r in d["rounds"] if r["decision"] == "kept"]
    assert len(kept) >= 1                                # 至少一轮真改进
    assert d["best"]["score"] > d["baseline_score"]      # 比基线强
    assert d["recommended_command"].startswith("vllm serve")
    assert d["applies_to"]["model"] == "M"
    assert d["applies_to"]["gpu"] == "sim-GPU"
    assert d["applies_to"]["workload_form"]["prompt_source"] == BENCH_SPEC["prompt_source"]
    assert d["applies_to"]["workload_form"]["prompt_tokens"] == BENCH_SPEC["prompt_tokens"]
    assert d["applies_to"]["objective"]["sla"]["ttft_p99_ms"] == 1000.0
    assert d["applies_to"]["objective"]["search_mode"] == "agent"
    assert d["applies_to"]["objective"]["bench_repeats"] == 1
    assert d["promote_package"]["state"] == "ready"
    assert d["promote_package"]["requires_confirmation"] is True
    assert d["promote_package"]["production_command"] == d["recommended_command"]
    assert d["promote_package"]["rollback_command"] == (
        "vllm serve M --max-num-seqs 64 --gpu-memory-utilization 0.7")
    phases = [e["phase"] for e in d["events"]]
    for phase in ["baseline", "observe", "propose", "apply", "benchmark", "decide", "finalize"]:
        assert phase in phases
    # action_space_summary(用户反馈,2026-07-22):"为什么只调这 2-3 个参数"要有据可查。
    aas = d["action_space_summary"]
    assert aas["total"] > aas["considerable_count"] > 0
    assert set(aas["tried_knobs"]) <= set(aas["relevant_knobs"]) <= set(aas["considerable_knobs"])
    assert aas["bottlenecks_seen"]                       # 至少诊断到过一种瓶颈
    store.close()


def test_e2e_sla_violation_does_not_block_real_improvement(tmp_path):
    """回归测试(真机复现,2026-07-23):7B-AWQ 上 chat/code 形态默认的 E2E 阈值比基线还紧,
    旧版 sla_ok() 把 E2E 也纳入闸门,导致基线一开局就判 -inf,后续候选无论怎么改都过不了
    这道硬门槛(-inf 打不过 -inf),session 全程"无改进",尽管 TTFT/TPOT 实际都在变好——
    用户反馈"性能怎么都上不去"正是这个根因。E2E 现在只监控上报,不进闸门,只要 TTFT/TPOT
    仍达标,更优候选就该正常被 kept。"""
    store = SessionStore(tmp_path / "e2e.jsonl")
    sla = {"ttft_p99_ms": 2000.0, "tpot_p99_ms": 50.0, "e2e_p99_ms": 500.0}   # e2e 阈值故意卡得比基线还紧
    obj = ObjectiveSpec(target="throughput", sla=SLA(**sla))
    store.new_session("ap-e2e", {"target": "throughput", "sla": sla}, {"rounds": 6})
    Runner(store=store, sandbox=SimSandbox(), agent=StubAgent(), obj=obj,
           budget={"rounds": 6, "seconds": 900}, model="M", step_delay_s=0.0).run()
    d = store.status_dict()
    baseline_sc = d["rounds"][0]["scorecard_after"]
    assert baseline_sc["e2e_p99_ms"] > 500                    # 基线本身就超 E2E 监控阈值
    assert baseline_sc["ttft_p99_ms"] <= 2000                 # 但 TTFT/TPOT 仍达标
    kept = [r for r in d["rounds"] if r["decision"] == "kept"]
    assert len(kept) >= 1                                     # E2E 超标不再拖累 kept 判定
    assert d["best"]["score"] > d["baseline_score"]
    store.close()


def test_runner_stops_cleanly_when_agent_raises_stop_requested(tmp_path):
    """agent.propose() 抛 AgentStopRequested(用户手动停止,卡在阻塞调用里被打断)时,
    _run_loop 要记一条 stop_cause=user_stop 的 stop 轮就 return,而不是当成异常走
    run() 顶层的 except → state=failed;_finalize() 仍要正常跑完(恢复 best、生成
    上线包、算 action_space_summary),不能因为是"被打断"就少一截收尾。"""
    from pping_lang.autopilot.agent import AgentDecision, AgentStopRequested

    class StopOnSecondRound:
        model = "stop-on-second-round"

        def __init__(self):
            self.calls = 0
            self.runner = None       # 测试里手动接上,模拟"外部信号已经把 runner._stopping set 了"

        def propose(self, ctx):
            self.calls += 1
            if self.calls >= 2:
                self.runner.stop()   # 对应真机：_should_stop() 命中时 runner._stopping 已被 set
                raise AgentStopRequested("用户手动停止")
            cand = ctx.candidates[0]
            return AgentDecision(knob=cand["knob"], config=cand["config"],
                                 from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                 rationale="第一轮正常提案")

    store = SessionStore(tmp_path / "stop.jsonl")
    store.new_session("ap-stop", {"target": "throughput"}, {"rounds": 6})
    agent = StopOnSecondRound()
    runner = Runner(store=store, sandbox=SimSandbox("M"), agent=agent, obj=OBJ,
                    budget={"rounds": 6, "seconds": 900}, model="M", step_delay_s=0.0)
    agent.runner = runner
    runner.run()

    d = store.status_dict()
    assert d["state"] == "stopped"                      # 不是 failed,也不是 done
    assert agent.calls == 2                              # 第二轮就被打断,没有继续跑满 6 轮
    stop_rounds = [r for r in d["rounds"] if r["kind"] == "stop"]
    assert len(stop_rounds) == 1 and stop_rounds[0]["stop_cause"] == "user_stop"
    assert d["recommended_command"].startswith("vllm serve")  # _finalize 仍正常收尾
    assert d["promote_package"]["state"] == "ready"
    assert d["action_space_summary"]["total"] > 0
    store.close()


def test_runner_repeats_bench_and_reports_config_review(tmp_path):
    class CountingSandbox(SimSandbox):
        def __init__(self):
            super().__init__("M")
            self.measures = 0

        def measure(self, obj):
            self.measures += 1
            sc = super().measure(obj)
            sc.run_meta["sample_no"] = self.measures
            return sc

    sb = CountingSandbox()
    store = SessionStore(tmp_path / "repeat.jsonl")
    store.new_session("ap-repeat", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store, sandbox=sb, agent=StubAgent(), obj=OBJ,
           budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0,
           bench_repeats=3).run()
    d = store.status_dict()
    assert sb.measures == 6                         # baseline 3 + candidate 3
    assert d["rounds"][0]["scorecard_after"]["run_meta"]["bench_repeats"] == 3
    cand = next(r for r in d["rounds"] if r["kind"] == "candidate")
    assert cand["scorecard_after"]["run_meta"]["bench_repeats"] == 3
    assert d["config_review"]["changes"]
    assert any(c["key"] == "max_num_seqs" for c in d["config_review"]["changes"])
    store.close()


def test_runner_conditional_repeats_only_on_borderline(tmp_path):
    """条件 median-of-3：默认 bench_repeats=1;Δ 落噪声带(gpu_util 微调恒 tie)才补测
    2 次走 median;明确改进(max_num_seqs 大幅提升)不补——补测时间只花在判定边界上。"""
    from pping_lang.autopilot.agent import AgentDecision

    class CountingSandbox(SimSandbox):
        def __init__(self):
            super().__init__("M")
            self.measures = 0

        def measure(self, obj):
            self.measures += 1
            return super().measure(obj)

    class PickKnobAgent:
        def __init__(self, key):
            self.model = f"pick-{key}"
            self._key = key

        def propose(self, ctx):
            cand = next(c for c in ctx.candidates if c["knob"] == self._key)
            return AgentDecision(knob=cand["knob"], config=cand["config"],
                                 from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                 rationale=f"试 {self._key}")

    # Δ 在噪声带内 → 补测：baseline 1 + candidate 3
    sb = CountingSandbox()
    store = SessionStore(tmp_path / "cond-rep.jsonl")
    store.new_session("ap-cond-rep", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store, sandbox=sb, agent=PickKnobAgent("long_prefill_token_threshold"), obj=OBJ,
           budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0).run()
    d = store.status_dict()
    assert sb.measures == 4, f"边界成绩应补测：measure {sb.measures} 次,预期 4(1+3)"
    cand = next(r for r in d["rounds"] if r["kind"] == "candidate")
    assert cand["bench_spec"]["bench_repeats"] == 3
    assert cand["bench_spec"]["repeats_trigger"] == "borderline"
    store.close()

    # 明确改进 → 不补：baseline 1 + candidate 1
    sb2 = CountingSandbox()
    store2 = SessionStore(tmp_path / "cond-norep.jsonl")
    store2.new_session("ap-cond-norep", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store2, sandbox=sb2, agent=PickKnobAgent("max_num_seqs"), obj=OBJ,
           budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0).run()
    assert sb2.measures == 2, f"明确改进不该补测：measure {sb2.measures} 次,预期 2(1+1)"
    cand2 = next(r for r in store2.status_dict()["rounds"] if r["kind"] == "candidate")
    assert cand2["bench_spec"]["bench_repeats"] == 1
    assert "repeats_trigger" not in cand2["bench_spec"]
    store2.close()


def test_runner_records_p2_search_mode(tmp_path):
    from pping_lang.autopilot.agent import AgentDecision
    seen = []

    class SpyAgent:
        model = "spy"

        def propose(self, ctx):
            seen.extend(ctx.candidates)
            return AgentDecision(done=True, rationale="stop")

    store = SessionStore(tmp_path / "p2.jsonl")
    store.new_session("ap-p2", {"target": "throughput"}, {"rounds": 2})
    Runner(store=store, sandbox=SimSandbox("M"), agent=SpyAgent(), obj=OBJ,
           budget={"rounds": 2, "seconds": 900}, model="M", step_delay_s=0.0,
           search_mode="bo", search_width=3).run()
    stop = next(r for r in store.status_dict()["rounds"] if r["kind"] == "stop")
    assert stop["diagnosis"]["p2_search"]["mode"] == "bo"
    assert seen and all(c.get("p2", {}).get("strategy") == "bo" for c in seen)
    store.close()


def test_runner_records_selected_p2_metadata_in_action(tmp_path):
    store = SessionStore(tmp_path / "p2-action.jsonl")
    store.new_session("ap-p2-action", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store, sandbox=SimSandbox("M"), agent=StubAgent(), obj=OBJ,
           budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0,
           search_mode="bo", search_width=3).run()
    cand = next(r for r in store.status_dict()["rounds"] if r["kind"] == "candidate")
    assert cand["action"]["p2"]["strategy"] == "bo"
    assert "acquisition" in cand["action"]["p2"]
    assert cand["action"]["p0"]["verdict"] in ("allow", "warn", "unknown")
    store.close()


# ---- controller / API ----

def test_controller_single_session_409():
    ctrl = AutopilotController(model="M", sim=True, step_delay_s=0.0)
    sid = ctrl.start({"target": "throughput", "sla": {"ttft_p99_ms": 1000}}, {"rounds": 4}, None)
    assert sid.startswith("ap-")
    # 活动中再 start → RuntimeError(路由层转 409);跑很快可能已 done,容忍两种
    try:
        ctrl.start({"target": "throughput"}, {"rounds": 4}, None)
    except RuntimeError:
        pass
    # 等它跑完
    if ctrl._runner:
        ctrl._runner.join(timeout=5)
    assert ctrl.status()["state"] in ("done", "stopped")


def test_build_objective():
    o = build_objective({"target": "throughput", "sla": {"ttft_p99_ms": 800}})
    assert o.target == "throughput" and o.sla.ttft_p99_ms == 800

    o2 = build_objective({"target": "throughput",
                          "sla": {"ttft_p99_ms": 800, "tpot_p99_ms": 30, "e2e_p99_ms": 5000}})
    assert o2.sla.e2e_p99_ms == 5000


def test_runner_defaults_and_no_improve_backstop(tmp_path):
    from pping_lang.autopilot.runner import K_NO_IMPROVE, MIN_EXPLORE_ROUNDS, Runner

    store = SessionStore(tmp_path / "defaults.jsonl")
    agent = StubAgent()
    runner = Runner(store=store, sandbox=SimSandbox("M"), agent=agent,
                    obj=build_objective({"target": "throughput"}), budget={}, model="M")
    assert runner._rounds_budget == 12
    assert runner._secs_budget == 30 * 60
    # 2026-07-12 复盘：999 曾是"强制跑满预算"的临时值;真机证据显示 agent 判 done 时
    # 桌上永远还有候选(候选真空由 T1→T2 fallback 在问它之前就兜掉了),它是在做定性
    # 判断而非"没得选",且被强制的额外轮次实测收益也有限——K_NO_IMPROVE 恢复成不依赖
    # 信任 LLM 的机械兜底,MIN_EXPLORE_ROUNDS 单独兜底"防止刚起步就撂挑子"。
    assert K_NO_IMPROVE == 4
    assert MIN_EXPLORE_ROUNDS == 2


def test_runner_sla_never_met_overrides_agent_done(tmp_path):
    """best_score=-inf(从未通过 SLA)时,agent 的 done 被覆盖——这跟"收益递减"是不同风险
    等级("我放弃了"和"从没成功过、我放弃了"分量不一样),哪怕已经探索够 MIN_EXPLORE_ROUNDS
    轮也不该采信。AlwaysDoneAgent 恒返回 done -> 每轮都被强制继续,直到 K_NO_IMPROVE 兜底
    (连续 4 轮无改善)触发才停——不是预算耗尽,是"试够了还是没戏"的诚实止损。"""
    from pping_lang.autopilot.agent import AgentDecision

    call_count = [0]

    class AlwaysDoneAgent:
        model = "always-done"

        def propose(self, ctx):
            call_count[0] += 1
            return AgentDecision(done=True, rationale="我判断非配置可解")

    # SLA 极严：基线 tpot=22 远超 10ms -> best_score 恒 -inf
    tight_obj = ObjectiveSpec(target="throughput", sla=SLA(tpot_p99_ms=10.0))
    store = SessionStore(tmp_path / "sla-guard.jsonl")
    store.new_session("ap-sla-guard", {"target": "throughput"}, {"rounds": 12})
    Runner(store=store, sandbox=SimSandbox(), agent=AlwaysDoneAgent(), obj=tight_obj,
           budget={"rounds": 12, "seconds": 900}, model="M", step_delay_s=0.0).run()
    d = store.status_dict()

    # K_NO_IMPROVE=4：每轮 done 都被强制继续、每轮都判负(SLA 恒不达标) -> 连续 4 轮
    # 无改善,兜底触发,agent 恰好被调 4 次(不是跑满 12 轮预算)
    assert call_count[0] == 4, f"agent 被调 {call_count[0]} 次,预期 4(K_NO_IMPROVE 兜底)"
    overrides = [e for e in d["events"] if "从未通过 SLA" in e.get("message", "")]
    assert len(overrides) == 4, f"强制继续 {len(overrides)} 次,预期 4"
    # 最终状态仍是 done(K_NO_IMPROVE 是正常收尾路径,不是 failed/stopped)
    assert d["state"] == "done"
    # 停机归因：K 兜底触发的自然退出必须补记 stop 轮
    stop = next(r for r in d["rounds"] if r["kind"] == "stop")
    assert stop["stop_cause"] == "no_improve_k"
    store.close()


def test_runner_forced_continue_preserves_original_agent_thinking(tmp_path):
    """真机复现(2026-07-16):agent 判 done 被 MIN_EXPLORE_ROUNDS 强制覆盖成一个候选轮后,
    该轮落盘的 agent_thinking 是空的——覆盖逻辑重新造了个 AgentDecision,没把原始 dec
    的 thinking 带过去,UI 那轮"Agent 思考过程"整节就消失了,用户看不到 agent 当时为什么
    想停。thinking 必须跟着原始 dec 一起保留下来。"""
    from pping_lang.autopilot.agent import AgentDecision

    class DoneWithThinkingAgent:
        model = "done-with-thinking"

        def propose(self, ctx):
            return AgentDecision(done=True, rationale="我判断已近最优",
                                 thinking="baseline 已经很稳,感觉不用再试了")

    store = SessionStore(tmp_path / "preserve-thinking.jsonl")
    store.new_session("ap-preserve-thinking", {"target": "throughput"}, {"rounds": 12})
    Runner(store=store, sandbox=SimSandbox(), agent=DoneWithThinkingAgent(), obj=OBJ,
           budget={"rounds": 12, "seconds": 900}, model="M", step_delay_s=0.0).run()
    d = store.status_dict()

    forced_round = next(r for r in d["rounds"] if r["kind"] == "candidate")
    assert forced_round["agent_thinking"] == "baseline 已经很稳,感觉不用再试了"
    store.close()


def test_runner_honors_done_after_min_explore_rounds_when_sla_ok(tmp_path):
    """SLA 已通过 + 探索满 MIN_EXPLORE_ROUNDS + 桌面大半已试过后,agent 的 done 才真正
    生效。相对门槛(③)：桌面超过一半候选没试过时 done 被强制覆盖——"2 轮探索 + 1 轮
    判 done"不再直接信任;本例第 3 轮的 done 被 ③ 强制(桌面 2/3 未试),第 4 轮桌面
    已听审充分,采信。"""
    from pping_lang.autopilot.agent import AgentDecision

    call_count = [0]

    class DoneAfterTwoAgent:
        """前两轮正常提议(推进探索计数),第三轮判 done。"""
        model = "done-after-two"

        def propose(self, ctx):
            call_count[0] += 1
            if call_count[0] <= 2:
                cand = ctx.candidates[0]
                return AgentDecision(knob=cand["knob"], config=cand["config"],
                                     from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                     rationale=f"round {call_count[0]}: 试 {cand['knob']}")
            return AgentDecision(done=True, rationale="已探索两轮,收益递减,判断已近最优")

    store = SessionStore(tmp_path / "honor-done.jsonl")
    store.new_session("ap-honor-done", {"target": "throughput"}, {"rounds": 12})
    Runner(store=store, sandbox=SimSandbox(), agent=DoneAfterTwoAgent(), obj=OBJ,
           budget={"rounds": 12, "seconds": 900}, model="M", step_delay_s=0.0).run()
    d = store.status_dict()

    # 桌面 6 候选(B↔D 并集后):done 连续两轮被 ③ 强制(5/6、4/6 未试),试过一半后采信
    assert call_count[0] == 5, (f"agent 被调 {call_count[0]} 次,预期 5"
                                "(2轮探索+2轮③强制+桌面半试后采信)")
    forced_rel = [e for e in d["events"] if "候选未试" in e.get("message", "")]
    assert len(forced_rel) == 2, f"相对门槛强制 {len(forced_rel)} 次,预期 2"
    stop = next(r for r in d["rounds"] if r["kind"] == "stop")
    assert stop["stop_cause"] == "agent_done"
    assert "收益递减" in stop["rationale"]
    tried_n = sum(1 for s in stop["table_snapshot"] if s["tried"] != "untried")
    assert tried_n * 2 >= len(stop["table_snapshot"]), "采信时桌面应至少试过一半"
    store.close()


def test_runner_relative_gate_forced_cap_then_honors(tmp_path, monkeypatch):
    """相对门槛(③)的连续强制有上限(MAX_FORCED_RELATIVE)：达到上限后,即使桌面仍有
    大半未试也采信 done 并在 rationale 注记——再逼下去就是烧 bench 换不到信息
    (monkeypatch 上限为 0 直接走采信路径验证注记机制)。"""
    from pping_lang.autopilot.agent import AgentDecision
    monkeypatch.setattr("pping_lang.autopilot.runner.MAX_FORCED_RELATIVE", 0)

    calls = [0]

    class AlwaysDoneAgent:
        model = "always-done-cap"

        def propose(self, ctx):
            calls[0] += 1
            return AgentDecision(done=True, rationale="判断已近最优")

    store = SessionStore(tmp_path / "rel-cap.jsonl")
    store.new_session("ap-rel-cap", {"target": "throughput"}, {"rounds": 12})
    Runner(store=store, sandbox=SimSandbox(), agent=AlwaysDoneAgent(), obj=OBJ,
           budget={"rounds": 12, "seconds": 900}, model="M", step_delay_s=0.0).run()
    d = store.status_dict()
    assert calls[0] == 3, f"agent 被调 {calls[0]} 次,预期 3(①强制2轮+③被关→第3轮采信)"
    stop = next(r for r in d["rounds"] if r["kind"] == "stop")
    assert stop["stop_cause"] == "agent_done"
    assert "达上限" in stop["rationale"]
    store.close()


def test_runner_forced_continue_stops_honestly_when_all_candidates_already_failed(tmp_path):
    """探索不够触发强制续跑,但候选集里剩下的全是已判负的配置时——重试产生不了新信息,
    不该退回 cands[0] 重复一个已知没用的候选,应该老实停。"""
    from pping_lang.autopilot.agent import AgentDecision

    class DoneImmediatelyAgent:
        model = "done-immediately"

        def propose(self, ctx):
            # 候选集只有 1 个参数时,防重命中即可制造"仅剩候选已判负"的场景
            if not ctx.tried_configs:
                cand = ctx.candidates[0]
                return AgentDecision(knob=cand["knob"], config=cand["config"],
                                     from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                     rationale="先试一个,大概率会被判负/持平")
            return AgentDecision(done=True, rationale="判断已近最优")

    store = SessionStore(tmp_path / "no-repeat.jsonl")
    store.new_session("ap-no-repeat", {"target": "throughput"}, {"rounds": 12})
    # gpu_util=0.97 逼近 SimSandbox 的 OOM 上限(>0.97 才拒),max_num_seqs 顶格让候选集很窄
    Runner(store=store, sandbox=SimSandbox(), agent=DoneImmediatelyAgent(), obj=OBJ,
           budget={"rounds": 12, "seconds": 900}, model="M", step_delay_s=0.0,
           baseline_config={"max_num_seqs": 2048, "gpu_memory_utilization": 0.97}).run()
    d = store.status_dict()
    # 不管最终停在哪一轮,事件里都不该出现重复已判负配置的强制候选轮
    stop = next((r for r in d["rounds"] if r["kind"] == "stop"), None)
    assert stop is not None
    store.close()


# ---- stop_cause 停机归因 ----

def test_runner_stop_cause_budget_rounds(tmp_path):
    """轮数预算耗尽是自然退出(非 break)：以前静默,现在必须补记 stop 轮,
    stop_cause=budget_rounds。"""
    store = SessionStore(tmp_path / "cause-rounds.jsonl")
    store.new_session("ap-cause-rounds", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store, sandbox=SimSandbox(), agent=StubAgent(), obj=OBJ,
           budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0).run()
    d = store.status_dict()
    assert d["state"] == "done"
    stop = next(r for r in d["rounds"] if r["kind"] == "stop")
    assert stop["stop_cause"] == "budget_rounds"
    store.close()


def test_runner_stop_cause_agent_done_carries_table_snapshot(tmp_path):
    """agent done 被采信时,stop 轮带判停瞬间的桌面快照——每个候选试没试过、
    P0 剪没剪都可审计(「没值得试的」不再是空口)。"""
    from pping_lang.autopilot.agent import AgentDecision

    call_count = [0]

    class DoneAfterTwoAgent:
        model = "done-after-two-snap"

        def propose(self, ctx):
            call_count[0] += 1
            if call_count[0] <= 2:
                cand = ctx.candidates[0]
                return AgentDecision(knob=cand["knob"], config=cand["config"],
                                     from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                     rationale=f"round {call_count[0]}")
            return AgentDecision(done=True, rationale="收益递减,判断已近最优")

    store = SessionStore(tmp_path / "cause-done.jsonl")
    store.new_session("ap-cause-done", {"target": "throughput"}, {"rounds": 12})
    Runner(store=store, sandbox=SimSandbox(), agent=DoneAfterTwoAgent(), obj=OBJ,
           budget={"rounds": 12, "seconds": 900}, model="M", step_delay_s=0.0).run()
    d = store.status_dict()
    stop = next(r for r in d["rounds"] if r["kind"] == "stop")
    assert stop["stop_cause"] == "agent_done"
    snap = stop["table_snapshot"]
    assert len(snap) > 0
    for entry in snap:
        assert set(entry) == {"knob", "from", "to", "tried", "p0"}
        assert entry["tried"] in ("untried", "kept", "reverted", "tie")
        assert entry["p0"].startswith(("kept", "pruned:"))
    store.close()


def test_runner_stop_cause_user_stop(tmp_path):
    """手动 stop：循环头部直接退出,补记的 stop 轮 stop_cause=user_stop,
    最终状态 stopped。"""
    store = SessionStore(tmp_path / "cause-stop.jsonl")
    store.new_session("ap-cause-stop", {"target": "throughput"}, {"rounds": 12})
    runner = Runner(store=store, sandbox=SimSandbox(), agent=StubAgent(), obj=OBJ,
                    budget={"rounds": 12, "seconds": 900}, model="M", step_delay_s=0.0)
    runner.stop()                    # baseline 之前发出停止：baseline 跑完,主循环不进
    runner.run()
    d = store.status_dict()
    assert d["state"] == "stopped"
    stop = next(r for r in d["rounds"] if r["kind"] == "stop")
    assert stop["stop_cause"] == "user_stop"
    store.close()


def test_resume_session_tolerates_rounds_without_stop_fields(tmp_path):
    """向后兼容：旧 JSONL 的 round 行没有 stop_cause/table_snapshot(且可能带未知字段),
    resume 时不报错,新字段取默认值。"""
    path = tmp_path / "old.jsonl"
    path.write_text(
        json.dumps({"rec": "session_start", "session_id": "ap-old", "state": "proposing",
                    "objective": {"target": "throughput"}, "budget": {"rounds": 12}},
                   ensure_ascii=False) + "\n"
        + json.dumps({"rec": "round", "session_id": "ap-old", "round": 0, "kind": "baseline",
                      "decision": "baseline", "config_after": {"max_num_seqs": 32},
                      "objective_score_after": 100.0, "future_unknown_field": 1},
                     ensure_ascii=False) + "\n",
        encoding="utf-8")
    st = SessionStore(path)
    cur = st.resume_session()
    assert cur is not None
    assert cur.rounds[0].stop_cause is None
    assert cur.rounds[0].table_snapshot == []
    st.close()


def test_agent_config_connectivity_probe(monkeypatch):
    from pping_lang.autopilot import api as ap_api

    assert not ap_api.test_agent_config({"base_url": "http://x", "model": "m"})["ok"]

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(ap_api.urllib.request, "urlopen", fake_urlopen)
    out = ap_api.test_agent_config({
        "base_url": "https://api.moonshot.ai/v1",
        "api_key": "secret",
        "model": "kimi-k2.6",
        "temperature": 0.6,
        "timeout_s": 9,
    })
    assert out["ok"] is True and out["sample"] == "ok"
    assert seen["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["model"] == "kimi-k2.6"
    assert seen["body"]["temperature"] == 0.6
    assert seen["timeout"] == 9


def test_agent_config_kimi_coding_probe(monkeypatch):
    from pping_lang.autopilot import api as ap_api

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"content":[{"type":"thinking","thinking":"hidden"},{"type":"text","text":"ok"}]}'

    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization")
        seen["ua"] = req.headers.get("User-agent")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(ap_api.urllib.request, "urlopen", fake_urlopen)
    out = ap_api.test_agent_config({
        "provider": "kimi_coding",
        "base_url": "https://api.kimi.com/coding/v1",
        "api_key": "secret",
        "model": "kimi-for-coding",
        "temperature": 0.6,
        "timeout_s": 9,
    })
    assert out["ok"] is True and out["provider"] == "kimi_coding"
    assert out["sample"] == "ok"
    assert seen["url"] == "https://api.kimi.com/coding/v1/messages"
    assert seen["auth"] == "Bearer secret"
    assert seen["ua"] == "KimiCLI/0.77"
    assert seen["body"]["model"] == "kimi-for-coding"
    assert seen["body"]["max_tokens"] == 32
    assert "temperature" not in seen["body"]
    assert seen["timeout"] == 9


def test_agent_config_anthropic_probe(monkeypatch):
    """agent-test 必须与 build_agent 的 ClaudeAgent 路由一致：/v1/messages + x-api-key。"""
    from pping_lang.autopilot import api as ap_api

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"content":[{"type":"text","text":"ok"}]}'

    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["key"] = req.headers.get("X-api-key")
        seen["version"] = req.headers.get("Anthropic-version")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return Resp()

    monkeypatch.setattr(ap_api.urllib.request, "urlopen", fake_urlopen)
    out = ap_api.test_agent_config({
        "provider": "anthropic",
        "api_key": "secret",                       # base_url 省略 → 用默认
        "model": "claude-opus-4",
    })
    assert out["ok"] is True and out["provider"] == "anthropic"
    assert out["sample"] == "ok"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["key"] == "secret"
    assert seen["version"] == "2023-06-01"
    assert seen["body"]["model"] == "claude-opus-4"


# ---- 真 LLM agent(mock HTTP)+ 兜底 ----

def _ctx(cands, bottleneck="A", config=None, tried=None):
    from pping_lang.autopilot.agent import AgentContext
    return AgentContext(
        objective={"target": "throughput"}, round=1, budget={"rounds_left": 5},
        current_config=config or {"max_num_seqs": 32, "gpu_memory_utilization": 0.70},
        diagnosis={"bottleneck": bottleneck, "evidence_refs": [f"{bottleneck}:roofline"]},
        candidates=cands, tried_configs=tried or [])


class _SSEResp:
    """假流式响应(stream:true 之后 _call() 逐行读 data: 帧,不再一次性 read())。
    events 是逐个要发的 dict,末尾自动补 [DONE]；context manager + 逐行可迭代都要支持。"""

    def __init__(self, events):
        lines = [f"data: {json.dumps(e)}\n".encode("utf-8") for e in events]
        lines.append(b"data: [DONE]\n")
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_build_messages_lang_directive_defaults_to_chinese():
    """不指定 lang 时默认中文指令,防止 LLM 跟着英文技术标识符默认答英文。"""
    from pping_lang.autopilot.agent import build_messages
    cands = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    system, _ = build_messages(_ctx(cands), lang="zh")
    assert "用中文回答" in system
    system_default, _ = build_messages(_ctx(cands))    # 不传 lang 参数也应默认中文
    assert "用中文回答" in system_default


def test_build_messages_lang_directive_english():
    from pping_lang.autopilot.agent import build_messages
    cands = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    system, _ = build_messages(_ctx(cands), lang="en")
    assert "Answer all free-text fields" in system and "用中文回答" not in system


def test_http_agent_invalid_lang_falls_back_to_zh():
    from pping_lang.autopilot.agent import OpenAIAgent
    a = OpenAIAgent("http://x/v1", "k", "m", lang="fr")   # 不支持的语言 → 回落中文,不炸
    assert a.lang == "zh"


def test_build_agent_propagates_lang_from_config():
    from pping_lang.autopilot.api import build_agent
    agent = build_agent({"provider": "anthropic", "api_key": "k", "model": "m", "lang": "en"})
    assert agent._primary.lang == "en"                    # ResilientAgent 包裹的真 agent
    agent_default = build_agent({"provider": "anthropic", "api_key": "k", "model": "m"})
    assert agent_default._primary.lang == "zh"             # 未传 lang → 默认中文


def test_run_cli_target_choices_include_cost():
    """--target choices 之前只有 throughput/latency,漏了 cost —— UI"性价比"按钮真跑
    一次会在参数解析这步直接 SystemExit(2)(argparse 拒绝非法 choice),会话还没起步就崩。
    用 --help 探测,不需要 docker/GPU 就能验证 choices 列表本身。"""
    import contextlib
    import io

    from pping_lang.autopilot import run as run_mod

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit) as exc_info:
        run_mod.main(["--help"])
    assert exc_info.value.code == 0
    help_text = buf.getvalue()
    assert "cost" in help_text                        # target choices 现在包含 cost
    assert "--latency-metric" in help_text             # 延迟优先"主看哪个指标"
    assert "--floor" in help_text                      # 延迟优先"吞吐硬下限"
    assert "--e2e" in help_text                         # E2E p99 SLA(agent/deadline 场景)


def test_run_cli_objective_carries_latency_metric_and_floor():
    """--latency-metric/--floor 得真的进 objective dict,不能只是接受了参数却没接到位。"""
    import argparse

    from pping_lang.autopilot import run as run_mod

    p = argparse.ArgumentParser()
    p.add_argument("--target", default="throughput", choices=["throughput", "latency", "cost"])
    p.add_argument("--ttft", type=float, default=None)
    p.add_argument("--tpot", type=float, default=None)
    p.add_argument("--latency-metric", default=None, choices=["ttft", "tpot"])
    p.add_argument("--floor", type=float, default=None)
    args = p.parse_args(["--target", "latency", "--ttft", "800", "--tpot", "30",
                         "--latency-metric", "ttft", "--floor", "500"])
    objective = {"target": args.target, "sla": {"ttft_p99_ms": args.ttft, "tpot_p99_ms": args.tpot}}
    if args.latency_metric:
        objective["latency_metric"] = args.latency_metric
    if args.floor is not None:
        objective["floor"] = {"output_tps": args.floor}
    spec = build_objective(objective)
    assert spec.latency_metric == "ttft" and spec.floor.output_tps == 500.0


def test_openai_agent_picks_candidate(monkeypatch):
    from pping_lang.autopilot.agent import OpenAIAgent
    a = OpenAIAgent("http://x/v1", "k", "m")
    monkeypatch.setattr(a, "_call", lambda *_a, **_k:
                        '{"done":false,"action":{"knob":"max_num_seqs","value":64},'
                        '"rationale":"提并发","evidence_refs":["A:roofline"]}')
    cands = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    d = a.propose(_ctx(cands))
    assert not d.done and d.knob == "max_num_seqs" and d.config["max_num_seqs"] == 64
    assert "A:roofline" in d.evidence_refs


def test_openai_agent_done(monkeypatch):
    from pping_lang.autopilot.agent import OpenAIAgent
    a = OpenAIAgent("http://x/v1", "k", "m")
    monkeypatch.setattr(a, "_call", lambda *_a, **_k: '{"done":true,"reason":"已近最优"}')
    d = a.propose(_ctx([], bottleneck="B"))
    assert d.done


def test_openai_agent_request_sets_generous_max_tokens(monkeypatch):
    """回归测试(真机复现 2026-07-23,ap-20260723-112536,glm-5.2):OpenAIAgent 之前请求体
    里没设 max_tokens,吃 provider 默认值——thinking 类模型想得越久(超时拉到 240s + 流式
    之后敢无限想下去,不再被网络超时先打断)越容易把默认额度吃满,text 被截断成非法 JSON
    (第一次调用报 JSONDecodeError,靠运气重试第二次才成功)。给够预算才是根治,不能靠
    重试撞运气。"""
    import urllib.request

    from pping_lang.autopilot.agent import OpenAIAgent

    seen = {}

    def fake_urlopen(req, timeout):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _SSEResp([{"choices": [{"delta": {"content": '{"done":true,"reason":"ok"}'}}]}])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    a = OpenAIAgent("http://x/v1", "k", "m")
    a.propose(_ctx([], bottleneck="B"))
    assert seen["body"]["max_tokens"] >= 8192


def test_claude_agent_request_sets_generous_max_tokens(monkeypatch):
    """同上一条:ClaudeAgent 原本 max_tokens=1024,同类 provider 里明显偏小,一并抬高
    避免 thinking+text 合计被截断。"""
    import urllib.request

    from pping_lang.autopilot.agent import ClaudeAgent

    seen = {}

    def fake_urlopen(req, timeout):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _SSEResp([{"delta": {"type": "text_delta", "text": '{"done":true,"reason":"ok"}'}}])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    a = ClaudeAgent(api_key="k")
    a.propose(_ctx([], bottleneck="B"))
    assert seen["body"]["max_tokens"] >= 8192


def test_kimi_agent_captures_thinking(monkeypatch):
    """思考过程直播：provider 流式响应里的 thinking_delta 随决策带出(dec.thinking)。"""
    from pping_lang.autopilot.agent import KimiCodingAgent

    events = [
        {"type": "content_block_delta",
         "delta": {"type": "thinking_delta", "thinking": "waiting=28 说明需求远超准入闸,一步到位"}},
        {"type": "content_block_delta",
         "delta": {"type": "text_delta", "text": '{"done":false,"action":{"knob":"max_num_seqs",'
                                                  '"value":32},"rationale":"提并发",'
                                                  '"evidence_refs":["A:live"]}'}},
    ]

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _SSEResp(events))
    a = KimiCodingAgent(api_key="k")
    cands = propose_candidates("A", {"max_num_seqs": 4, "gpu_memory_utilization": 0.70})
    d = a.propose(_ctx(cands, config={"max_num_seqs": 4}))
    assert d.knob == "max_num_seqs" and d.to_val == 32
    assert "一步到位" in d.thinking


def test_http_agent_propose_stops_promptly_without_waiting_for_call_to_finish(monkeypatch):
    """真机复现(2026-07-22):agent 调用单次能拖到 90s+,用户点停止后进程卡在阻塞调用里对
    SIGINT 反应不及时,10s 优雅期不够被 SIGKILL 强杀。_HTTPAgent.propose() 现在把 _call 放
    进守护线程轮询(0.5s 粒度),stop_check 一旦为真就该在秒级内跳出,不必等 _call 真正返回。"""
    import threading
    import time as _time

    from pping_lang.autopilot.agent import AgentStopRequested, OpenAIAgent

    call_finished = threading.Event()

    def _slow_call(_system, _user):
        _time.sleep(5.0)          # 远大于 stop_check 该生效的秒级窗口
        call_finished.set()
        return '{"done":true,"reason":"不该跑到这"}'

    a = OpenAIAgent("http://x/v1", "k", "m")
    monkeypatch.setattr(a, "_call", _slow_call)
    stop_after = _time.monotonic() + 0.6
    a.set_stop_check(lambda: _time.monotonic() >= stop_after)

    t0 = _time.monotonic()
    with pytest.raises(AgentStopRequested):
        a.propose(_ctx([], bottleneck="B"))
    elapsed = _time.monotonic() - t0

    assert elapsed < 2.0                 # 秒级跳出,不是等满 5s 阻塞调用
    assert not call_finished.is_set()    # 弃置的调用线程还没跑完(是守护线程,不阻塞退出)


def _serve_sse_chunks(chunks, gap_s):
    """起一个真实本地 HTTP server,按 gap_s 间隔逐块推 SSE 帧;返回 (server, thread, port)。
    调用方负责 server.shutdown() + thread.join()。用真 socket 而不是 mock,才能真正验证
    urllib 的 socket timeout 是"两块数据之间的间隔"而不是"整次调用的总时长"。"""
    import http.server
    import threading as _threading
    import time as _time

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode("utf-8"))
                self.wfile.flush()
                _time.sleep(gap_s)
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *_a):  # noqa: ANN001 — 测试不需要 access log 噪声
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = _threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, server.server_address[1]


def test_http_agent_streaming_idle_timeout_survives_slow_total_time(monkeypatch):
    """彻底根治的核心验证(2026-07-23,真实 socket 而非 mock):流式之后 timeout_s 的语义
    变成"两块数据之间的最大间隔",不是"整次调用的总时长"。3 个数据块之间各间隔 0.5s,
    总耗时 1.5s+ > timeout_s=0.8s,但因为每次间隔都 < 0.8s,应该正常拿到完整响应而不是
    超时——这正是要修的问题:旧版非流式一次性 resp.read() 必须在 timeout_s 内拿到全部
    内容,thinking 慢一点整次就超时,重试也没用(真机 glm-5.2 卡在 90s 超时线上 10 次)。"""
    from pping_lang.autopilot.agent import OpenAIAgent

    chunks = [
        {"choices": [{"delta": {"reasoning_content": "先想第一步…"}}]},
        {"choices": [{"delta": {"reasoning_content": "再想第二步…"}}]},
        {"choices": [{"delta": {"content": '{"done":true,"reason":"想清楚了"}'}}]},
    ]
    server, thread, port = _serve_sse_chunks(chunks, gap_s=0.5)
    try:
        a = OpenAIAgent(f"http://127.0.0.1:{port}", "k", "m", timeout_s=0.8)
        t0 = __import__("time").monotonic()
        d = a.propose(_ctx([], bottleneck="B"))
        elapsed = __import__("time").monotonic() - t0
        assert d.done and d.reason == "想清楚了"
        assert elapsed > 1.0                 # 总耗时确实超过了单次 timeout_s(0.8s)
        assert "先想第一步" in d.thinking and "再想第二步" in d.thinking
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_agent_call_stops_mid_stream_via_real_socket(monkeypatch):
    """_iter_sse_data 在真实流式连接的数据块之间也会查 _should_stop()(不只是外层线程
    轮询兜底)——用真 socket 起一个会一直吐数据块的 server,stop_check 在收到第 2 块后
    变真,验证 _call() 能在拿到完整响应前就主动跳出,而不是把它当普通异常重试。"""
    from pping_lang.autopilot.agent import AgentStopRequested, OpenAIAgent

    # 数据块管够(server 端不知道客户端会提前挂断),数量远超测试需要的"第 2 块后停"
    chunks = [{"choices": [{"delta": {"reasoning_content": f"第{i}块…"}}]} for i in range(20)]
    server, thread, port = _serve_sse_chunks(chunks, gap_s=0.2)
    try:
        a = OpenAIAgent(f"http://127.0.0.1:{port}", "k", "m", timeout_s=5.0)
        seen = {"n": 0}
        orig_iter = a._iter_sse_data

        def _counting_stop_check():
            return seen["n"] >= 2

        a.set_stop_check(_counting_stop_check)

        def _patched_iter(resp):
            for evt in orig_iter(resp):
                seen["n"] += 1
                yield evt

        monkeypatch.setattr(a, "_iter_sse_data", _patched_iter)
        with pytest.raises(AgentStopRequested):
            a.propose(_ctx([], bottleneck="B"))
        assert seen["n"] < len(chunks)        # 提前跳出,没有把 20 块全读完
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_runner_persists_thinking_and_streams_decision(tmp_path):
    """思考落 round 记录(agent_thinking)+ 决策到达即刻直播(不等 bench 落定)。"""
    from pping_lang.autopilot.agent import AgentDecision

    class ThinkingAgent:
        model = "thinker"

        def propose(self, ctx):
            cand = next(c for c in ctx.candidates if c["knob"] == "max_num_seqs")
            return AgentDecision(knob="max_num_seqs", config=cand["config"],
                                 from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                 rationale="提并发摊薄权重搬运",
                                 thinking="MFU 双低且 KV 富余,先动准入闸")

    store = SessionStore(tmp_path / "think.jsonl")
    store.new_session("ap-think", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store, sandbox=SimSandbox("M"), agent=ThinkingAgent(), obj=OBJ,
           budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0).run()
    st = store.status_dict()
    cand = [r for r in st["rounds"] if r["kind"] == "candidate"][0]
    assert "先动准入闸" in cand["agent_thinking"]
    msgs = [e["message"] for e in st["events"]]
    assert any(m.startswith("agent 思考：") for m in msgs)
    assert any(m.startswith("agent 决策：max_num_seqs") for m in msgs)
    store.close()


def test_resilient_agent_falls_back_to_stub():
    from pping_lang.autopilot.agent import ResilientAgent

    class Boom:
        model = "boom"

        def propose(self, ctx):
            raise RuntimeError("net down")

    r = ResilientAgent(Boom(), StubAgent(), retries=1)
    cands = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    d = r.propose(_ctx(cands))
    assert d.knob == "max_num_seqs" and "兜底" in d.rationale     # 启发式接管,透明标注


def test_resilient_agent_propagates_stop_without_falling_back():
    """用户手动停止不是"调用失败"——ResilientAgent 不该吞掉 AgentStopRequested 去重试或
    退回 StubAgent 硬跑一次启发式决策,那样多余且延误(见 agent.py AgentStopRequested 文档)。"""
    from pping_lang.autopilot.agent import AgentStopRequested, ResilientAgent

    fallback_calls = []

    class StoppedPrimary:
        model = "stopped-primary"

        def propose(self, ctx):
            raise AgentStopRequested("用户手动停止")

    class TrackingFallback:
        model = "tracking-fallback"

        def propose(self, ctx):
            fallback_calls.append(ctx)
            return StubAgent().propose(ctx)

    r = ResilientAgent(StoppedPrimary(), TrackingFallback(), retries=2)
    cands = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    with pytest.raises(AgentStopRequested):
        r.propose(_ctx(cands))
    assert not fallback_calls              # 没有重试,也没有退回兜底


def test_validate_rejects_offmenu_and_repeat():
    from pping_lang.autopilot.agent import AgentDecision, config_hash, validate
    cands = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    cfg = next(c["config"] for c in cands if c["knob"] == "max_num_seqs")
    ctx = _ctx(cands, tried=[{"hash": config_hash(cfg), "decision": "reverted"}])
    assert validate(AgentDecision(knob="nonexistent_knob"), ctx)        # 不在候选集 → 拒
    assert validate(AgentDecision(knob="max_num_seqs", config=cfg), ctx)  # 已试 reverted → 防重拒
    assert validate(AgentDecision(done=True), ctx) is None              # done 合法


def test_validate_recovery_action():
    from pping_lang.autopilot.agent import AgentContext, AgentDecision, validate
    ctx = AgentContext(
        objective={"target": "throughput"}, round=0, budget={"rounds_left": 12},
        current_config={"max_num_seqs": 64}, diagnosis={}, candidates=[],
        recovery_mode=True,
        failure_context={"error": "500", "tried_recovery_actions": []})
    assert validate(AgentDecision(recovery_action="lower_bench_concurrency"), ctx) is None
    assert validate(AgentDecision(recovery_action="bad_action"), ctx) is not None
    assert validate(AgentDecision(), ctx) is not None


def test_extract_last_json_object_skips_braces_mentioned_in_prose():
    """回归测试(真机复现 2026-07-23,ap-20260723-120120,自定义 provider):有的模型不把
    thinking 单独放进 reasoning_content,连同最终 JSON 一起算进普通 content 流,思考文字
    提到"配置形如 {...}"这类非 JSON 花括号很常见。朴素的 find('{')/rfind('}') 会把思考
    文字里的花括号也截进来,做出非法 JSON——真正的平衡扫描应该跳过这些,只取最后一个
    完整闭合的顶层块。"""
    from pping_lang.autopilot.agent import _extract_last_json_object

    text = ('让我想想,配置形如 {key: value} 这样,但那只是举例,不是最终答案。\n'
            '最终答案:\n{"done": false, "action": {"knob": "max_num_seqs", "value": 96}, '
            '"rationale": "带宽瓶颈提并发"}')
    out = json.loads(_extract_last_json_object(text))
    assert out["action"]["knob"] == "max_num_seqs" and out["action"]["value"] == 96


def test_extract_last_json_object_ignores_braces_inside_string_values():
    """JSON 字符串值内部提到的花括号(如 rationale 里写"配置 {a:1}")不该被误判成
    结构边界,深度扫描要跳过字符串字面量。"""
    from pping_lang.autopilot.agent import _extract_last_json_object

    text = '{"done": true, "reason": "配置形如 {a:1} 时不用再动", "rationale": "已近最优"}'
    out = json.loads(_extract_last_json_object(text))
    assert out["done"] is True and out["reason"] == "配置形如 {a:1} 时不用再动"


def test_extract_last_json_object_picks_last_block_when_multiple_present():
    """模型有时会先给一版草稿 JSON 又推翻重写,最后一个完整闭合块才是真正想要的答案。"""
    from pping_lang.autopilot.agent import _extract_last_json_object

    text = ('草稿:{"done": false, "action": {"knob": "wrong_knob", "value": 1}}\n'
            '不对,重新想。\n'
            '{"done": false, "action": {"knob": "max_num_seqs", "value": 128}}')
    out = json.loads(_extract_last_json_object(text))
    assert out["action"]["knob"] == "max_num_seqs"


def test_extract_last_json_object_falls_back_on_truncated_response():
    """真截断(没有任何闭合的顶层块)时退回朴素启发式,行为跟以前一致——不该在这种情况下
    伪造出一个"看似合法"但实际残缺的边界,让 json.loads 该报错就报错(交给上层重试)。"""
    from pping_lang.autopilot.agent import _extract_last_json_object

    text = '{"done": false, "action": {"knob": "max_num_seqs", "value'   # 硬生生截断
    with pytest.raises(json.JSONDecodeError):
        json.loads(_extract_last_json_object(text))


def test_decision_from_json_recovery_mode():
    from pping_lang.autopilot.agent import AgentContext, _decision_from_json
    ctx = AgentContext(
        objective={"target": "throughput"}, round=0, budget={"rounds_left": 12},
        current_config={"max_num_seqs": 64}, diagnosis={}, candidates=[],
        recovery_mode=True, failure_context={})
    dec = _decision_from_json({"recovery_action": "raise_gpu_memory_utilization",
                               "rationale": "扩 KV 池", "expected_effect": "缓解 OOM"}, ctx)
    assert dec.recovery_action == "raise_gpu_memory_utilization"
    assert dec.rationale == "扩 KV 池"


def test_build_messages_recovery_mode():
    from pping_lang.autopilot.agent import AgentContext, build_messages
    ctx = AgentContext(
        objective={"target": "throughput"}, round=0, budget={"rounds_left": 12},
        current_config={"max_num_seqs": 64}, diagnosis={"bench_plan": {"concurrency": 32}},
        candidates=[], recovery_mode=True,
        failure_context={"error": "500 Internal Server Error", "error_type": "APIServer500"})
    system, user = build_messages(ctx)
    assert "recovery 模式" in system
    assert "raise_gpu_memory_utilization" in system
    assert "500 Internal Server Error" in user


def test_stub_agent_recovery_chooses_action_by_error_type():
    from pping_lang.autopilot.agent import AgentContext, StubAgent
    agent = StubAgent()
    ctx_oom = AgentContext(
        objective={"target": "throughput"}, round=0, budget={"rounds_left": 12},
        current_config={"max_num_seqs": 64}, diagnosis={"bench_plan": {}}, candidates=[],
        recovery_mode=True, failure_context={"error": "CUDA out of memory"})
    assert agent.propose(ctx_oom).recovery_action == "raise_gpu_memory_utilization"
    ctx_500 = AgentContext(
        objective={"target": "throughput"}, round=0, budget={"rounds_left": 12},
        current_config={"max_num_seqs": 64}, diagnosis={"bench_plan": {}}, candidates=[],
        recovery_mode=True, failure_context={"error": "500 Internal Server Error"})
    assert agent.propose(ctx_500).recovery_action == "lower_bench_concurrency"


def test_build_agent_selects_claude_openai_stub():
    from pping_lang.autopilot.agent import ClaudeAgent, KimiCodingAgent, OpenAIAgent, ResilientAgent
    from pping_lang.autopilot.api import build_agent
    assert isinstance(build_agent(None), StubAgent)
    assert isinstance(build_agent({"base_url": "http://x/v1", "model": "m"}), StubAgent)  # 没 key
    a = build_agent({"base_url": "http://x/v1", "api_key": "k", "model": "m"})
    assert isinstance(a, ResilientAgent) and isinstance(a._primary, OpenAIAgent)
    c = build_agent({"provider": "anthropic", "api_key": "k", "model": "claude-opus-4"})  # G3 默认 Claude
    assert isinstance(c, ResilientAgent) and isinstance(c._primary, ClaudeAgent)
    k = build_agent({"provider": "kimi_coding", "base_url": "https://api.kimi.com/coding/v1",
                     "api_key": "k", "model": "kimi-for-coding"})
    assert isinstance(k, ResilientAgent) and isinstance(k._primary, KimiCodingAgent)


# ---- DockerSandbox(真沙盒;mock docker / bench,无 GPU)----

def _fake_proc(returncode=0, stdout="", stderr=""):
    return type("R", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


def test_clean_log_line_strips_layered_prefixes():
    """就绪心跳日志清理：pid 前缀 + INFO/时间戳/文件名前缀常叠两层,须都剥掉。"""
    from pping_lang.autopilot.sandbox import _clean_log_line
    raw = "(EngineCore pid=206) INFO 07-08 23:35:42 [monitor.py:53] torch.compile took 12.3s"
    assert _clean_log_line(raw) == "torch.compile took 12.3s"
    assert _clean_log_line("INFO 07-08 23:26:47 [api_server.py:1] hello") == "hello"
    assert _clean_log_line("no prefix here") == "no prefix here"


def test_logs_tail_interesting_filters_noise(monkeypatch):
    """噪声行(环境变量警告)被过滤;真正标志进度的行(加载/CUDA graph)被选中并清理。"""
    from pping_lang.autopilot.sandbox import DockerSandbox
    sb = DockerSandbox("M", "img:dev")
    lines = "\n".join([
        "INFO 07-08 23:44:41 [envs.py:1866] Unknown vLLM environment variable detected: FOO",
        "(APIServer pid=1) INFO 07-08 23:44:59 [gpu_model_runner.py:4959] Model loading took 5.29 GiB",
        "INFO 07-08 23:45:01 [server.py:9] some other harmless line",
    ])
    monkeypatch.setattr(sb, "_docker", lambda *a: _fake_proc(0, lines))
    assert sb._logs_tail_interesting() == "Model loading took 5.29 GiB"


def test_logs_tail_interesting_none_when_nothing_matches(monkeypatch):
    from pping_lang.autopilot.sandbox import DockerSandbox
    sb = DockerSandbox("M", "img:dev")
    monkeypatch.setattr(sb, "_docker", lambda *a: _fake_proc(0, "just noise\nmore noise"))
    assert sb._logs_tail_interesting() is None


def test_docker_sandbox_builds_run_command(monkeypatch):
    import subprocess
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args) or _fake_proc(0, "cid"))
    from pping_lang.autopilot.sandbox import DockerSandbox
    sb = DockerSandbox("M", "img:dev", port=8011, gpus="all", container="ap-c",
                       env={"HF_TOKEN": "x"}, volumes=("/m:/m",))
    monkeypatch.setattr(sb, "_wait_ready", lambda: None)
    sb.apply({"max_num_seqs": 64, "gpu_memory_utilization": 0.8})
    run = next(c for c in calls if "run" in c)
    assert "--gpus" in run and "all" in run and "8011:8000" in run and "img:dev" in run
    assert "--max-num-seqs" in run and "64" in run        # config → flags
    assert "-e" in run and "HF_TOKEN=x" in run and "/m:/m" in run


def test_docker_sandbox_cmd_template_overrides_entrypoint(monkeypatch):
    import subprocess
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args) or _fake_proc(0, "cid"))
    from pping_lang.autopilot.sandbox import DockerSandbox
    sb = DockerSandbox(
        "Qwen/X", "pping:dev", port=8011, internal_port=8000, container="ap-c",
        entrypoint="/bin/bash", cap_add=("SYS_ADMIN",), volumes=("/models:/models",),
        env={"HF_HOME": "/models"},
        cmd_template="pping-serve-entry '{model}' --host 0.0.0.0 --port {port} {flags}")
    monkeypatch.setattr(sb, "_wait_ready", lambda: None)
    sb.apply({"max_num_seqs": 64, "gpu_memory_utilization": 0.8})
    run = next(c for c in calls if "run" in c)
    assert "--entrypoint" in run and "/bin/bash" in run and "8011:8000" in run
    assert "--cap-add" in run and "SYS_ADMIN" in run and "/models:/models" in run
    shell = run[run.index("-c") + 1]                     # entrypoint bash -c "<shell>"
    assert "pping-serve-entry 'Qwen/X'" in shell and "--port 8000" in shell
    assert "--enforce-eager" not in shell
    assert shell.rstrip().endswith("--max-num-seqs 64 --gpu-memory-utilization 0.8")


def test_docker_sandbox_launch_error_on_exit(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _fake_proc(0))
    from pping_lang.autopilot.sandbox import DockerSandbox, LaunchError
    sb = DockerSandbox("M", "img", ready_timeout_s=0.05, poll_s=0.01)
    monkeypatch.setattr(sb, "_alive", lambda: False)      # 容器立刻退出
    monkeypatch.setattr(sb, "_logs_tail", lambda n=40: "torch CUDA out of memory")
    with pytest.raises(LaunchError, match="memory|起不来"):
        sb.apply({"max_num_seqs": 64})


def test_docker_sandbox_teardown_verifies_ports(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _fake_proc(0))
    import pping_lang.autopilot.sandbox as sbmod
    from pping_lang.autopilot.sandbox import DockerSandbox, TeardownError
    monkeypatch.setattr(sbmod, "GPU_FREE_TIMEOUT_S", 0.01)
    sb = DockerSandbox("M", "img", port=8011, ready_timeout_s=0.01)
    monkeypatch.setattr(sb, "_alive", lambda: False)
    monkeypatch.setattr(sb, "_port_open", lambda p: True)
    monkeypatch.setattr(sb, "_gpu_used_mib", lambda: None)
    with pytest.raises(TeardownError, match="open_ports"):
        sb.teardown()


def test_docker_sandbox_gpu_probe_missing_is_optional(monkeypatch):
    import subprocess
    from pping_lang.autopilot.sandbox import DockerSandbox

    def missing(*a, **k):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(subprocess, "run", missing)
    assert DockerSandbox("M", "img")._gpu_used_mib() is None


def test_bench_scorecard_maps_summary(monkeypatch):
    import pping_lang.bench.runner as br
    from pping_lang.bench.measurement import LatencyStats, RunSummary
    rs = RunSummary(total=120, ok=120, errors=0, duration_s=30,
                    ttft_ms=LatencyStats(p99=420.0), tpot_ms=LatencyStats(p99=18.0),
                    e2e_ms=LatencyStats(p99=2200.0), output_throughput_tps=1907.0)
    seen = {}

    async def fake(scen, client, **kw):
        seen["prompt_tokens"] = scen.prompt_tokens
        seen["output_tokens"] = scen.output_tokens
        seen["prompt_source"] = scen.prompt_source
        return rs

    monkeypatch.setattr(br, "run_static", fake)
    from pping_lang.autopilot.sandbox import BENCH_SPEC, bench_scorecard
    spec = {**BENCH_SPEC, "prompt_source": "synthetic", "prompt_tokens": 2048,
            "output_tokens": 64}
    sc = bench_scorecard("http://127.0.0.1:9/v1", "M", spec, {"max_num_seqs": 64})
    assert sc.output_tps == 1907.0 and sc.ttft_p99_ms == 420 and sc.tpot_p99_ms == 18.0
    assert sc.error_rate == 0 and sc.run_meta["sim"] is False and sc.run_meta["max_num_seqs"] == 64
    assert seen == {"prompt_source": "synthetic", "prompt_tokens": 2048, "output_tokens": 64}
    assert sc.run_meta["prompt_tokens"] == 2048 and sc.run_meta["output_tokens"] == 64


def test_bench_scorecard_zero_ok_raises(monkeypatch):
    import pping_lang.bench.runner as br
    from pping_lang.bench.measurement import RunSummary

    async def fake(scen, client, **kw):
        return RunSummary(total=10, ok=0, errors=10)

    monkeypatch.setattr(br, "run_static", fake)
    from pping_lang.autopilot.sandbox import BENCH_SPEC, BenchError, bench_scorecard
    with pytest.raises(BenchError):
        bench_scorecard("http://127.0.0.1:9/v1", "M", BENCH_SPEC)


def test_bench_scorecard_scales_client_pool_to_concurrency(monkeypatch):
    import pping_lang.bench.client as bc
    import pping_lang.bench.runner as br
    from pping_lang.bench.measurement import LatencyStats, RunSummary
    seen = {}

    class FakeClient:
        def __init__(self, endpoint, *, timeout_s, max_keepalive, **kwargs):
            seen["max_keepalive"] = max_keepalive
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None

    async def fake(scen, client, **kw):
        return RunSummary(total=1, ok=1, errors=0, duration_s=1,
                          ttft_ms=LatencyStats(p99=1.0),
                          tpot_ms=LatencyStats(p99=1.0),
                          e2e_ms=LatencyStats(p99=1.0),
                          output_throughput_tps=1.0)

    monkeypatch.setattr(bc, "OpenAIStreamClient", FakeClient)
    monkeypatch.setattr(br, "run_static", fake)
    from pping_lang.autopilot.sandbox import BENCH_SPEC, bench_scorecard
    bench_scorecard("http://127.0.0.1:9/v1", "M", {**BENCH_SPEC, "concurrency": 256})
    assert seen["max_keepalive"] == 256


# ---- ③ 真诊断：读候选 /api/diagnoses → bottleneck ----

def test_docker_sandbox_read_diagnosis_picks_most_severe(monkeypatch):
    from pping_lang.autopilot.sandbox import DockerSandbox
    sb = DockerSandbox("M", "img", dash_port=8013)
    payload = {"window_seconds": 120, "diagnoses": [
        {"rule_id": "A", "severity": "info", "message": "双低", "ts_ns": 1, "context": {"mfu": 0.1}},
        {"rule_id": "D", "severity": "critical", "message": "容量瓶颈", "ts_ns": 2, "context": {"kv_pressure": 0.95}}]}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    d = sb.read_diagnosis()
    assert d["bottleneck"] == "D" and d["source"] == "live:/api/diagnoses"   # 取 critical
    assert any("kv_pressure" in e for e in d["evidence_refs"])


def test_docker_sandbox_read_diagnosis_none_paths(monkeypatch):
    from pping_lang.autopilot.sandbox import DockerSandbox
    assert DockerSandbox("M", "img").read_diagnosis() is None                # 没 dash_port
    sb = DockerSandbox("M", "img", dash_port=8013)
    import urllib.request

    class _Empty:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"diagnoses": []}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Empty())
    assert sb.read_diagnosis() is None                                       # 没命中诊断


def test_docker_sandbox_runtime_probe_filters_bench_window(monkeypatch):
    from pping_lang.autopilot.sandbox import DockerSandbox
    sb = DockerSandbox("M", "img", dash_port=8013)
    start, end = 1_000_000_000, 4_000_000_000

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"points": [
                {"ts_ns": start - 1, "value": 0.1},
                {"ts_ns": start, "value": 0.2},
                {"ts_ns": start + 1_000_000_000, "value": 0.9},
                {"ts_ns": end + 1, "value": 0.3},
            ]}).encode()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    probe = sb._runtime_probe(start, end)
    assert probe["kv_cache_usage"]["max"] == 0.9
    assert probe["kv_cache_usage"]["last"] == 0.9
    assert probe["kv_cache_usage"]["n"] == 2
    assert round(probe["kv_cache_usage"]["sum"], 1) == 1.1


def test_docker_sandbox_effective_config_from_info(monkeypatch):
    from pping_lang.autopilot.sandbox import DockerSandbox
    sb = DockerSandbox("M", "img", dash_port=8013)
    sb._cfg = {"max_num_seqs": 64}
    monkeypatch.setattr(sb, "_read_info", lambda: {"resolved_config": {
        "scheduler_config": {"max_num_seqs": 128, "max_num_batched_tokens": 4096,
                             "enable_chunked_prefill": True},
        "cache_config": {"gpu_memory_utilization": 0.9, "cache_dtype": "auto"},
        "model_config": {"max_model_len": 2048},
    }})
    eff = sb.effective_config()
    assert eff["max_num_seqs"] == 128
    assert eff["gpu_memory_utilization"] == 0.9
    assert eff["max_model_len"] == 2048


def test_runner_observe_prefers_live_diagnosis(tmp_path):
    # baseline 的 scorecard 带 run_meta['diagnosis'] → observe 用真诊断(D),不走 baseline 启发式(A)
    import pytest as _pytest  # noqa: F401
    from pping_lang.autopilot.agent import AgentDecision
    from pping_lang.autopilot.objective import Scorecard
    seen = []

    class DiagSandbox:
        def apply(self, cfg): ...
        def measure(self, obj):
            return Scorecard(output_tps=1000, ttft_p99_ms=100, tpot_p99_ms=10,
                             run_meta={"diagnosis": {"bottleneck": "D", "evidence_refs": ["D:live"],
                                                     "metrics": {}}})
        def teardown(self): ...

    class SpyAgent:
        model = "spy"

        def propose(self, ctx):
            seen.append(ctx.diagnosis.get("bottleneck"))
            return AgentDecision(done=True, rationale="stop")               # 捕获 bottleneck 即停

    store = SessionStore(tmp_path / "d.jsonl")
    store.new_session("ap-d", {"target": "throughput"}, {"rounds": 2})
    Runner(store=store, sandbox=DiagSandbox(), agent=SpyAgent(), obj=OBJ,
           budget={"rounds": 2, "seconds": 900}, model="M", step_delay_s=0.0).run()
    assert seen and seen[0] == "D"                                          # 用了真诊断
    store.close()


def test_runner_candidate_context_includes_p0_kvfit(tmp_path):
    from pping_lang.autopilot.agent import AgentDecision
    seen = []

    class SpyAgent:
        model = "spy"

        def propose(self, ctx):
            seen.extend(ctx.candidates)
            return AgentDecision(done=True, rationale="stop")

    store = SessionStore(tmp_path / "p0.jsonl")
    store.new_session("ap-p0", {"target": "throughput"}, {"rounds": 2})
    Runner(store=store, sandbox=SimSandbox("M"), agent=SpyAgent(), obj=OBJ,
           budget={"rounds": 2, "seconds": 900}, model="M", step_delay_s=0.0).run()
    assert seen
    assert all("p0" in c for c in seen)
    assert any(c["p0"]["verdict"] in ("allow", "warn", "unknown") for c in seen)
    store.close()


def test_runner_records_p0_pruned_candidates_in_diagnosis(tmp_path):
    from pping_lang.autopilot.agent import AgentDecision

    class CapacitySandbox(SimSandbox):
        def measure(self, obj):
            # KV 半满(D 守卫按实测余量 0.5 放行大 batch),但并发翻倍在解析上装不下 → P0 剪
            return Scorecard(output_tps=1000, ttft_p99_ms=100, tpot_p99_ms=10,
                             run_meta={"prompt_tokens": 7500, "output_tokens": 16,
                                       "diagnosis": {"bottleneck": "A", "evidence_refs": ["A:live"],
                                                     "metrics": {}},
                                       "runtime_probe": {
                                           "kv_cache_usage": {"max": 0.5},
                                           "running_reqs": {"max": 160},
                                           # waiting>0 → 准入闸绑定(KV 装不下在排队),
                                           # max_num_seqs↑ 过 load 守卫,由 P0 KV-fit 解析剪掉
                                           "waiting_reqs": {"max": 2},
                                       }})

    class StopAgent:
        model = "stop"

        def propose(self, ctx):
            return AgentDecision(done=True, rationale="stop")

    store = SessionStore(tmp_path / "p0-pruned.jsonl")
    store.new_session("ap-p0-pruned", {"target": "throughput"}, {"rounds": 2})
    Runner(store=store, sandbox=CapacitySandbox("M"), agent=StopAgent(), obj=OBJ,
           budget={"rounds": 2, "seconds": 900}, model="M", step_delay_s=0.0,
           baseline_config={"max_num_seqs": 512, "gpu_memory_utilization": 0.5}).run()
    stop = next(r for r in store.status_dict()["rounds"] if r["kind"] == "stop")
    p0 = stop["diagnosis"]["p0_kvfit"]
    assert p0["pruned"] >= 1
    assert any(c["p0"]["verdict"] == "reject" for c in p0["pruned_candidates"])
    store.close()


def test_runner_uses_effective_config_but_applies_tracked_patch(tmp_path):
    from pping_lang.autopilot.agent import AgentDecision
    applied = []

    class EffectiveSandbox(SimSandbox):
        def effective_config(self):
            return {"max_num_seqs": 32, "gpu_memory_utilization": 0.70,
                    "enable_chunked_prefill": True}

        def apply(self, cfg):
            applied.append(dict(cfg))
            super().apply(cfg)

    class OneShotAgent:
        model = "oneshot"

        def propose(self, ctx):
            assert ctx.current_config["enable_chunked_prefill"] is True
            cand = next(c for c in ctx.candidates if c["knob"] == "max_num_batched_tokens")
            return AgentDecision(knob="max_num_batched_tokens", config=cand["config"],
                                 from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                 rationale="use live config")

    store = SessionStore(tmp_path / "effective.jsonl")
    store.new_session("ap-effective", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store, sandbox=EffectiveSandbox("M"), agent=OneShotAgent(), obj=OBJ,
           budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0).run()
    # Candidate apply should not replay live default booleans into command/config.
    assert any("max_num_batched_tokens" in cfg and "enable_chunked_prefill" not in cfg
               for cfg in applied)
    store.close()


def test_runner_t2_equivalence_failure_reverts_before_bench(tmp_path):
    from pping_lang.autopilot.agent import AgentDecision

    class DriftSandbox(SimSandbox):
        def sample_outputs(self, prompts=None):
            return ["changed"] if self._cfg.get("kv_cache_dtype") == "fp8" else ["gold"]

    class PickKvAgent:
        model = "pick-kv"

        def propose(self, ctx):
            cand = next(c for c in ctx.candidates if c["knob"] == "kv_cache_dtype")
            return AgentDecision(knob="kv_cache_dtype", config=cand["config"],
                                 from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                 rationale="try fp8 kv")

    store = SessionStore(tmp_path / "eq-fail.jsonl")
    store.new_session("ap-eq-fail", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store, sandbox=DriftSandbox("M"), agent=PickKvAgent(), obj=OBJ,
           budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0,
           baseline_config={"max_num_seqs": 128, "gpu_memory_utilization": 0.70},
           quality_gate=True).run()
    cand = [r for r in store.status_dict()["rounds"] if r["kind"] == "candidate"][0]
    assert cand["decision"] == "reverted"
    assert cand["scorecard_after"] is None
    assert "equivalence check failed" in cand["rationale"]
    store.close()


def test_runner_t2_equivalence_passes_and_benches(tmp_path):
    from pping_lang.autopilot.agent import AgentDecision

    class StableSandbox(SimSandbox):
        def sample_outputs(self, prompts=None):
            return ["gold"]

    class PickKvAgent:
        model = "pick-kv"

        def propose(self, ctx):
            cand = next(c for c in ctx.candidates if c["knob"] == "kv_cache_dtype")
            return AgentDecision(knob="kv_cache_dtype", config=cand["config"],
                                 from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                 rationale="try fp8 kv")

    store = SessionStore(tmp_path / "eq-pass.jsonl")
    store.new_session("ap-eq-pass", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store, sandbox=StableSandbox("M"), agent=PickKvAgent(), obj=OBJ,
           budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0,
           baseline_config={"max_num_seqs": 128, "gpu_memory_utilization": 0.70},
           quality_gate=True).run()
    cand = [r for r in store.status_dict()["rounds"] if r["kind"] == "candidate"][0]
    assert cand["scorecard_after"] is not None
    assert cand["decision"] in ("kept", "tie")
    store.close()


def test_runner_t2_equivalence_tolerates_minor_drift(tmp_path):
    """serving 非 seed 决定性(§6.2)：个别 token 漂移不应误杀 T2 候选,相似度阈值放行。"""
    from pping_lang.autopilot.agent import AgentDecision

    class MinorDriftSandbox(SimSandbox):
        def sample_outputs(self, prompts=None):
            if self._cfg.get("kv_cache_dtype") == "fp8":
                return ["The answer to 2 + 2 is 4, a basic arithmetic fact"]
            return ["The answer to 2 + 2 is 4, a basic arithmetic fact."]

    class PickKvAgent:
        model = "pick-kv"

        def propose(self, ctx):
            cand = next(c for c in ctx.candidates if c["knob"] == "kv_cache_dtype")
            return AgentDecision(knob="kv_cache_dtype", config=cand["config"],
                                 from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                 rationale="try fp8 kv")

    store = SessionStore(tmp_path / "eq-drift.jsonl")
    store.new_session("ap-eq-drift", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store, sandbox=MinorDriftSandbox("M"), agent=PickKvAgent(), obj=OBJ,
           budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0,
           baseline_config={"max_num_seqs": 128, "gpu_memory_utilization": 0.70},
           quality_gate=True).run()
    cand = [r for r in store.status_dict()["rounds"] if r["kind"] == "candidate"][0]
    assert cand["scorecard_after"] is not None          # 没被等价检查拦下,进了 bench
    assert "equivalence check failed" not in cand["rationale"]
    store.close()


def test_runner_baseline_failure_emits_error_event(tmp_path):
    """基线轮的 LaunchError/BenchError 现在进入 recovery 自愈;若无法修复,failed 前
    仍须保留原始错误事件和 recovery 尝试事件。"""
    from pping_lang.autopilot.scorecard import BenchError

    class CrashingSandbox(SimSandbox):
        def measure(self, obj):
            raise BenchError("bench 0 个成功样本(共 480)→ 候选不可用,判负")

    store = SessionStore(tmp_path / "baseline-crash.jsonl")
    store.new_session("ap-crash", {"target": "throughput"}, {"rounds": 2})
    Runner(store=store, sandbox=CrashingSandbox("M"), agent=StubAgent(), obj=OBJ,
           budget={"rounds": 2, "seconds": 900}, model="M", step_delay_s=0.0).run()
    st = store.status_dict()
    assert st["state"] == "failed"
    assert "BenchError" in (st.get("error") or "")
    errs = [e for e in st["events"] if e.get("level") == "error"]
    assert any("基线失败" in (e.get("message") or "") for e in errs)
    assert any((e.get("detail") or {}).get("error") for e in errs)
    # recovery 痕迹：agent 被询求修复动作
    assert any("recovery" in (e.get("message") or "").lower() for e in st["events"])
    store.close()


def test_runner_baseline_recovery_success(tmp_path):
    """B+C：基线第一次失败,recovery 降并发后成功,最终 session 完成且有有效 baseline。"""
    from pping_lang.autopilot.scorecard import BenchError

    class RecoveringSandbox(SimSandbox):
        def __init__(self):
            super().__init__("M")
            self.failures_left = 1

        def measure(self, obj):
            if self.failures_left > 0:
                self.failures_left -= 1
                raise BenchError("bench 0 个成功样本(共 480)→ 候选不可用,判负")
            return super().measure(obj)

    store = SessionStore(tmp_path / "baseline-recovery.jsonl")
    store.new_session("ap-recovery", {"target": "throughput"}, {"rounds": 2})
    Runner(store=store, sandbox=RecoveringSandbox(), agent=StubAgent(), obj=OBJ,
           budget={"rounds": 2, "seconds": 900}, model="M", step_delay_s=0.0,
           baseline_config={"max_num_seqs": 64, "gpu_memory_utilization": 0.9}).run()
    st = store.status_dict()
    assert st["state"] == "done"
    assert st["baseline_score"] > 0
    # recovery 成功事件
    assert any("recovery 成功" in (e.get("message") or "") for e in st["events"])
    # 最终 baseline config 应被 recovery 动作修改过(并发降低或 gpu_util 提高)
    assert any(e.get("detail", {}).get("recovery_action") for e in st["events"])
    store.close()


def test_run_static_streams_progress_snapshots():
    """bench 直播(反馈密度二期 A)：采集期周期回调运行中快照,回调异常不影响压测。"""
    import asyncio
    import time as _t

    from pping_lang.bench.measurement import RequestSample
    from pping_lang.bench.runner import run_static
    from pping_lang.bench.scenarios.schema import StaticScenario

    class MockClient:
        async def chat(self, model, prompt, output_tokens):
            t0 = _t.monotonic_ns()
            await asyncio.sleep(0.01)
            now = _t.monotonic_ns()
            return RequestSample(started_ns=t0, first_token_ns=t0 + 5_000_000,
                                 finished_ns=now, output_tokens=output_tokens)

        async def completions(self, model, prompt, output_tokens):
            return await self.chat(model, prompt, output_tokens)

    seen: list[dict] = []

    def on_progress(p):
        seen.append(p)
        raise RuntimeError("callback boom")          # 异常必须被吞掉

    scen = StaticScenario(name="t", endpoint="http://x", model="m", concurrency=2,
                          duration_s=1, warmup_s=0, output_tokens=4,
                          prompt_text="hi", timeout_s=5)
    rs = asyncio.run(run_static(scen, MockClient(), on_progress=on_progress,
                                progress_interval_s=0.1))
    assert rs.ok > 0
    assert seen, "采集期应至少回调一次进度快照"
    snap = seen[-1]
    assert snap["ok"] > 0 and snap["tps"] > 0 and "elapsed_s" in snap


def test_run_static_aborts_all_error_storm():
    """候选死透(全错)时熔断：5s 后 0 成功即提前终止,不空烧整个压测窗口。"""
    import asyncio
    import time as _t

    from pping_lang.bench.measurement import RequestSample
    from pping_lang.bench.runner import run_static
    from pping_lang.bench.scenarios.schema import StaticScenario

    class DeadClient:
        async def chat(self, model, prompt, output_tokens):
            now = _t.monotonic_ns()
            return RequestSample(started_ns=now, finished_ns=now, error="conn_refused")

        async def completions(self, model, prompt, output_tokens):
            return await self.chat(model, prompt, output_tokens)

    scen = StaticScenario(name="t", endpoint="http://x", model="m", concurrency=64,
                          duration_s=60, warmup_s=0, output_tokens=4,
                          prompt_text="hi", timeout_s=5)
    t0 = _t.monotonic()
    rs = asyncio.run(run_static(scen, DeadClient()))
    elapsed = _t.monotonic() - t0
    assert rs.ok == 0 and rs.total > 0
    assert elapsed < 20, f"全错风暴应在数秒内熔断,实际 {elapsed:.0f}s"


def test_agent_free_value_within_range():
    """LLM 自选 value：证据支持时一步到位(4→32),不必逐档爬梯。"""
    from pping_lang.autopilot.agent import _decision_from_json
    cands = propose_candidates("A", {"max_num_seqs": 4, "gpu_memory_utilization": 0.70})
    ctx = _ctx(cands, config={"max_num_seqs": 4, "gpu_memory_utilization": 0.70})
    dec = _decision_from_json({"action": {"knob": "max_num_seqs", "value": 32},
                               "rationale": "waiting=28 → 需求约 32,一步到位"}, ctx)
    assert dec.to_val == 32
    assert dec.config["max_num_seqs"] == 32
    assert dec.candidate_meta["value_source"] == "agent"


def test_agent_free_value_clamped_to_range():
    from pping_lang.autopilot.agent import _decision_from_json
    cands = propose_candidates("A", {"max_num_seqs": 4, "gpu_memory_utilization": 0.70})
    ctx = _ctx(cands, config={"max_num_seqs": 4, "gpu_memory_utilization": 0.70})
    dec = _decision_from_json({"action": {"knob": "max_num_seqs", "value": 999999}}, ctx)
    assert dec.to_val == 2048                       # clamp 到 Knob.hi
    assert dec.candidate_meta["value_source"] == "agent"


def test_agent_free_value_wrong_direction_falls_back():
    """候选方向来自诊断(A → 提并发);LLM 给反方向值 → 回落建议档,不放行。"""
    from pping_lang.autopilot.agent import _decision_from_json
    cands = propose_candidates("A", {"max_num_seqs": 4, "gpu_memory_utilization": 0.70})
    ctx = _ctx(cands, config={"max_num_seqs": 4, "gpu_memory_utilization": 0.70})
    dec = _decision_from_json({"action": {"knob": "max_num_seqs", "value": 2}}, ctx)
    assert dec.to_val == 8                          # 回落预定档 4→8
    assert dec.candidate_meta["value_source"] == "ladder"


def test_agent_free_value_choice_knob_uses_ladder():
    from pping_lang.autopilot.agent import _decision_from_json
    cands = propose_candidates("B", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70},
                               quality_gate=True)
    cand = next(c for c in cands if c["knob"] == "kv_cache_dtype")
    ctx = _ctx(cands, bottleneck="B", config={"max_num_seqs": 32})
    dec = _decision_from_json({"action": {"knob": "kv_cache_dtype", "value": "fp8"}}, ctx)
    assert dec.to_val == cand["to"]                 # choice 类忽略自由值,走建议档
    assert dec.candidate_meta["value_source"] == "ladder"


def test_validate_dedup_uses_actual_config():
    """防重按实际 config 查：同参数预定档已 reverted,自选不同值仍可提。"""
    from pping_lang.autopilot.agent import _decision_from_json, config_hash, validate
    cands = propose_candidates("A", {"max_num_seqs": 4, "gpu_memory_utilization": 0.70})
    ladder_cfg = next(c for c in cands if c["knob"] == "max_num_seqs")["config"]
    tried = [{"hash": config_hash(ladder_cfg), "decision": "reverted"}]
    ctx = _ctx(cands, config={"max_num_seqs": 4, "gpu_memory_utilization": 0.70}, tried=tried)
    ladder = _decision_from_json({"action": {"knob": "max_num_seqs"}}, ctx)
    assert validate(ladder, ctx) is not None        # 预定档已失败 → 挡
    free = _decision_from_json({"action": {"knob": "max_num_seqs", "value": 32}}, ctx)
    assert validate(free, ctx) is None              # 不同值 = 不同配置 → 放行


def test_propose_candidates_load_binding_guard():
    """准入闸没绑定(bench 并发喂不满)→ 提 max_num_seqs 是空转,必须剪掉。"""
    cfg = {"max_num_seqs": 32, "gpu_memory_utilization": 0.70}
    with_guard = propose_candidates("A", cfg, load_binding=False)
    assert all(c["knob"] != "max_num_seqs" for c in with_guard)
    unknown = propose_candidates("A", cfg, load_binding=None)      # 无实测证据 → 旧行为
    assert any(c["knob"] == "max_num_seqs" for c in unknown)
    binding = propose_candidates("A", cfg, load_binding=True)
    assert any(c["knob"] == "max_num_seqs" for c in binding)


def test_load_binding_from_probe():
    """load_binding 证据链：running 峰值 vs max_num_seqs + waiting。"""
    from pping_lang.autopilot.runner import load_binding
    cfg = {"max_num_seqs": 32}

    def sc_with(probe):
        return Scorecard(output_tps=1000, ttft_p99_ms=100, run_meta={"runtime_probe": probe})

    # 并发 8 压 32 上限：running 峰值 8、无 waiting 证据 → 没绑定(running 代理)
    assert load_binding(cfg, {}, sc_with({"running_reqs": {"max": 8.0, "avg": 7.3}})) is False
    # waiting 证据缺失时退化用 running 峰值代理：顶到上限 → 绑定
    assert load_binding(cfg, {}, sc_with({"running_reqs": {"max": 31.0, "avg": 28.0}})) is True
    # 决定性证据：running 顶满但 waiting==0 → 负载恰好吃满,提上限空转 → 没绑定
    assert load_binding(cfg, {}, sc_with({"running_reqs": {"max": 32.0, "avg": 29.9},
                                          "waiting_reqs": {"max": 0.0, "avg": 0.0}})) is False
    # 有排队 → 绑定(不管 running)
    assert load_binding(cfg, {}, sc_with({"running_reqs": {"max": 8.0, "avg": 7.0},
                                          "waiting_reqs": {"max": 3.0, "avg": 1.0}})) is True
    # 无实测 → None(保持旧行为)
    assert load_binding(cfg, {}, Scorecard(output_tps=1000, ttft_p99_ms=100)) is None


def test_load_binding_ambiguous_when_bench_concurrency_not_exceeding_cap():
    """真机教训：bench 并发默认恰好等于基线 max_num_seqs(都是 32)时,waiting 结构性
    永远测不出 >0(client 自己就没发第 33 个请求)——不能把"没测过"当"真没绑定"(False),
    否则 max_num_seqs 会被误剪出候选集,agent 连试都试不了。"""
    from pping_lang.autopilot.runner import load_binding
    cfg = {"max_num_seqs": 32}

    def sc_with(probe, concurrency):
        return Scorecard(output_tps=1000, ttft_p99_ms=100,
                         run_meta={"runtime_probe": probe, "concurrency": concurrency})

    running_saturated = {"running_reqs": {"max": 32.0, "avg": 29.9},
                         "waiting_reqs": {"max": 0.0, "avg": 0.0}}
    # bench 并发 == 上限：没机会观察排队 → 未知(None),别误判 False
    assert load_binding(cfg, {}, sc_with(running_saturated, concurrency=32)) is None
    # bench 并发 < 上限：更没机会测出排队 → 同样未知
    assert load_binding(cfg, {}, sc_with(running_saturated, concurrency=24)) is None
    # bench 并发确实超过上限、仍无排队 → 这才是"真没绑定"的硬证据
    assert load_binding(cfg, {}, sc_with(running_saturated, concurrency=48)) is False


def test_resilient_agent_marks_fallback_structurally():
    """兜底必须结构化标记(candidate_meta.llm_fallback),不能只藏在 rationale 文案。"""
    from pping_lang.autopilot.agent import ResilientAgent

    class Boom:
        model = "boom"

        def propose(self, ctx):
            raise RuntimeError("quota exhausted")

    cands = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    ctx = _ctx(cands)
    dec = ResilientAgent(Boom(), StubAgent(), retries=0).propose(ctx)
    assert dec.candidate_meta.get("llm_fallback") == "RuntimeError"
    assert "启发式兜底" in dec.rationale


def test_runner_emits_fallback_warn_event(tmp_path):
    from pping_lang.autopilot.agent import ResilientAgent

    class Boom:
        model = "boom"

        def propose(self, ctx):
            raise RuntimeError("quota exhausted")

    store = SessionStore(tmp_path / "fb.jsonl")
    store.new_session("ap-fb", {"target": "throughput"}, {"rounds": 1})
    Runner(store=store, sandbox=SimSandbox("M"), agent=ResilientAgent(Boom(), StubAgent(), retries=0),
           obj=OBJ, budget={"rounds": 1, "seconds": 900}, model="M", step_delay_s=0.0).run()
    st = store.status_dict()
    warns = [e for e in st["events"] if e.get("level") == "warn" and "兜底" in e.get("message", "")]
    assert warns, "LLM 兜底必须发 warn 事件让 UI 显眼提示"
    cand = [r for r in st["rounds"] if r["kind"] == "candidate"][0]
    assert (cand.get("action") or {}).get("llm_fallback") == "RuntimeError"
    store.close()


def test_runner_resume_elapsed_consumes_time_budget(tmp_path):
    """resume 后时间预算按 session 起点算：已耗尽 → 不再跑候选轮,直接收尾。"""
    store = SessionStore(tmp_path / "elapsed.jsonl")
    store.new_session("ap-elapsed", {"target": "throughput"}, {"rounds": 6})
    Runner(store=store, sandbox=SimSandbox("M"), agent=StubAgent(), obj=OBJ,
           budget={"rounds": 6, "seconds": 900}, model="M", step_delay_s=0.0,
           elapsed_s=901.0).run()
    st = store.status_dict()
    assert st["state"] == "done"
    kinds = [r["kind"] for r in st["rounds"]]
    assert kinds == ["baseline", "stop"]                  # 预算已尽：零候选轮,补记归因 stop
    assert st["rounds"][-1]["stop_cause"] == "budget_time"
    store.close()


# ---- JSONL 重建 + dashboard 读盘 ----

def test_load_status_from_jsonl(tmp_path):
    p = tmp_path / "ap-x.jsonl"
    st = SessionStore(p)
    st.new_session("ap-x", {"target": "throughput"}, {"rounds": 6}, "stub-agent")
    st.append_round(Round(round=0, kind="baseline", decision="baseline",
                          scorecard_after={"output_tps": 1240}))
    st.update_best(0, {"max_num_seqs": 32}, 1240.0, "vllm serve M --max-num-seqs 32")
    st.set_state("done")
    st.close()                                            # 写 final 完整快照
    loaded = SessionStore.load_status(p)
    assert loaded["session_id"] == "ap-x" and loaded["state"] == "done"
    assert loaded["best"]["score"] == 1240.0 and loaded["recommended_command"].startswith("vllm serve")
    assert "applies_to" in loaded
    assert "promote_package" in loaded
    assert len(loaded["rounds"]) == 1


def test_load_status_no_final_reconstructs_rounds(tmp_path):
    # 没 final 快照(崩溃/进行中)→ 回退用 session_start + 回放 round。
    # 回归：'kind' 判别曾被 Round.kind 覆盖,round 行漏读 → 现用 'rec'。
    p = tmp_path / "ap-crash.jsonl"
    st = SessionStore(p)
    st.new_session("ap-crash", {"target": "throughput"}, {"rounds": 6})
    st.append_round(Round(round=0, kind="baseline", decision="baseline",
                          scorecard_after={"output_tps": 1472}))
    st.append_round(Round(round=1, kind="candidate", decision="kept",
                          scorecard_after={"output_tps": 1900}))
    st.set_state("benchmarking")
    # 不调 close() → 没 final 快照
    loaded = SessionStore.load_status(p)
    assert loaded["session_id"] == "ap-crash" and loaded["state"] == "benchmarking"
    assert [r["round"] for r in loaded["rounds"]] == [0, 1]          # round 行被读回
    assert loaded["rounds"][1]["kind"] == "candidate"               # Round 自带 kind 保留
    st.close()


def test_resume_session_restores_memory_state(tmp_path):
    p = tmp_path / "ap-resume.jsonl"
    st = SessionStore(p)
    st.new_session("ap-resume", {"target": "throughput"}, {"rounds": 4})
    st.current.baseline_score = 1240.0
    sc = Scorecard(output_tps=1240, ttft_p99_ms=380, tpot_p99_ms=22,
                   run_meta={"model": "M", "gpu": "sim-GPU", **BENCH_SPEC})
    st.append_round(Round(round=0, kind="baseline", decision="baseline",
                          config_after={"max_num_seqs": 32, "gpu_memory_utilization": 0.70},
                          scorecard_after=sc.to_dict()))
    st.update_best(0, {"max_num_seqs": 32, "gpu_memory_utilization": 0.70}, 1240.0,
                   "vllm serve M --max-num-seqs 32")
    st.set_state("benchmarking")
    st.close()

    resumed = SessionStore(p)
    s = resumed.resume_session()
    assert s and s.session_id == "ap-resume" and s.state == "resuming"
    assert s.best_config["max_num_seqs"] == 32 and len(s.rounds) == 1
    resumed.close()


def test_runner_resumes_incomplete_session_and_continues(tmp_path):
    p = tmp_path / "ap-resume-run.jsonl"
    st = SessionStore(p)
    st.new_session("ap-resume-run", {"target": "throughput"}, {"rounds": 4, "seconds": 900})
    st.current.baseline_score = 1240.0
    sc = Scorecard(output_tps=1240, ttft_p99_ms=380, tpot_p99_ms=22,
                   run_meta={"model": "M", "gpu": "sim-GPU", **BENCH_SPEC})
    st.append_round(Round(round=0, kind="baseline", decision="baseline",
                          config_after={"max_num_seqs": 32, "gpu_memory_utilization": 0.70},
                          scorecard_after=sc.to_dict(), objective_score_after=1240.0))
    st.update_best(0, {"max_num_seqs": 32, "gpu_memory_utilization": 0.70}, 1240.0,
                   "vllm serve M --max-num-seqs 32")
    st.set_state("benchmarking")
    st.close()

    resumed = SessionStore(p)
    resumed.resume_session()
    Runner(store=resumed, sandbox=SimSandbox("M"), agent=StubAgent(), obj=OBJ,
           budget={"rounds": 4, "seconds": 900}, model="M", step_delay_s=0.0).run()
    d = resumed.status_dict()
    assert d["state"] == "done"
    assert any(r["state_at_record"] == "resuming" and r["decision"] == "reverted"
               for r in d["rounds"])
    assert any(r["decision"] == "kept" for r in d["rounds"])
    assert d["best"]["score"] > 1240.0
    resumed.close()


def test_runner_discards_candidate_when_budget_expires_mid_bench(tmp_path):
    class SlowCandidateSandbox(SimSandbox):
        def __init__(self):
            super().__init__("M")
            self.n = 0

        def measure(self, obj):
            import time as _time
            self.n += 1
            if self.n > 1:
                _time.sleep(0.08)
            return super().measure(obj)

    store = SessionStore(tmp_path / "budget.jsonl")
    store.new_session("ap-budget", {"target": "throughput"}, {"rounds": 4, "seconds": 0.05})
    Runner(store=store, sandbox=SlowCandidateSandbox(), agent=StubAgent(), obj=OBJ,
           budget={"rounds": 4, "seconds": 0.05}, model="M", step_delay_s=0.0).run()
    d = store.status_dict()
    cand = [r for r in d["rounds"] if r["kind"] == "candidate"]
    assert cand and cand[0]["decision"] == "reverted"
    assert "半截轮丢弃" in cand[0]["rationale"]
    assert d["best"]["config"]["max_num_seqs"] == 64
    store.close()


def test_controller_status_reads_disk(tmp_path):
    p = tmp_path / "ap-done.jsonl"
    st = SessionStore(p)
    st.new_session("ap-done", {"target": "throughput"}, {"rounds": 6})
    st.append_round(Round(round=0, kind="baseline", decision="baseline"))
    st.set_state("done")
    st.close()
    ctrl = AutopilotController(model="M", session_dir=tmp_path)   # 全新,无内存 session
    s = ctrl.status()
    assert s and s["session_id"] == "ap-done" and s["state"] == "done"


def test_controller_status_reads_disk_by_mtime_not_filename(tmp_path):
    """真机复现：sid 带非日期后缀(如 ap-expA-20260718-...)时,字典序 'e' > 数字,
    会排在同一天甚至更晚的 ap-20260721-... 后面——bridge/controller 重启后没了
    内存 session,退回读磁盘"最新"JSONL 时,曾经按文件名字典序取 files[-1],把陈旧
    的手工实验 session 误判成刚跑完的,用户看不到真正最近一次 session 的结果。
    必须按 mtime 排序,不按文件名。"""
    import os
    import time

    old = tmp_path / "ap-expA-20260718-113858.jsonl"
    new = tmp_path / "ap-20260721-025947.jsonl"
    for path, sid in ((old, "ap-expA-20260718-113858"), (new, "ap-20260721-025947")):
        st = SessionStore(path)
        st.new_session(sid, {"target": "throughput"}, {"rounds": 1})
        st.set_state("done")
        st.close()
    now = time.time()
    os.utime(old, (now - 100, now - 100))   # 旧文件,mtime 更早
    os.utime(new, (now, now))               # 新文件,mtime 更晚(文件名字典序却相反)

    ctrl = AutopilotController(model="M", session_dir=tmp_path)
    s = ctrl.status()
    assert s and s["session_id"] == "ap-20260721-025947"


# ---- host CLI orchestrator(mock docker,SimSandbox 顶替免 GPU)----

def test_run_cli_orchestrates_session(tmp_path, monkeypatch):
    import pping_lang.autopilot.run as runmod
    from pping_lang.autopilot.sandbox import SimSandbox
    stops = []
    monkeypatch.setattr(runmod, "_docker", lambda *a: stops.append(a) or _fake_proc(0, "true"))
    monkeypatch.setattr(runmod, "_serve_running", lambda name: True)
    monkeypatch.setattr(runmod, "DockerSandbox", lambda *a, **k: SimSandbox("M"))
    rc = runmod.main(["--model", "M", "--image", "img", "--serve-container", "pvllm",
                      "--session-dir", str(tmp_path), "--rounds", "4", "--ttft", "1000"])
    assert rc == 0
    assert any("stop" in a for a in stops) and any("start" in a for a in stops)  # 停+重启主 serve
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    s = SessionStore.load_status(files[0])
    assert s["state"] == "done" and s["best"]["score"] > s["baseline_score"]


def test_run_cli_accepts_external_session_id(tmp_path, monkeypatch):
    import pping_lang.autopilot.run as runmod
    from pping_lang.autopilot.sandbox import SimSandbox
    monkeypatch.setattr(runmod, "DockerSandbox", lambda *a, **k: SimSandbox("M"))
    rc = runmod.main(["--model", "M", "--image", "img", "--session-id", "ap-fixed",
                      "--session-dir", str(tmp_path), "--rounds", "1", "--ttft", "1000"])
    assert rc == 0
    assert (tmp_path / "ap-fixed.jsonl").exists()


def test_run_cli_resumes_session(tmp_path, monkeypatch):
    import pping_lang.autopilot.run as runmod
    from pping_lang.autopilot.sandbox import SimSandbox
    p = tmp_path / "ap-resume-cli.jsonl"
    st = SessionStore(p)
    st.new_session("ap-resume-cli", {"target": "throughput", "sla": {"ttft_p99_ms": 1000}},
                   {"rounds": 4, "seconds": 900})
    st.current.baseline_score = 1240.0
    sc = Scorecard(output_tps=1240, ttft_p99_ms=380, tpot_p99_ms=22,
                   run_meta={"model": "M", "gpu": "sim-GPU", **BENCH_SPEC})
    st.append_round(Round(round=0, kind="baseline", decision="baseline",
                          config_after={"max_num_seqs": 32, "gpu_memory_utilization": 0.70},
                          scorecard_after=sc.to_dict(), objective_score_after=1240.0))
    st.update_best(0, {"max_num_seqs": 32, "gpu_memory_utilization": 0.70}, 1240.0,
                   "vllm serve M --max-num-seqs 32")
    st.set_state("benchmarking")
    st.close()

    monkeypatch.setattr(runmod, "DockerSandbox", lambda *a, **k: SimSandbox("M"))
    rc = runmod.main(["--model", "M", "--image", "img", "--resume", str(p)])
    assert rc == 0
    s = SessionStore.load_status(p)
    assert s["state"] == "done"
    assert any(r["state_at_record"] == "resuming" for r in s["rounds"])
    assert s["best"]["score"] > 1240.0


# ---- review fixes:kv 语义 / equivalence 顺序 / flag 渲染 ----

def test_diag_block_kv_util_is_capacity_not_bandwidth():
    # kv_util = KV cache 占用比(容量维,D 证据),不是 NVML HBM busy%(带宽维,B 证据)
    from pping_lang.autopilot.runner import diag_block
    sc = Scorecard(output_tps=1000, ttft_p99_ms=100, tpot_p99_ms=10, run_meta={
        "diagnosis": {"bottleneck": "D", "source": "live:/api/diagnoses",
                      "metrics": {"gpu.mem_util_pct:avg": 88.0,
                                  "vllm.perf.mfu_ratio:avg": 0.05}},
        "runtime_probe": {"kv_cache_usage": {"max": 0.97, "avg": 0.9},
                          "gpu_mem_bw_pct": {"avg": 88.0},
                          "waiting_reqs": {"max": 12.0}},
    })
    d = diag_block({"max_num_seqs": 256}, sc)
    assert d["kv_util"] == 0.97
    assert d["mbu"] == 88.0
    assert d["waiting"] == 12.0


def test_kv_headroom_prefers_measured_kv_usage():
    from pping_lang.autopilot.runner import kv_headroom
    sc = Scorecard(run_meta={"runtime_probe": {"kv_cache_usage": {"max": 0.95}}})
    diag = {"running": 8.0}
    # 准入闸代理会说余量充足(running≪max_num_seqs),但实测 KV 占用 95% → 余量 0.05
    assert kv_headroom({"max_num_seqs": 256}, diag, sc) == pytest.approx(0.05)
    # 真 KV 证据缺失 → 退回准入闸代理
    assert kv_headroom({"max_num_seqs": 256}, diag, None) == pytest.approx(1 - 8 / 256)


def test_runner_t2_golden_taken_before_candidate_apply(tmp_path):
    # resume 场景 golden 缺失：必须在 apply 候选**前**取 golden(此刻沙盒是 best)。
    # 若在候选加载后再取,候选自己的输出会被当 golden → 等价检查恒真放行。
    from pping_lang.autopilot.agent import AgentDecision

    class DriftSandbox(SimSandbox):
        def sample_outputs(self, prompts=None):
            return ["changed"] if self._cfg.get("kv_cache_dtype") == "fp8" else ["gold"]

    class PickKvAgent:
        model = "pick-kv"

        def propose(self, ctx):
            cand = next(c for c in ctx.candidates if c["knob"] == "kv_cache_dtype")
            return AgentDecision(knob="kv_cache_dtype", config=cand["config"],
                                 from_val=cand["from"], to_val=cand["to"], flag=cand["flag"],
                                 rationale="try fp8 kv")

    base_cfg = {"max_num_seqs": 128, "gpu_memory_utilization": 0.70}
    probe = SimSandbox("M")
    probe.apply(base_cfg)
    base_sc = probe.measure(OBJ)
    store = SessionStore(tmp_path / "eq-resume.jsonl")
    store.new_session("ap-eq-resume", {"target": "throughput"}, {"rounds": 2})
    store.append_round(Round(round=0, kind="baseline", decision="baseline",
                             config_after=dict(base_cfg),
                             scorecard_after=base_sc.to_dict(),
                             objective_score_after=base_sc.output_tps))
    store.update_best(0, base_cfg, base_sc.output_tps, "cmd")
    Runner(store=store, sandbox=DriftSandbox("M"), agent=PickKvAgent(), obj=OBJ,
           budget={"rounds": 2, "seconds": 900}, model="M", step_delay_s=0.0,
           baseline_config=base_cfg, quality_gate=True).run()
    cand = [r for r in store.status_dict()["rounds"]
            if r["kind"] == "candidate" and (r.get("action") or {}).get("knob") == "kv_cache_dtype"]
    assert cand and cand[0]["decision"] == "reverted"
    assert "equivalence check" in cand[0]["rationale"]
    assert cand[0]["scorecard_after"] is None          # 没 bench 错误配置
    store.close()


def test_render_speculative_and_cudagraph_as_valid_cli():
    # --speculative-config 收 JSON dict;cudagraph_mode 是 CompilationConfig 字段,
    # 顶层没有 --cudagraph-mode flag(§4.5)
    from pping_lang.autopilot.action_space import render_flags
    flags = render_flags({"speculative": "ngram", "cudagraph_mode": "PIECEWISE"})
    spec = json.loads(flags[flags.index("--speculative-config") + 1])
    assert spec["method"] == "ngram" and spec["num_speculative_tokens"] >= 1
    comp = json.loads(flags[flags.index("--compilation-config") + 1])
    assert comp == {"cudagraph_mode": "PIECEWISE"}
    assert "--cudagraph-mode" not in flags


def test_render_command_quotes_json_tokens():
    cmd = render_command("M", {"cudagraph_mode": "PIECEWISE"})
    assert "'{\"cudagraph_mode\":\"PIECEWISE\"}'" in cmd


# ---- 业务形态 WorkloadSpec(M1 优先级 5)----

def test_workload_shape_table_integrity():
    """形态表与首页实时 tab 同套词汇;custom 必须全空(全手动透传,行为同引入形态前)。"""
    from pping_lang.autopilot.workload import WORKLOAD_SHAPES
    assert set(WORKLOAD_SHAPES) == {"chat", "rag", "agent", "reasoning", "code", "custom"}
    assert WORKLOAD_SHAPES["custom"]["bench"] == {} and WORKLOAD_SHAPES["custom"]["sla"] == {}
    for name, s in WORKLOAD_SHAPES.items():
        if name == "custom":
            continue
        assert {"prompt_tokens", "output_tokens", "concurrency"} <= set(s["bench"]), name
        assert {"ttft_p99_ms", "tpot_p99_ms", "e2e_p99_ms"} <= set(s["sla"]), name


def test_workload_resolve_bench_explicit_beats_shape():
    from pping_lang.autopilot.workload import resolve_bench
    # 显式 flag 优先于形态
    out = resolve_bench("chat", {"concurrency": 256, "prompt_tokens": None, "output_tokens": None})
    assert out["concurrency"] == 256
    assert out["prompt_tokens"] == 500 and out["output_tokens"] == 128   # 形态补上
    # custom / 无形态：透传,只留显式值
    out = resolve_bench("custom", {"concurrency": 32, "prompt_tokens": None, "output_tokens": None})
    assert out == {"concurrency": 32}
    out = resolve_bench("", {"concurrency": None, "prompt_tokens": None, "output_tokens": None})
    assert out == {}


def test_workload_resolve_sla():
    from pping_lang.autopilot.workload import resolve_sla
    assert resolve_sla("code") == (100, 20, 2000)                    # 形态默认(硬延迟闸)
    assert resolve_sla("code", ttft=250) == (250, 20, 2000)          # 显式优先
    assert resolve_sla("code", e2e=1500) == (100, 20, 1500)          # e2e 也可单独覆盖
    assert resolve_sla("custom") == (None, None, None)               # 透传不加闸
    assert resolve_sla("") == (None, None, None)


def test_run_cli_workload_resolution(monkeypatch, tmp_path):
    """run.py 接线：--workload 展开形态负载+SLA;显式 flag 覆盖;custom 行为同旧默认。"""
    from pping_lang.autopilot import run as run_cli
    from pping_lang.autopilot.sandbox import BENCH_SPEC

    captured = {}

    class _FakeRunner:
        def __init__(self, **kw):
            captured.update(kw)
        def run(self):
            pass

    class _FakeSandbox:
        def __init__(self, *a, **kw):
            captured["bench_spec"] = kw.get("bench_spec")
        def teardown(self):
            pass

    class _FakeStore:
        def __init__(self, path):
            pass
        def new_session(self, sid, objective, budget, agent_model=""):
            captured["objective"] = objective
        def close(self):
            pass
        def status_dict(self):
            return {"state": "done", "rounds": []}

    monkeypatch.setattr(run_cli, "Runner", _FakeRunner)
    monkeypatch.setattr(run_cli, "DockerSandbox", _FakeSandbox)
    monkeypatch.setattr(run_cli, "SessionStore", _FakeStore)
    monkeypatch.setattr(run_cli, "_serve_running", lambda name: False)
    monkeypatch.setattr(run_cli, "_docker", lambda *a: None)

    def _run(argv):
        captured.clear()
        rc = run_cli.main(argv + ["--model", "M", "--image", "I",
                                  "--session-dir", str(tmp_path)])
        assert rc == 0
        return captured

    base = ["--session-id", "ap-test"]
    # 形态展开：chat → c64/p500/o128 + SLA 1000/50,objective 带 workload 标记
    c = _run(base + ["--workload", "chat"])
    bs = c["bench_spec"]
    assert (bs["concurrency"], bs["prompt_tokens"], bs["output_tokens"]) == (64, 500, 128)
    assert c["objective"]["sla"]["ttft_p99_ms"] == 1000
    assert c["objective"]["sla"]["tpot_p99_ms"] == 50
    assert c["objective"]["sla"]["e2e_p99_ms"] == 3000
    assert c["objective"]["workload"] == "chat"

    # 显式 flag 覆盖形态;未显式给的仍由形态补
    c = _run(base + ["--workload", "chat", "--bench-concurrency", "256", "--ttft", "800"])
    assert c["bench_spec"]["concurrency"] == 256
    assert c["objective"]["sla"]["ttft_p99_ms"] == 800
    assert c["objective"]["sla"]["tpot_p99_ms"] == 50

    # custom / 无形态：行为同引入形态前(旧兜底 8/128/BENCH_SPEC,SLA 不加闸)
    c = _run(base + ["--workload", "custom"])
    assert c["bench_spec"]["concurrency"] == 8
    assert c["bench_spec"]["output_tokens"] == 128
    assert c["bench_spec"]["prompt_tokens"] == BENCH_SPEC["prompt_tokens"]
    assert c["objective"]["sla"]["ttft_p99_ms"] is None
    assert c["objective"]["sla"]["e2e_p99_ms"] is None
    c = _run(base)
    assert c["bench_spec"]["concurrency"] == 8
    assert "workload" not in c["objective"]


def test_run_cli_registers_signal_handlers_that_stop_runner(monkeypatch, tmp_path):
    """bridge 跨进程停止 session 只能靠 os.killpg(SIGINT/SIGTERM)(没法直接调 Python 方法)。
    真机复现(2026-07-22)：默认 SIGINT→KeyboardInterrupt 在进程卡着阻塞 HTTPS 调用时不可靠,
    bridge 10s 优雅期等不到就 SIGKILL 强杀,session 没有 stop_cause、没走 _finalize()。main()
    现在必须显式接管 SIGINT/SIGTERM,转译成 runner.stop()(配合 agent.py 里 0.5s 粒度轮询,
    能在阻塞调用完成前跳出)——这里锁死"注册了这两个信号且 handler 确实调用了 runner.stop()",
    不依赖真的给测试进程发信号(避免污染 pytest 自身的信号处理)。"""
    import signal as signal_mod

    from pping_lang.autopilot import run as run_cli

    instances = []

    class _FakeRunner:
        def __init__(self, **kw):
            self.stopped = False
            instances.append(self)

        def run(self):
            pass

        def stop(self):
            self.stopped = True

    class _FakeSandbox:
        def __init__(self, *a, **kw):
            pass

        def teardown(self):
            pass

    class _FakeStore:
        def __init__(self, path):
            pass

        def new_session(self, sid, objective, budget, agent_model=""):
            pass

        def close(self):
            pass

        def status_dict(self):
            return {"state": "done", "rounds": []}

    registered = {}

    def _fake_signal(sig, handler):
        registered[sig] = handler

    monkeypatch.setattr(run_cli, "Runner", _FakeRunner)
    monkeypatch.setattr(run_cli, "DockerSandbox", _FakeSandbox)
    monkeypatch.setattr(run_cli, "SessionStore", _FakeStore)
    monkeypatch.setattr(run_cli, "_serve_running", lambda name: False)
    monkeypatch.setattr(run_cli, "_docker", lambda *a: None)
    monkeypatch.setattr(run_cli.signal, "signal", _fake_signal)

    rc = run_cli.main(["--model", "M", "--image", "I", "--session-id", "ap-sig",
                       "--session-dir", str(tmp_path)])
    assert rc == 0
    assert signal_mod.SIGINT in registered and signal_mod.SIGTERM in registered

    runner_instance = instances[-1]
    assert not runner_instance.stopped
    registered[signal_mod.SIGINT](signal_mod.SIGINT, None)
    assert runner_instance.stopped        # 信号 → runner.stop(),不靠抛异常打断阻塞调用
