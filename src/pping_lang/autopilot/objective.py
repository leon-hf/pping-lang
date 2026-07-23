"""目标 spec + 打分 + 胜负判定(§6.2,G1)。

字典序：SLA 闸门(硬约束)→ 主指标。`sla_ok` 用 client-side bench p99;延迟目标的
吞吐下限走独立 `objective.floor`。统一"越大越好"(延迟取负)。decide 用 noise_margin
挡住噪声内的伪胜利(记 tie)。纯函数,无副作用,可单测。

SLA 三项里只有 ttft/tpot 进闸门,e2e 仅监控上报不参与判定(2026-07-23,见 sla_ok 内注释)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

Target = Literal["throughput", "latency", "cost"]


@dataclass(frozen=True)
class SLA:
    ttft_p99_ms: float | None = None
    tpot_p99_ms: float | None = None
    e2e_p99_ms: float | None = None          # 端到端完成时间(仅监控上报,不进 sla_ok 闸门)


@dataclass(frozen=True)
class Floor:
    output_tps: float = 0.0


@dataclass(frozen=True)
class ObjectiveSpec:
    target: Target = "throughput"
    sla: SLA = field(default_factory=SLA)
    floor: Floor | None = None                  # 延迟目标时的吞吐硬下限
    latency_metric: Literal["ttft", "tpot"] = "tpot"
    gpu_count: int = 1
    noise_margin: float = 0.03                   # 收益须超此比例才算赢


@dataclass(frozen=True)
class Scorecard:
    """一次候选的标准 bench 实测 + 派生分。来自 client-side bench(§6.2)。"""

    output_tps: float = 0.0
    ttft_p99_ms: float = 0.0
    tpot_p99_ms: float = 0.0
    e2e_p99_ms: float = 0.0
    error_rate: float = 0.0
    run_meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "output_tps": self.output_tps, "ttft_p99_ms": self.ttft_p99_ms,
            "tpot_p99_ms": self.tpot_p99_ms, "e2e_p99_ms": self.e2e_p99_ms,
            "error_rate": self.error_rate, "run_meta": dict(self.run_meta),
        }


def sla_ok(sc: Scorecard, obj: ObjectiveSpec) -> bool:
    ok = True
    if obj.sla.ttft_p99_ms is not None:
        ok &= sc.ttft_p99_ms <= obj.sla.ttft_p99_ms
    if obj.sla.tpot_p99_ms is not None:
        ok &= sc.tpot_p99_ms <= obj.sla.tpot_p99_ms
    # e2e_p99_ms 不进闸门(仅监控上报,见 SLA.e2e_p99_ms 字段注释)：E2E ≈ TTFT + TPOT×输出
    # token 数,后者是负载形态的固定属性,不受 vLLM 参数影响——硬卡一个调参移不动的目标,
    # 真机复现(2026-07-23):7B-AWQ 上 chat/code 形态的默认 E2E 阈值本身就比基线还紧,
    # 基线一开局就判 -inf,后续候选无论怎么改都过不了这道闸,session 全程"无改进",
    # 但 TTFT/TPOT 实际都在变好——硬门槛把真实收益整个盖住了。
    if obj.target == "latency" and obj.floor is not None:        # 吞吐下限作硬约束
        ok &= sc.output_tps >= obj.floor.output_tps
    if sc.error_rate > 0.5:                                       # 高错误率不可接受
        ok = False
    return ok


def objective_score(sc: Scorecard, obj: ObjectiveSpec) -> float:
    """统一'越大越好';破 SLA = -inf(不可接受)。"""
    if not sla_ok(sc, obj):
        return float("-inf")
    if obj.target == "throughput":
        return sc.output_tps
    if obj.target == "cost":
        return sc.output_tps / max(1, obj.gpu_count)             # M0 单卡 = output_tps
    # latency:minimize → 取负,方向被吸收为"越大越好"
    return -(sc.tpot_p99_ms if obj.latency_metric == "tpot" else sc.ttft_p99_ms)


def decide(cand_score: float, best_score: float, noise_margin: float = 0.03) -> str:
    """kept / reverted / tie。reverted/tie 都保留旧 best。"""
    if cand_score == float("-inf"):
        return "reverted"                                        # 破 SLA / 起不来 / 高错误
    if best_score == float("-inf"):
        return "kept"                                            # 首个可行候选(基线也可能不达标)
    margin = abs(best_score) * noise_margin
    if cand_score > best_score + margin:
        return "kept"                                            # 超噪声边界才算赢
    if cand_score < best_score - margin:
        return "reverted"                                        # 真变差了(不是噪声内)→ 回滚,
                                                                   # 别标"持平"掩盖真实退化
    return "tie"                                                 # 噪声内：不替换 best


def primary_delta_pct(cand: Scorecard, best: Scorecard, obj: ObjectiveSpec) -> float | None:
    """候选相对 best-so-far 的主指标 Δ%(非相对 baseline);best 不可比时 None。"""
    bs, cs = objective_score(best, obj), objective_score(cand, obj)
    if not math.isfinite(bs) or bs == 0:
        return None
    return (cs - bs) / abs(bs) * 100.0
