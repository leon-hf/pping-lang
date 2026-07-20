"""业务形态 WorkloadSpec(M1 优先级 5):bench 负载 + 默认 SLA 一张表。

动机(2026-07-19 真机 session `ap-20260719-004104`):泛目标(吞吐优先)+ 泛负载
(c32 mixed-short)在真实轻载下 `load_limited` 交白卷——**调优空间 = 具体业务负载 ×
具体配置的不匹配**,泛形态造不出也测不准。形态与首页实时 tab 同套词汇
(chat/rag/agent/reasoning/code/custom),让 bench、SLA、诊断 regime 说同一种负载语言。

优先级:显式 flag / 表单值 > 形态默认 > 各调用方原有兜底;`custom` = 全手动透传
(形态不施加任何影响,行为同引入形态之前)。
"""
from __future__ import annotations

# SLA 数值沿用 dashboard WORKLOAD_SLA 的产品共识;负载参数按 runw 0.5B~7B 真机
# 校准区间给默认——喂得饱(消除 load_limited 假阳性)又不至于把分钟预算烧在排队上。
WORKLOAD_SHAPES: dict[str, dict] = {
    "chat": {
        "label": "对话(短进短出)",
        "bench": {"prompt_tokens": 500, "output_tokens": 128, "concurrency": 64},
        "sla": {"ttft_p99_ms": 1000, "tpot_p99_ms": 50},
    },
    "rag": {
        "label": "RAG 问答(长 prompt)",
        "bench": {"prompt_tokens": 4000, "output_tokens": 256, "concurrency": 16},
        "sla": {"ttft_p99_ms": 3000, "tpot_p99_ms": 50},
    },
    "agent": {
        "label": "Agent 工具循环",
        "bench": {"prompt_tokens": 2000, "output_tokens": 512, "concurrency": 32},
        "sla": {"ttft_p99_ms": 1000, "tpot_p99_ms": 50},
    },
    "reasoning": {
        "label": "长推理(长输出)",
        "bench": {"prompt_tokens": 1000, "output_tokens": 4096, "concurrency": 16},
        "sla": {"ttft_p99_ms": 1000, "tpot_p99_ms": 30},
    },
    "code": {
        "label": "代码补全(硬延迟闸)",
        "bench": {"prompt_tokens": 300, "output_tokens": 128, "concurrency": 16},
        "sla": {"ttft_p99_ms": 100, "tpot_p99_ms": 20},
    },
    "custom": {"label": "自定义(全手动)", "bench": {}, "sla": {}},
}

DEFAULT_SHAPE = "chat"


def shape_bench(name: str) -> dict:
    """形态的 bench 负载默认(prompt_tokens/output_tokens/concurrency);custom/未知 → {}。"""
    return dict((WORKLOAD_SHAPES.get(name) or {}).get("bench") or {})


def shape_sla(name: str) -> dict:
    """形态的 SLA 默认(ttft_p99_ms/tpot_p99_ms);custom/未知 → {}(透传,不加闸)。"""
    return dict((WORKLOAD_SHAPES.get(name) or {}).get("sla") or {})


def resolve_bench(shape: str, explicit: dict) -> dict:
    """显式值 > 形态默认,返回只含非 None 的 bench 参数(调用方再叠各自兜底)。"""
    out: dict = {}
    for k in ("prompt_tokens", "output_tokens", "concurrency"):
        v = explicit.get(k)
        if v is None:
            v = shape_bench(shape).get(k)
        if v is not None:
            out[k] = v
    return out


def resolve_sla(shape: str, ttft: float | None = None, tpot: float | None = None):
    """显式值 > 形态默认;都没给 → (None, None)(不加闸,同引入形态前)。"""
    s = shape_sla(shape)
    return (ttft if ttft is not None else s.get("ttft_p99_ms"),
            tpot if tpot is not None else s.get("tpot_p99_ms"))
