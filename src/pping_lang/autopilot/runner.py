"""runner.py —— 状态机编排 + 预算/收敛 + 收尾回 best(§9.3,G5)。

idle → baselining → (proposing → applying → warming_up → benchmarking → deciding)* →
finalizing → done。后台线程跑;每步写 session_store 供 UI 2s 轮询。收尾强制 apply best。

停机 = 轮数尽 OR 时间尽 OR 连续 K 轮无 kept OR agent done。半截轮丢弃。
observe(诊断)在 M0 sim 路由由 scorecard/config 推出瓶颈;真路由读 /api/diagnoses。
"""
from __future__ import annotations

import threading
import time

from pping_lang.autopilot.action_space import knob, propose_candidates, render_command
from pping_lang.autopilot.agent import AgentContext, AgentDecision, config_hash, validate
from pping_lang.autopilot.config_review import review_config_diff
from pping_lang.autopilot.kvfit import evaluate_kvfit
from pping_lang.autopilot.objective import (
    ObjectiveSpec,
    Scorecard,
    decide,
    objective_score,
    primary_delta_pct,
)
from pping_lang.autopilot.promote import build_promote_package
from pping_lang.autopilot.session_store import Round, SessionStore
from pping_lang.autopilot.repeat import aggregate_scorecards
from pping_lang.autopilot.search import prepare_search_candidates

BASELINE_CONFIG = {"max_num_seqs": 32, "gpu_memory_utilization": 0.70}
K_NO_IMPROVE = 2
MAX_ILLEGAL = 2          # proposing:连续非法提案上限 → failed(§9.3)
INCOMPLETE_STATES = {"applying", "warming_up", "benchmarking", "deciding"}


def diag_block(config: dict, sc: Scorecard) -> dict:
    """observe → §8 蒸馏诊断块。优先候选实测的真诊断(③:sc.run_meta['diagnosis']),
    映射成 {bottleneck, fired_rules, mfu, running, kv_util, ttft/tpot/tps, evidence_refs};
    没有则回退 config 启发式 diagnose()。"""
    live = (sc.run_meta or {}).get("diagnosis")
    if live and live.get("bottleneck"):
        m = live.get("metrics") or {}
        bn = live["bottleneck"]
        return {
            "bottleneck": bn, "fired_rules": [bn], "source": live.get("source"),
            "mfu": m.get("vllm.perf.mfu_ratio:avg"),
            "running": m.get("vllm.scheduler.running_reqs:avg"),
            "kv_util": m.get("gpu.mem_util_pct:avg"),
            "ttft_p99_ms": sc.ttft_p99_ms, "tpot_p99_ms": sc.tpot_p99_ms,
            "output_tps": sc.output_tps, "evidence_refs": live.get("evidence_refs", []),
        }
    return diagnose(config, sc)


def kv_headroom(config: dict, diag: dict) -> float:
    """KV 余量代理(§4.4 D 守卫):准入闸快满(running≈max_num_seqs)→ 余量低,推大-batch 先治 D。"""
    running = diag.get("running")
    seqs = config.get("max_num_seqs")
    if running and seqs:
        return max(0.0, 1.0 - float(running) / float(seqs))
    kvp = (diag.get("metrics") or {}).get("kv_pressure")     # sim 路由
    return max(0.0, 1.0 - float(kvp)) if kvp is not None else 1.0


def diagnose(config: dict, sc: Scorecard) -> dict:
    """observe:从配置+实测推出当前命中瓶颈(sim 路由)。真路由换成读 /api/diagnoses。

    KV 压力 = 并发 / KV 容量(∝ gpu_util)。<0.6 双低(A,喂不饱)/ 0.6–1 带宽墙(B,KV 在填)/
    >1 容量墙(D,抢占)。
    """
    seqs = float(config.get("max_num_seqs", 32))
    util = float(config.get("gpu_memory_utilization", 0.70))
    kv_cap = 140.0 * (util / 0.70)
    pressure = seqs / kv_cap
    if pressure > 1.0:
        bn, det = "D", "kv_pressure"
    elif pressure >= 0.6:
        bn, det = "B", "hbm_busy"
    else:
        bn, det = "A", "roofline"
    return {
        "bottleneck": bn,
        "evidence_refs": [f"{bn}:{det}", f"regime:{bn}",
                          f"metric:kv_pressure={round(pressure, 2)}"],
        "metrics": {"max_num_seqs": seqs, "gpu_memory_utilization": util,
                    "kv_pressure": round(pressure, 2), "output_tps": sc.output_tps,
                    "ttft_p99_ms": sc.ttft_p99_ms},
    }


def applies_to(model: str, obj: ObjectiveSpec, sc: Scorecard | None) -> dict:
    """报告适用边界(§9.3):一次 Autopilot 结论只能绑定到当时的模型/GPU/vLLM/workload/objective。"""
    meta = sc.run_meta if sc else {}
    return {
        "model": model or meta.get("model") or "unknown",
        "gpu": meta.get("gpu") or meta.get("gpu_name") or "unknown",
        "vllm_version": meta.get("vllm_version") or meta.get("system_fingerprint") or "unknown",
        "workload_form": {
            "prompt_source": meta.get("prompt_source", "unknown"),
            "prompt_tokens": meta.get("prompt_tokens"),
            "concurrency": meta.get("concurrency"),
            "duration_s": meta.get("duration_s"),
            "warmup_s": meta.get("warmup_s"),
            "output_tokens": meta.get("output_tokens"),
        },
        "objective": {
            "target": obj.target,
            "latency_metric": obj.latency_metric,
            "sla": {
                "ttft_p99_ms": obj.sla.ttft_p99_ms,
                "tpot_p99_ms": obj.sla.tpot_p99_ms,
            },
            "floor": {
                "output_tps": obj.floor.output_tps if obj.floor else None,
            },
            "noise_margin": obj.noise_margin,
            "search_mode": meta.get("search_mode"),
            "bench_repeats": meta.get("bench_repeats"),
        },
    }


def scorecard_from_dict(d: dict | None) -> Scorecard | None:
    if not d:
        return None
    return Scorecard(
        output_tps=float(d.get("output_tps") or 0.0),
        ttft_p99_ms=float(d.get("ttft_p99_ms") or 0.0),
        tpot_p99_ms=float(d.get("tpot_p99_ms") or 0.0),
        e2e_p99_ms=float(d.get("e2e_p99_ms") or 0.0),
        error_rate=float(d.get("error_rate") or 0.0),
        run_meta=dict(d.get("run_meta") or {}),
    )


class Runner(threading.Thread):
    def __init__(self, *, store: SessionStore, sandbox, agent, obj: ObjectiveSpec,
                 budget: dict, model: str, step_delay_s: float = 0.0,
                 baseline_config: dict | None = None, quality_gate: bool = False,
                 bench_repeats: int = 1, search_mode: str = "agent",
                 search_width: int = 3) -> None:
        super().__init__(daemon=True, name="AutopilotRunner")
        self._store = store
        self._sb = sandbox
        self._agent = agent
        self._obj = obj
        self._rounds_budget = int(budget.get("rounds", 6))
        self._secs_budget = float(budget.get("seconds", budget.get("minutes", 15) * 60))
        self._model = model
        self._delay = step_delay_s          # UI 逐轮观感(真路由由 bench 时长自然产生)
        self._baseline = dict(baseline_config or BASELINE_CONFIG)   # 可配:压低=造"喂不饱"工况
        self._quality_gate = quality_gate          # 开则放 T2(质量类)候选;M0 默认关(只 T1)
        self._bench_repeats = max(1, int(bench_repeats))
        self._search_mode = search_mode
        self._search_width = max(1, int(search_width))
        self._stopping = threading.Event()
        self._best_cfg: dict = {}
        self._best_sc: Scorecard | None = None
        self._best_score = float("-inf")
        self._equivalence_golden: list[str] | None = None

    def stop(self) -> None:
        self._stopping.set()

    def _effective_config(self) -> dict:
        if hasattr(self._sb, "effective_config"):
            try:
                eff = self._sb.effective_config()
                if eff:
                    return {**self._best_cfg, **eff}
            except Exception:  # noqa: BLE001
                pass
        if self._best_sc:
            eff = (self._best_sc.run_meta or {}).get("effective_config")
            if eff:
                return {**self._best_cfg, **eff}
        return dict(self._best_cfg)

    def _measure(self, config: dict) -> Scorecard:
        self._sb.apply(config)              # 起+就绪(失败抛 LaunchError)
        return self._measure_loaded()

    def _measure_loaded(self) -> Scorecard:
        samples = [self._sb.measure(self._obj) for _ in range(self._bench_repeats)]
        sc = aggregate_scorecards(samples)
        sc.run_meta["search_mode"] = self._search_mode
        return sc

    def _sample_outputs(self) -> list[str] | None:
        if not hasattr(self._sb, "sample_outputs"):
            return None
        try:
            return list(self._sb.sample_outputs())
        except Exception:  # noqa: BLE001
            return None

    def _ensure_equivalence_golden(self) -> bool:
        if self._equivalence_golden is not None:
            return True
        self._equivalence_golden = self._sample_outputs()
        if self._equivalence_golden is not None:
            return True
        try:
            self._sb.apply(self._best_cfg)
            self._equivalence_golden = self._sample_outputs()
        except Exception:  # noqa: BLE001
            self._equivalence_golden = None
        return self._equivalence_golden is not None

    def _needs_equivalence(self, dec) -> bool:
        k = knob(dec.knob) if dec.knob else None
        return bool(k and k.output_impact == "equivalence")

    def _equivalence_ok(self, dec) -> tuple[bool, str]:
        if not self._needs_equivalence(dec):
            return True, ""
        if not self._ensure_equivalence_golden():
            return False, "equivalence check unavailable: no baseline golden outputs"
        outs = self._sample_outputs()
        if outs is None:
            return False, "equivalence check failed: candidate outputs unavailable"
        if outs != self._equivalence_golden:
            return False, "equivalence check failed: candidate output differs from current best"
        return True, ""

    def run(self) -> None:
        try:
            resume = self._restore_or_baseline()
            self._run_loop(**resume)
            self._finalize()
        except Exception as e:               # noqa: BLE001 —— 任何异常 → failed,给人工
            self._store.set_state("failed", f"{type(e).__name__}: {e}")

    def _restore_or_baseline(self) -> dict:
        cur = self._store.current
        if cur and cur.rounds and cur.best_config:
            self._restore_best(cur)
            interrupted = getattr(cur, "resume_from_state", None) or cur.state
            if interrupted in INCOMPLETE_STATES:
                self._append_resume_revert(cur)
            return self._resume_loop_state(cur)
        self._run_baseline()
        return self._resume_loop_state(self._store.current)

    def _restore_best(self, cur) -> None:
        best_round = next((r for r in cur.rounds if r.round == cur.best_round), None)
        best_sc = scorecard_from_dict(best_round.scorecard_after if best_round else None)
        if best_sc is None:
            for r in reversed(cur.rounds):
                if r.decision in ("kept", "baseline") and r.scorecard_after:
                    best_sc = scorecard_from_dict(r.scorecard_after)
                    break
        self._best_cfg = dict(cur.best_config)
        self._best_sc = best_sc
        self._best_score = float(cur.best_score if cur.best_score is not None else float("-inf"))

    def _append_resume_revert(self, cur) -> None:
        next_round = max((r.round for r in cur.rounds), default=0) + 1
        self._store.append_round(Round(
            round=next_round, kind="candidate", state_at_record="resuming",
            config_before=dict(self._best_cfg), config_after=dict(self._best_cfg),
            command=render_command(self._model, self._best_cfg),
            rationale=(f"resume: previous state '{getattr(cur, 'resume_from_state', None) or cur.state}' "
                       "was incomplete; candidate treated as failed and best restored."),
            scorecard_before=self._best_sc.to_dict() if self._best_sc else None,
            objective_score_before=self._best_score,
            objective_score_after=float("-inf"),
            decision="reverted",
            bench_spec=self._best_sc.run_meta if self._best_sc else {},
            agent_model=getattr(self._agent, "model", "")))

    def _resume_loop_state(self, cur) -> dict:
        rounds = list(cur.rounds) if cur else []
        next_round = max((r.round for r in rounds), default=0) + 1
        history = [{"round": r.round, "knob": (r.action or {}).get("knob"), "to": (r.action or {}).get("to")}
                   for r in rounds if r.kind == "candidate" and r.action]
        tried = [{"hash": config_hash(r.config_after), "decision": r.decision}
                 for r in rounds if r.kind == "candidate" and r.config_after and r.decision in ("kept", "reverted", "tie")]
        no_improve = 0
        for r in reversed([r for r in rounds if r.kind == "candidate"]):
            if r.decision == "kept":
                break
            if r.decision in ("reverted", "tie"):
                no_improve += 1
            else:
                break
        return {"start_round": max(1, next_round), "history": history,
                "tried": tried, "no_improve": no_improve}

    def _run_baseline(self) -> None:
        self._store.set_state("baselining")
        sc = self._measure(self._baseline)
        self._equivalence_golden = self._sample_outputs()
        score = objective_score(sc, self._obj)
        self._best_cfg, self._best_sc, self._best_score = dict(self._baseline), sc, score
        self._store.current.baseline_score = sc.output_tps
        self._store.update_best(0, self._baseline, score, render_command(self._model, self._baseline))
        self._store.append_round(Round(
            round=0, kind="baseline", state_at_record="baselining",
            config_before={}, config_after=dict(self._baseline),
            command=render_command(self._model, self._baseline),
            scorecard_after=sc.to_dict(), objective_score_after=score,
            decision="baseline", bench_spec=sc.run_meta, agent_model=self._agent.model
            if hasattr(self._agent, "model") else "",
            rationale="朴素基线(后续候选都跟它 + best-so-far 比)。"))
        self._tick()

    def _run_loop(self, *, start_round: int = 1, history: list[dict] | None = None,
                  tried: list[dict] | None = None, no_improve: int = 0) -> None:
        history = list(history or [])
        tried = list(tried or [])
        t0 = time.monotonic()
        rnd = start_round
        while (not self._stopping.is_set() and rnd <= self._rounds_budget
               and (time.monotonic() - t0) < self._secs_budget and no_improve < K_NO_IMPROVE):
            deadline = t0 + self._secs_budget
            self._store.set_state("proposing")
            effective_cfg = self._effective_config()
            diag = diag_block(effective_cfg, self._best_sc)             # ③ observe(真诊断优先)
            cands = propose_candidates(diag["bottleneck"], effective_cfg,
                                       kv_headroom=kv_headroom(effective_cfg, diag),
                                       quality_gate=self._quality_gate)   # §4 交集 + D 守卫
            cands = prepare_search_candidates(
                cands, effective_cfg, diag["bottleneck"], history,
                mode=self._search_mode, max_values_per_knob=self._search_width)
            p0 = evaluate_kvfit(cands, effective_cfg, self._best_sc)      # §5.2 P0:0-eval KV-fit 剪枝
            cands = p0.candidates
            diag["p0_kvfit"] = p0.summary()
            diag["p2_search"] = {"mode": self._search_mode, "candidates": len(cands)}
            ctx = AgentContext(
                objective={"target": self._obj.target, "sla": {
                    "ttft_p99_ms": self._obj.sla.ttft_p99_ms, "tpot_p99_ms": self._obj.sla.tpot_p99_ms}},
                round=rnd, budget={"rounds_left": self._rounds_budget - rnd + 1,
                                   "seconds_left": round(self._secs_budget - (time.monotonic() - t0))},
                current_config=effective_cfg, diagnosis=diag, candidates=cands,
                history=history, tried_configs=tried,
                best_so_far={"config": dict(self._best_cfg), "scorecard": self._best_sc.to_dict()})
            if not cands:                                                # 无对症候选 → 近最优,停
                self._append_stop(rnd, AgentDecision(done=True, reason="无对症候选 → 近最优"), diag)
                break
            dec = self._propose_valid(ctx)                              # 校验 ∈ 候选 + 防重(2 次非法→failed)
            if dec is None:
                self._store.set_state("failed", "agent 连续 2 次非法提案(§9.3)")
                return
            if dec.done:
                self._append_stop(rnd, dec, diag)
                break
            if dec.config is not None and dec.knob:
                tracked = dict(self._best_cfg)
                tracked[dec.knob] = dec.to_val
                dec.config = tracked
            hist_entry = {"round": rnd, "knob": dec.knob, "to": dec.to_val}
            history.append(hist_entry)
            self._run_candidate(rnd, dec, diag, deadline=deadline)
            last = self._store.current.rounds[-1]
            hist_entry["decision"] = last.decision
            hist_entry["score"] = last.objective_score_after
            tried.append({"hash": config_hash(dec.config), "decision": last.decision})
            no_improve = 0 if last.decision == "kept" else no_improve + 1
            rnd += 1

    def _propose_valid(self, ctx: AgentContext) -> AgentDecision | None:
        """proposing:最多 MAX_ILLEGAL 次,要 ∈ 候选集 + 不防重命中;都非法 → None(→ failed)。"""
        for _ in range(MAX_ILLEGAL):
            dec = self._agent.propose(ctx)
            if validate(dec, ctx) is None:
                return dec
        return None

    def _run_candidate(self, rnd: int, dec, diag: dict, *, deadline: float | None = None) -> None:
        self._store.set_state("benchmarking")
        before_cfg, before_sc, before_score = self._best_cfg, self._best_sc, self._best_score
        try:
            self._sb.apply(dec.config)
            ok, reason = self._equivalence_ok(dec)
            if not ok:
                dec.rationale += f" [{reason}]"
                sc, score = None, float("-inf")
            else:
                sc = self._measure_loaded()
                if deadline is not None and time.monotonic() >= deadline:
                    dec.rationale += " [时间预算在 benchmarking 中途耗尽,半截轮丢弃]"
                    sc, score = None, float("-inf")
                else:
                    score = objective_score(sc, self._obj)
        except Exception as e:               # LaunchError/BenchError → 判负回滚
            sc, score = None, float("-inf")
            dec.rationale += f" [候选失败:{type(e).__name__}]"
        decision = decide(score, before_score, self._obj.noise_margin)
        if decision == "kept":
            self._best_cfg, self._best_sc, self._best_score = dict(dec.config), sc, score
            self._equivalence_golden = self._sample_outputs()
            self._store.update_best(rnd, dec.config, score, render_command(self._model, dec.config))
        self._store.append_round(Round(
            round=rnd, kind="candidate", state_at_record="deciding",
            config_before=dict(before_cfg), config_after=dict(dec.config),
            command=render_command(self._model, dec.config),
            action={"knob": dec.knob, "from": dec.from_val, "to": dec.to_val, "flag": dec.flag,
                    **(dec.candidate_meta or {})},
            rationale=dec.rationale, evidence_refs=dec.evidence_refs, diagnosis=diag,
            scorecard_before=before_sc.to_dict() if before_sc else None,
            scorecard_after=sc.to_dict() if sc else None,
            objective_score_before=before_score, objective_score_after=score,
            delta_pct=primary_delta_pct(sc, before_sc, self._obj) if sc and before_sc else None,
            decision=decision, bench_spec=sc.run_meta if sc else {},
            agent_model=getattr(self._agent, "model", "")))
        if decision != "kept":                # 运行时也回 best,保证下一轮 observe 的是 best
            try:
                self._sb.apply(before_cfg)
                self._equivalence_golden = self._sample_outputs()
            except Exception as e:            # noqa: BLE001
                self._store.set_state(
                    "failed",
                    f"restore best failed after {decision}: {type(e).__name__}: {e}")
        self._tick()

    def _append_stop(self, rnd: int, dec, diag: dict) -> None:
        self._store.append_round(Round(
            round=rnd, kind="stop", state_at_record="deciding", decision="done",
            rationale=dec.rationale, evidence_refs=dec.evidence_refs, diagnosis=diag,
            agent_model=getattr(self._agent, "model", "")))
        self._tick()

    def _finalize(self) -> None:
        self._store.set_state("finalizing")
        try:                                 # 收尾强制回 best 并验证就绪
            self._sb.apply(self._best_cfg)
        except Exception:                    # noqa: BLE001
            pass
        app = applies_to(self._model, self._obj, self._best_sc)
        review = review_config_diff(self._baseline, self._best_cfg)
        self._store.set_applies_to(app)
        self._store.set_config_review(review)
        self._store.set_promote_package(build_promote_package(
            model=self._model,
            baseline_config=self._baseline,
            best_config=self._best_cfg,
            applies_to=app,
            config_review=review,
            recommended_command=render_command(self._model, self._best_cfg),
        ))
        self._store.set_state("stopped" if self._stopping.is_set() else "done")

    def _tick(self) -> None:
        if self._delay and not self._stopping.is_set():
            self._stopping.wait(self._delay)
