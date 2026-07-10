"""Autopilot M0 决策核 + sim 闭环单测(无 GPU、无网络)。"""
from __future__ import annotations

import json

import pytest

from pping_lang.autopilot.action_space import propose_candidates, render_command
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


def test_score_high_error_rejected():
    sc = Scorecard(output_tps=9999, ttft_p99_ms=100, error_rate=0.6)
    assert objective_score(sc, OBJ) == float("-inf")


def test_decide_kept_revert_tie():
    assert decide(2000, 1240, 0.03) == "kept"            # 超噪声边界
    assert decide(float("-inf"), 1240) == "reverted"     # 破 SLA
    assert decide(2000, float("-inf")) == "kept"         # 基线不达标,首个可行候选
    assert decide(1250, 1240, 0.03) == "tie"             # 噪声内


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
    # 容量墙 D:max_num_seqs 伤 D(hurts),不能"提并发";给"降并发"或"扩 KV 池"
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


def test_propose_t2_gated_by_quality_gate():
    # 质量门关(默认)→ 只 T1;开 → 放 T2(kv_cache_dtype 等),给 B 瓶颈
    cfg = {"max_num_seqs": 128, "gpu_memory_utilization": 0.92}
    off = {c["knob"] for c in propose_candidates("B", cfg, quality_gate=False)}
    on = {c["knob"] for c in propose_candidates("B", cfg, quality_gate=True)}
    assert "kv_cache_dtype" not in off and "kv_cache_dtype" in on


def test_propose_d_headroom_guard():
    # 推大-batch 类(max_num_seqs↑)在 KV 余量不足时被守卫挡掉(§4.4),改给治 D 的旋钮
    cfg = {"max_num_seqs": 8, "gpu_memory_utilization": 0.70}
    tight = {c["knob"] for c in propose_candidates("A", cfg, kv_headroom=0.05)}
    loose = {c["knob"] for c in propose_candidates("A", cfg, kv_headroom=0.9)}
    assert "max_num_seqs" in loose and "max_num_seqs" not in tight


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


def test_constraint_graph_needs_mechanism():
    # 约束图 needs 机制本身(§4.5):前置 flag 关着 → 不可行。
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
        "vllm serve M --max-num-seqs 32 --gpu-memory-utilization 0.7")
    phases = [e["phase"] for e in d["events"]]
    for phase in ["baseline", "observe", "propose", "apply", "benchmark", "decide", "finalize"]:
        assert phase in phases
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


def test_runner_defaults_allow_deeper_agent_exploration(tmp_path):
    from pping_lang.autopilot.runner import K_NO_IMPROVE, Runner

    store = SessionStore(tmp_path / "defaults.jsonl")
    agent = StubAgent()
    runner = Runner(store=store, sandbox=SimSandbox("M"), agent=agent,
                    obj=build_objective({"target": "throughput"}), budget={}, model="M")
    assert runner._rounds_budget == 12
    assert runner._secs_budget == 30 * 60
    assert K_NO_IMPROVE == 4


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
    """agent-test 必须与 build_agent 的 ClaudeAgent 路由一致:/v1/messages + x-api-key。"""
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


def test_kimi_agent_captures_thinking(monkeypatch):
    """思考过程直播:provider 响应里的 thinking block 随决策带出(dec.thinking)。"""
    from pping_lang.autopilot.agent import KimiCodingAgent

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({"content": [
                {"type": "thinking", "thinking": "waiting=28 说明需求远超准入闸,一步到位"},
                {"type": "text", "text": '{"done":false,"action":{"knob":"max_num_seqs","value":32},'
                                         '"rationale":"提并发","evidence_refs":["A:live"]}'},
            ]}).encode("utf-8")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Resp())
    a = KimiCodingAgent(api_key="k")
    cands = propose_candidates("A", {"max_num_seqs": 4, "gpu_memory_utilization": 0.70})
    d = a.propose(_ctx(cands, config={"max_num_seqs": 4}))
    assert d.knob == "max_num_seqs" and d.to_val == 32
    assert "一步到位" in d.thinking


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
    assert any(m.startswith("agent 思考:") for m in msgs)
    assert any(m.startswith("agent 决策:max_num_seqs") for m in msgs)
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


def test_validate_rejects_offmenu_and_repeat():
    from pping_lang.autopilot.agent import AgentDecision, config_hash, validate
    cands = propose_candidates("A", {"max_num_seqs": 32, "gpu_memory_utilization": 0.70})
    cfg = next(c["config"] for c in cands if c["knob"] == "max_num_seqs")
    ctx = _ctx(cands, tried=[{"hash": config_hash(cfg), "decision": "reverted"}])
    assert validate(AgentDecision(knob="nonexistent_knob"), ctx)        # 不在候选集 → 拒
    assert validate(AgentDecision(knob="max_num_seqs", config=cfg), ctx)  # 已试 reverted → 防重拒
    assert validate(AgentDecision(done=True), ctx) is None              # done 合法


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
    """就绪心跳日志清理:pid 前缀 + INFO/时间戳/文件名前缀常叠两层,须都剥掉。"""
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


# ---- ③ 真诊断:读候选 /api/diagnoses → bottleneck ----

def test_docker_sandbox_read_diagnosis_picks_most_severe(monkeypatch):
    from pping_lang.autopilot.sandbox import DockerSandbox
    sb = DockerSandbox("M", "img", dash_port=8013)
    payload = {"window_seconds": 120, "diagnoses": [
        {"rule_id": "A", "severity": "info", "message": "双低", "ts_ns": 1, "context": {"mfu": 0.1}},
        {"rule_id": "D", "severity": "critical", "message": "容量墙", "ts_ns": 2, "context": {"kv_pressure": 0.95}}]}

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
    """serving 非 seed 决定性(§6.2):个别 token 漂移不应误杀 T2 候选,相似度阈值放行。"""
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
    """基线轮的 LaunchError/BenchError 之前是裸抛,连诊断信息都留不下(真机教训:
    "0 个成功样本" 报废整个 session,却没有任何事件说明候选中途崩了)。现在必须
    在 failed 之前先发一条带 detail.error 的 decide 事件。"""
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
    store.close()


def test_run_static_streams_progress_snapshots():
    """bench 直播(反馈密度二期 A):采集期周期回调运行中快照,回调异常不影响压测。"""
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
    """候选死透(全错)时熔断:5s 后 0 成功即提前终止,不空烧整个压测窗口。"""
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
    """LLM 自选 value:证据支持时一步到位(4→32),不必逐档爬梯。"""
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
    """防重按实际 config 查:同旋钮预定档已 reverted,自选不同值仍可提。"""
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
    """load_binding 证据链:running 峰值 vs max_num_seqs + waiting。"""
    from pping_lang.autopilot.runner import load_binding
    cfg = {"max_num_seqs": 32}

    def sc_with(probe):
        return Scorecard(output_tps=1000, ttft_p99_ms=100, run_meta={"runtime_probe": probe})

    # 并发 8 压 32 上限:running 峰值 8、无 waiting 证据 → 没绑定(running 代理)
    assert load_binding(cfg, {}, sc_with({"running_reqs": {"max": 8.0, "avg": 7.3}})) is False
    # waiting 证据缺失时退化用 running 峰值代理:顶到上限 → 绑定
    assert load_binding(cfg, {}, sc_with({"running_reqs": {"max": 31.0, "avg": 28.0}})) is True
    # 决定性证据:running 顶满但 waiting==0 → 负载恰好吃满,提上限空转 → 没绑定
    assert load_binding(cfg, {}, sc_with({"running_reqs": {"max": 32.0, "avg": 29.9},
                                          "waiting_reqs": {"max": 0.0, "avg": 0.0}})) is False
    # 有排队 → 绑定(不管 running)
    assert load_binding(cfg, {}, sc_with({"running_reqs": {"max": 8.0, "avg": 7.0},
                                          "waiting_reqs": {"max": 3.0, "avg": 1.0}})) is True
    # 无实测 → None(保持旧行为)
    assert load_binding(cfg, {}, Scorecard(output_tps=1000, ttft_p99_ms=100)) is None


def test_load_binding_ambiguous_when_bench_concurrency_not_exceeding_cap():
    """真机教训:bench 并发默认恰好等于基线 max_num_seqs(都是 32)时,waiting 结构性
    永远测不出 >0(client 自己就没发第 33 个请求)——不能把"没测过"当"真没绑定"(False),
    否则 max_num_seqs 会被误剪出候选集,agent 连试都试不了。"""
    from pping_lang.autopilot.runner import load_binding
    cfg = {"max_num_seqs": 32}

    def sc_with(probe, concurrency):
        return Scorecard(output_tps=1000, ttft_p99_ms=100,
                         run_meta={"runtime_probe": probe, "concurrency": concurrency})

    running_saturated = {"running_reqs": {"max": 32.0, "avg": 29.9},
                         "waiting_reqs": {"max": 0.0, "avg": 0.0}}
    # bench 并发 == 上限:没机会观察排队 → 未知(None),别误判 False
    assert load_binding(cfg, {}, sc_with(running_saturated, concurrency=32)) is None
    # bench 并发 < 上限:更没机会测出排队 → 同样未知
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
    """resume 后时间预算按 session 起点算:已耗尽 → 不再跑候选轮,直接收尾。"""
    store = SessionStore(tmp_path / "elapsed.jsonl")
    store.new_session("ap-elapsed", {"target": "throughput"}, {"rounds": 6})
    Runner(store=store, sandbox=SimSandbox("M"), agent=StubAgent(), obj=OBJ,
           budget={"rounds": 6, "seconds": 900}, model="M", step_delay_s=0.0,
           elapsed_s=901.0).run()
    st = store.status_dict()
    assert st["state"] == "done"
    kinds = [r["kind"] for r in st["rounds"]]
    assert kinds == ["baseline"]                        # 预算已尽:只有基线,零候选轮
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
    # 回归:'kind' 判别曾被 Round.kind 覆盖,round 行漏读 → 现用 'rec'。
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
    assert d["best"]["config"]["max_num_seqs"] == 32
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
    # resume 场景 golden 缺失:必须在 apply 候选**前**取 golden(此刻沙盒是 best)。
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
