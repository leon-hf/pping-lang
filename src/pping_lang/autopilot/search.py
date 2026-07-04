"""P2 measured-search candidate generation and lightweight BO ranking."""
from __future__ import annotations

from pping_lang.autopilot.action_space import knob


def _numeric_values(k, cur, first, regime: str, *, max_values: int) -> list:
    vals = [first]
    if k.kind == "float":
        step = 0.10
        nxt = float(first)
        while len(vals) < max_values:
            nxt = round(nxt + step, 3)
            if nxt > k.hi:
                break
            vals.append(nxt)
        return vals
    down = regime == "D" and k.key in ("max_num_seqs", "max_model_len")
    nxt = float(first)
    while len(vals) < max_values:
        nxt = max(k.lo, nxt / 2) if down else min(k.hi, nxt * 2)
        iv = int(round(nxt))
        if iv == vals[-1]:
            break
        vals.append(iv)
    return vals


def expand_grid_candidates(base: list[dict], current_config: dict, regime: str,
                           *, max_values_per_knob: int = 3) -> list[dict]:
    """Expand P1 one-step candidates into a small one-knob coordinate grid."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for cand in base:
        k = knob(cand["knob"])
        if not k:
            continue
        if k.kind == "choice":
            values = []
            choices = list(k.choices)
            try:
                idx = choices.index(cand["to"])
            except ValueError:
                idx = 0
            for v in choices[idx:]:
                if v != cand["from"]:
                    values.append(v)
                if len(values) >= max_values_per_knob:
                    break
        else:
            values = _numeric_values(k, cand["from"], cand["to"], regime,
                                     max_values=max_values_per_knob)
        for rank, val in enumerate(values):
            cfg = dict(current_config)
            cfg[k.key] = val
            key = (k.key, repr(val))
            if key in seen:
                continue
            seen.add(key)
            c = {**cand, "to": val, "config": cfg}
            c["p2"] = {"strategy": "grid", "rank": rank, "knob": k.key}
            out.append(c)
    return out


def bo_rank_candidates(candidates: list[dict], history: list[dict]) -> list[dict]:
    """Tiny dependency-free BO v1: prior + observed reward by knob.

    This is deliberately lightweight: it is a warm-started surrogate/ranking
    layer, not a full Gaussian-process optimizer. It uses previous decisions as
    observations so expensive evals are biased toward knobs that have helped and
    away from knobs that tied/reverted.
    """
    weights = {"kept": 1.0, "tie": -0.25, "reverted": -1.0}
    by_knob: dict[str, float] = {}
    for h in history:
        k = h.get("knob")
        if not k:
            continue
        by_knob[k] = by_knob.get(k, 0.0) + weights.get(h.get("decision"), 0.0)
    ranked: list[dict] = []
    for idx, cand in enumerate(candidates):
        prior = -float((cand.get("p2") or {}).get("rank", idx)) * 0.05
        score = prior + by_knob.get(cand["knob"], 0.0)
        c = dict(cand)
        p2 = dict(c.get("p2") or {})
        p2.update({"strategy": "bo", "acquisition": round(score, 4),
                   "observed_knob_reward": round(by_knob.get(cand["knob"], 0.0), 4)})
        c["p2"] = p2
        ranked.append(c)
    ranked.sort(key=lambda c: c["p2"]["acquisition"], reverse=True)
    return ranked


def prepare_search_candidates(base: list[dict], current_config: dict, regime: str,
                              history: list[dict], *, mode: str = "agent",
                              max_values_per_knob: int = 3) -> list[dict]:
    if mode == "agent":
        return base
    grid = expand_grid_candidates(base, current_config, regime,
                                  max_values_per_knob=max_values_per_knob)
    if mode == "grid":
        return grid
    if mode == "bo":
        return bo_rank_candidates(grid, history)
    return base
