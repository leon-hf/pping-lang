"""HTTP API request/response schemas — Pydantic models。

仅在 POST/PUT 边界上做校验。读路径返回 dict 即可（FastAPI 自动 JSON 序列化）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ConditionIn(BaseModel):
    metric: str
    op: Literal["<", "<=", ">", ">=", "==", "!="]
    threshold: float
    window_seconds: int = Field(gt=0)
    aggregation: Literal[
        "avg", "p50", "p95", "p99", "max", "min", "count"
    ] = "avg"


class RuleIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1)
    severity: Literal["info", "warning", "critical"]
    category: str = Field(min_length=1)
    condition: ConditionIn
    message: str
    suggestion: str
    enabled: bool = True


class RuleTestRequest(BaseModel):
    """POST /api/rules/{id}/test 的请求体（可选——空表示用 store 中的规则）。"""
    override: RuleIn | None = None
