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
    # 认识论分类(与 category 正交);见 rules/schema.py。默认 derived。
    claim: Literal[
        "measurement", "derived", "hypothesis", "suggestion", "unavailable"
    ] = "derived"
    enabled: bool = True


class RuleTestRequest(BaseModel):
    """POST /api/rules/{id}/test 的请求体（可选——空表示用 store 中的规则）。"""
    override: RuleIn | None = None


class BenchStartIn(BaseModel):
    """POST /api/bench/start 的请求体——静态压测场景参数。

    duration_s 与 num_requests 互斥（与 StaticScenario.validate 一致）。
    服务端会进一步通过 StaticScenario.validate() 走全套校验。
    """
    name: str | None = None
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_tokens: int = Field(default=500, gt=0)
    output_tokens: int = Field(default=100, gt=0)
    concurrency: int = Field(default=16, gt=0)
    duration_s: int | None = 60
    num_requests: int | None = None
    warmup_s: int = Field(default=5, ge=0)
    timeout_s: float = Field(default=30.0, gt=0)
    api: Literal["chat", "completions"] = "chat"
    sweep: str | None = None  # e.g. "concurrency=1,2,4,8"
    slo: str | None = None    # constraint spec, e.g. "ttft:p99<500ms;tpot:p99<50ms"
    prompt_source: str = "synthetic"  # synthetic | builtin:<name> | file:<path>
