"""P0 KV-fit predictor.

This is a cheap pre-bench filter for obvious capacity failures. It does not try
to replace vLLM's own allocator; it uses the last measured run to estimate how
many workload-shaped requests can fit in the KV pool, then annotates/prunes
candidates before the expensive sandbox launch.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

from pping_lang.autopilot.objective import Scorecard


@dataclass(frozen=True)
class KvFitEstimate:
    verdict: str
    reason: str
    predicted_kv_usage: float | None = None
    fit_concurrency: int | None = None
    workload_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"verdict": self.verdict, "reason": self.reason}
        if self.predicted_kv_usage is not None:
            out["predicted_kv_usage"] = round(self.predicted_kv_usage, 3)
        if self.fit_concurrency is not None:
            out["fit_concurrency"] = self.fit_concurrency
        if self.workload_tokens is not None:
            out["workload_tokens"] = self.workload_tokens
        return out


@dataclass(frozen=True)
class KvFitResult:
    candidates: list[dict]
    pruned: list[dict]

    def summary(self) -> dict[str, Any]:
        return {
            "kept": len(self.candidates),
            "pruned": len(self.pruned),
            "pruned_candidates": self.pruned,
        }


def _runtime_stat(sc: Scorecard | None, group: str, field: str) -> float | None:
    probe = ((sc.run_meta or {}).get("runtime_probe") if sc else None) or {}
    try:
        val = probe[group][field]
    except Exception:  # noqa: BLE001
        return None
    return float(val) if val is not None else None


def _meta_float(sc: Scorecard | None, key: str) -> float | None:
    if not sc:
        return None
    val = (sc.run_meta or {}).get(key)
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def workload_tokens(sc: Scorecard | None, config: dict) -> int | None:
    prompt = _meta_float(sc, "prompt_tokens")
    output = _meta_float(sc, "output_tokens")
    if prompt is None and config.get("max_model_len"):
        prompt = float(config["max_model_len"])
    if output is None:
        output = 16.0
    if prompt is None:
        return None
    return max(1, int(prompt + output))


def estimate_token_capacity(sc: Scorecard | None, config: dict) -> float | None:
    """Infer rough KV token-slots from the last measured bench window."""
    tokens = workload_tokens(sc, config)
    if tokens is None:
        return None
    kv_usage = _runtime_stat(sc, "kv_cache_usage", "max")
    running = _runtime_stat(sc, "running_reqs", "max")
    if kv_usage and running and kv_usage > 0:
        return (running * tokens) / kv_usage
    # Sim/offline fallback: enough for deterministic tests and a conservative
    # shape proxy when no live runtime_probe exists yet.
    util = float(config.get("gpu_memory_utilization", 0.70) or 0.70)
    return 140.0 * (util / 0.70) * tokens


def predict_kv_fit(current_config: dict, candidate_config: dict, sc: Scorecard | None) -> KvFitEstimate:
    tokens = workload_tokens(sc, candidate_config)
    max_model_len = candidate_config.get("max_model_len")
    if tokens is not None and max_model_len and int(max_model_len) < tokens:
        return KvFitEstimate(
            "reject",
            f"workload needs about {tokens} tokens but max_model_len={int(max_model_len)}",
            workload_tokens=tokens,
        )

    cap = estimate_token_capacity(sc, current_config)
    if cap is None or tokens is None:
        return KvFitEstimate("unknown", "insufficient runtime evidence for KV-fit prediction")

    current_seqs = float(current_config.get("max_num_seqs") or 1)
    target = float(candidate_config.get("max_num_seqs") or current_seqs)
    if target <= current_seqs:
        return KvFitEstimate(
            "allow",
            "candidate does not raise max_num_seqs; skip hard KV-fit rejection",
            fit_concurrency=max(1, floor(cap / tokens)) if tokens > 0 else None,
            workload_tokens=tokens,
        )

    cur_util = float(current_config.get("gpu_memory_utilization", 0.70) or 0.70)
    cand_util = float(candidate_config.get("gpu_memory_utilization", cur_util) or cur_util)
    if cur_util > 0:
        cap *= cand_util / cur_util

    needed = target * tokens
    predicted = needed / cap if cap > 0 else None
    fit = max(1, floor(cap / tokens)) if tokens > 0 else None
    if predicted is None:
        return KvFitEstimate("unknown", "invalid KV-fit capacity estimate", workload_tokens=tokens)
    if predicted > 1.05:
        return KvFitEstimate(
            "reject",
            f"predicted KV usage {predicted:.2f} exceeds pool; fit concurrency about {fit}",
            predicted_kv_usage=predicted,
            fit_concurrency=fit,
            workload_tokens=tokens,
        )
    if predicted > 0.90:
        return KvFitEstimate(
            "warn",
            f"predicted KV usage {predicted:.2f} is close to capacity",
            predicted_kv_usage=predicted,
            fit_concurrency=fit,
            workload_tokens=tokens,
        )
    return KvFitEstimate(
        "allow",
        f"predicted KV usage {predicted:.2f} fits pool",
        predicted_kv_usage=predicted,
        fit_concurrency=fit,
        workload_tokens=tokens,
    )


def evaluate_kvfit(candidates: list[dict], current_config: dict, sc: Scorecard | None) -> KvFitResult:
    """Annotate candidates with P0 evidence and separate obvious KV impossibilities."""
    kept: list[dict] = []
    pruned: list[dict] = []
    for cand in candidates:
        est = predict_kv_fit(current_config, cand.get("config") or {}, sc)
        tagged = dict(cand)
        tagged["p0"] = est.to_dict()
        if est.verdict == "reject":
            key = tagged.get("knob")
            cfg = tagged.get("config") or {}
            pruned.append({
                "knob": key,
                "from": tagged.get("from", current_config.get(key)),
                "to": tagged.get("to", cfg.get(key)),
                "p0": tagged["p0"],
            })
        else:
            kept.append(tagged)
    return KvFitResult(kept, pruned)


def apply_kvfit(candidates: list[dict], current_config: dict, sc: Scorecard | None) -> list[dict]:
    """Backward-compatible helper: return only candidates that survive P0."""
    return evaluate_kvfit(candidates, current_config, sc).candidates
