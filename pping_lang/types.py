"""数据模型 — see pre-impl-rfc §1.

热路径数据载荷使用 frozen + slots 减少内存开销并防止误改。
所有 metric value 都是 float（DuckDB 用 DOUBLE 列），统一处理。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

Severity = Literal["info", "warning", "critical"]


@dataclass(slots=True, frozen=True)
class MetricPoint:
    """单个采样点。

    Note:
        instance_id 故意不在这里 —— 由 Sink 在出站边界统一打上，避免进程内重复存储。
    """

    ts_ns: int
    name: str
    value: float
    engine_idx: int = 0
    gpu_idx: int = -1
    labels: Mapping[str, str] | None = None


@dataclass(slots=True, frozen=True)
class Diagnosis:
    """规则触发输出。message 在生产端预渲染，避免下游模板逻辑重复。"""

    ts_ns: int
    rule_id: str
    severity: Severity
    triggered_value: float
    threshold: float
    window_seconds: int
    message: str
    suggestion: str
    engine_idx: int = 0
    gpu_idx: int = -1
    context: Mapping[str, float] | None = None
