"""Configuration diff review for promote-to-prod preparation."""
from __future__ import annotations

from typing import Any

from pping_lang.autopilot.action_space import knob


def review_config_diff(before: dict, after: dict) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old == new:
            continue
        k = knob(key)
        output_impact = k.output_impact if k else "unknown"
        risk: list[str] = []
        if output_impact != "none":
            risk.append("may affect model outputs; requires quality gate")
        if key == "max_model_len" and new is not None and old is not None and float(new) < float(old):
            risk.append("reduces supported context length")
        if key == "gpu_memory_utilization" and new is not None and old is not None and float(new) > float(old):
            risk.append("uses more GPU memory; check co-tenancy/OOM headroom")
        if key == "max_num_seqs" and new is not None and old is not None and float(new) > float(old):
            risk.append("admits more running requests; may increase KV pressure and tail latency")
        changes.append({
            "key": key,
            "flag": k.flag if k else None,
            "from": old,
            "to": new,
            "output_impact": output_impact,
            "lever": k.lever if k else "",
            "risk_notes": risk,
        })
    return {
        "changes": changes,
        "requires_quality_gate": any(c["output_impact"] != "none" for c in changes),
        "risk_count": sum(len(c["risk_notes"]) for c in changes),
    }
