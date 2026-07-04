"""DiagnosisConfig —— 诊断规则的集中配置(SLA + 所有阈值)。

见 `_design-notes/诊断规则-方法论映射到信号.md`。

设计铁律:**阈值绝不内嵌进规则**,全部集中在这里;各规则只「引用」配置项。
换 workload / 改 SLA / 阈值随硬件代际演进时,改一处即可(防 §7「阈值会过期」)。

环境变量:
- `PPING_LANG_DIAGNOSIS_CONFIG`: 指向 JSON 文件,字段同 `DiagnosisConfig`;加载后覆盖默认。
  只给 `workload_form` 也行 —— 会带出该形态的 SLA 默认。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

WorkloadForm = Literal["chat", "rag", "agent", "reasoning", "code", "custom"]
WORKLOAD_FORMS: tuple[str, ...] = (
    "chat", "rag", "agent", "reasoning", "code", "custom",
)

# 各业务形态的 SLA 默认 (TTFT_p99_ms, TPOT_p99_ms) —— 见 §12 黄金阈值表 + 业界惯例。
# 选了 workload_form 就带出这套默认;仍可被显式字段覆盖。
_WORKLOAD_SLA: dict[str, tuple[float, float]] = {
    "chat":      (1000.0, 50.0),
    "rag":       (3000.0, 50.0),
    "agent":     (1000.0, 50.0),
    "reasoning": (1000.0, 30.0),
    "code":      (100.0, 20.0),
    "custom":    (2000.0, 50.0),
}


@dataclass(frozen=True)
class DiagnosisConfig:
    """诊断规则的集中配置。规则只读这里,不内嵌魔数。"""

    workload_form: WorkloadForm = "custom"
    # --- SLA(随 workload 带默认,可覆盖)---
    sla_ttft_p99_ms: float = 2000.0      # S1
    sla_tpot_p99_ms: float = 50.0        # S2
    # --- 阈值(各规则引用;名称对应规则)---
    long_prompt_tokens: int = 2048       # (保留:历史字段)
    waiting_reqs: float = 50.0           # (保留:历史字段)
    min_running_reqs: float = 0.5        # A 空载守卫:窗口内平均在跑请求数下限(挡掉"空载也双低"误报)
    mbu_low_pct: float = 50.0            # A 远离带宽屋顶:NVML HBM 控制器繁忙% < 此(有界 0-100)
    stall_scheduler_slack_pct: float = 30.0   # A 内核佐证手段:warp 就绪未选中(scheduler_slack)占 stall%
    mbu_high_pct: float = 85.0           # B 贴带宽屋顶:NVML HBM 控制器繁忙% > 此(有界;实测 perf-MBU 因 L2 复用无界,弃用作阈)
    stall_memory_throttle_pct: float = 25.0   # B 内核佐证:访存管线 throttle 占 stall%
    stall_memory_dep_pct: float = 25.0   # B 内核佐证:访存延迟(long_scoreboard)占 stall%
    stall_math_pipe_pct: float = 25.0    # C 内核佐证:计算管线(FMA/ALU/Tensor)打满占 stall%
    batch_small_reqs: float = 4.0        # (保留:历史字段)
    mfu_low_ratio: float = 0.20          # A 算力利用低(配 MBU 低 = 双低 / 喂不饱)
    mfu_high_ratio: float = 0.50         # C 算力打满(算力墙)
    tail_ratio: float = 5.0              # (保留:历史字段)
    kv_pressure_ratio: float = 0.90      # D KV 用量
    prefix_hit_low: float = 0.10         # (保留:历史字段)
    weights_hbm_ratio: float = 0.90      # (保留:历史字段)


def default_config(workload_form: str = "custom") -> DiagnosisConfig:
    """按 workload 形态带出 SLA 默认(其余阈值用 dataclass 默认)。"""
    form = workload_form if workload_form in _WORKLOAD_SLA else "custom"
    ttft, tpot = _WORKLOAD_SLA[form]
    return DiagnosisConfig(
        workload_form=form, sla_ttft_p99_ms=ttft, sla_tpot_p99_ms=tpot,
    )


def validate_config(cfg: DiagnosisConfig) -> None:
    """非法配置直接抛 ValueError(挡住手写 JSON 的笔误)。"""
    if cfg.workload_form not in WORKLOAD_FORMS:
        raise ValueError(
            f"unknown workload_form {cfg.workload_form!r}; must be one of {WORKLOAD_FORMS}"
        )
    for f in ("sla_ttft_p99_ms", "sla_tpot_p99_ms", "long_prompt_tokens",
              "waiting_reqs", "batch_small_reqs", "tail_ratio", "min_running_reqs"):
        if getattr(cfg, f) <= 0:
            raise ValueError(f"{f} must be > 0")
    for f in ("mbu_low_pct", "mbu_high_pct", "stall_memory_throttle_pct", "stall_memory_dep_pct",
              "stall_math_pipe_pct", "stall_scheduler_slack_pct"):
        if not 0 < getattr(cfg, f) <= 100:
            raise ValueError(f"{f} must be in (0, 100]")
    for f in ("mfu_low_ratio", "mfu_high_ratio",
              "kv_pressure_ratio", "prefix_hit_low", "weights_hbm_ratio"):
        if not 0 < getattr(cfg, f) <= 1:
            raise ValueError(f"{f} must be in (0, 1]")
    if cfg.mbu_low_pct >= cfg.mbu_high_pct:
        raise ValueError("mbu_low_pct 须 < mbu_high_pct(否则'双低'与'贴屋顶'区间重叠)")
    if cfg.mfu_low_ratio >= cfg.mfu_high_ratio:
        raise ValueError("mfu_low_ratio must be < mfu_high_ratio(否则'喂不饱'与'算力墙'区间重叠)")


def from_dict(d: dict) -> DiagnosisConfig:
    """从 dict 构建:先按 workload_form 取默认,再覆盖给定字段,最后校验。"""
    form = d.get("workload_form", "custom")
    base = default_config(form)
    known = {f.name for f in fields(DiagnosisConfig)}
    overrides = {k: v for k, v in d.items() if k in known and k != "workload_form"}
    cfg = replace(base, **overrides)
    validate_config(cfg)
    return cfg


def to_dict(cfg: DiagnosisConfig) -> dict:
    return asdict(cfg)


def load_config() -> DiagnosisConfig:
    """从 `PPING_LANG_DIAGNOSIS_CONFIG`(JSON)加载;没有/出错则回退 custom 默认。"""
    path = os.environ.get("PPING_LANG_DIAGNOSIS_CONFIG")
    if not path:
        return default_config("custom")
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("DiagnosisConfig JSON 必须是 object")
        cfg = from_dict(raw)
        logger.info(
            "[pping-lang] loaded DiagnosisConfig: workload=%s ttft_sla=%.0fms tpot_sla=%.0fms",
            cfg.workload_form, cfg.sla_ttft_p99_ms, cfg.sla_tpot_p99_ms,
        )
        return cfg
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[pping-lang] failed to load DiagnosisConfig from %s: %s; using custom defaults",
            path, e,
        )
        return default_config("custom")
