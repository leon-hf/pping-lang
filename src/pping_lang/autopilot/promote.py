"""Promote package builder.

Autopilot can recommend a production command, but applying it is deliberately
manual. This module turns the final best config into a reviewable package for
UI/API surfaces: diff, risks, production command, rollback command and a human
confirmation checklist.
"""
from __future__ import annotations

from pping_lang.autopilot.action_space import render_command


def _risk_notes(review: dict) -> list[str]:
    out: list[str] = []
    for change in review.get("changes") or []:
        key = change.get("key", "unknown")
        for note in change.get("risk_notes") or []:
            out.append(f"{key}: {note}")
    if review.get("requires_quality_gate"):
        out.append("quality gate required: at least one recommended change can affect output equivalence")
    return out


def build_promote_package(*, model: str, baseline_config: dict, best_config: dict,
                          applies_to: dict, config_review: dict,
                          recommended_command: str | None = None) -> dict:
    """Build a manual promote-to-prod package.

    No side effects: this never mutates production. The returned object is safe
    to expose in status/UI and later wire to a confirmation workflow.
    """
    changed = dict(baseline_config) != dict(best_config)
    production_command = recommended_command or render_command(model, best_config)
    rollback_command = render_command(model, baseline_config)
    state = "ready" if changed else "noop"
    risks = _risk_notes(config_review)
    if not risks:
        risks = ["no specific config risks flagged by review_config_diff"]
    return {
        "state": state,
        "manual_only": True,
        "requires_confirmation": changed,
        "applied": False,
        "production_command": production_command,
        "rollback_command": rollback_command,
        "diff": config_review,
        "risk_notes": risks,
        "applies_to": applies_to,
        "checklist": [
            "confirm applies_to matches the production model/GPU/vLLM/workload",
            "drain or shift traffic before replacing the vLLM serve command",
            "apply production_command through the deployment system",
            "health check /v1/models and a smoke chat completion",
            "watch TTFT/TPOT/error rate and rollback_command on regression",
        ],
        "message": (
            "No config change beat the baseline; keep current production command."
            if not changed else
            "Manual promote package is ready; production was not modified by Autopilot."
        ),
    }
