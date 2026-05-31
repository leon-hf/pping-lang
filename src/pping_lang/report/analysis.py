"""报告分析逻辑 — 各 section 的数据提取。

所有查询 defensive：DuckDB 表不存在 / 数据缺失 / 派生指标无 peak 都返回空/None。
"""
from __future__ import annotations

import logging
from typing import Any

from pping_lang.metrics_catalog import M

logger = logging.getLogger(__name__)


def _scalar(conn: Any, sql: str, params: list) -> float | int | None:
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def executive_summary(conn: Any, since_ns: int) -> dict[str, Any]:
    """6 个 marquee KPI."""
    ttft_p50 = _scalar(
        conn,
        "SELECT QUANTILE_CONT(value, 0.5) FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
        [M.VLLM_REQ_TTFT_MS, since_ns],
    )
    ttft_p99 = _scalar(
        conn,
        "SELECT QUANTILE_CONT(value, 0.99) FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
        [M.VLLM_REQ_TTFT_MS, since_ns],
    )
    return {
        "total_requests": _scalar(
            conn,
            "SELECT COUNT(*) FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
            [M.VLLM_REQ_E2E_LATENCY_MS, since_ns],
        ) or 0,
        "ttft_p50_ms": ttft_p50,
        "ttft_p99_ms": ttft_p99,
        "gpu_util_avg_pct": _scalar(
            conn,
            "SELECT AVG(value) FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
            [M.GPU_UTIL_PCT, since_ns],
        ),
        "mfu_avg_pct": (
            (lambda v: v * 100 if v is not None else None)(_scalar(
                conn,
                "SELECT AVG(value) FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
                [M.VLLM_PERF_MFU_RATIO, since_ns],
            ))
        ),
        "padding_ratio_avg_pct": (
            (lambda v: v * 100 if v is not None else None)(_scalar(
                conn,
                "SELECT AVG(value) FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
                [M.VLLM_CUDAGRAPH_PADDING_RATIO, since_ns],
            ))
        ),
        "total_diagnoses": _scalar(
            conn, "SELECT COUNT(*) FROM diagnoses WHERE ts_ns >= ?", [since_ns],
        ) or 0,
    }


def top_diagnoses(conn: Any, since_ns: int, limit: int = 5) -> list[dict[str, Any]]:
    """按 severity 优先 + 触发次数排序，返回 top N。"""
    try:
        rows = conn.execute(
            """
            SELECT rule_id, severity, COUNT(*) AS cnt,
                   MAX(message) AS message, MAX(suggestion) AS suggestion,
                   MAX(ts_ns) AS last_ts, AVG(triggered_value) AS avg_val,
                   MAX(threshold) AS threshold
            FROM diagnoses WHERE ts_ns >= ?
            GROUP BY rule_id, severity
            ORDER BY
                CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                cnt DESC
            LIMIT ?
            """,
            [since_ns, limit],
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "rule_id": r[0],
            "severity": r[1],
            "fire_count": r[2],
            "message": r[3],
            "suggestion": r[4],
            "last_ts_ns": r[5],
            "avg_value": r[6],
            "threshold": r[7],
        }
        for r in rows
    ]


def trend_data(conn: Any, since_ns: int) -> dict[str, list[tuple[float, float]]]:
    """关键 metric 时序，用于趋势图。返回 {label: [(ts_relative_s, value), ...]}"""
    series = {}
    metrics = [
        (M.GPU_UTIL_PCT, "GPU 利用率 %"),
        (M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO, "KV cache (×100)"),
        (M.VLLM_PERF_MFU_RATIO, "MFU (×100)"),
        (M.VLLM_CUDAGRAPH_PADDING_RATIO, "Padding ratio (×100)"),
    ]
    for name, label in metrics:
        try:
            rows = conn.execute(
                "SELECT ts_ns, value FROM metrics "
                "WHERE metric_name = ? AND ts_ns >= ? ORDER BY ts_ns",
                [name, since_ns],
            ).fetchall()
        except Exception:
            continue
        if not rows:
            continue
        # ratios → percent for visual scale parity
        scale = 100 if "ratio" in name else 1
        series[label] = [
            ((r[0] - since_ns) / 1e9, r[1] * scale) for r in rows
        ]
    return series


def roofline_data(conn: Any, since_ns: int) -> list[dict[str, float]]:
    """Roofline 散点：每个 step 一个 (arithmetic_intensity, throughput) 点。

    需要 perf_stats 数据：flops, read+write bytes。
    arithmetic_intensity = flops / bytes
    throughput = flops / dt (per step)
    """
    try:
        # Pair flops with read+write bytes by ts_ns (same step)
        rows = conn.execute(
            """
            WITH per_step AS (
                SELECT ts_ns,
                       MAX(CASE WHEN metric_name = ? THEN value END) AS flops,
                       MAX(CASE WHEN metric_name = ? THEN value END) AS read_b,
                       MAX(CASE WHEN metric_name = ? THEN value END) AS write_b
                FROM metrics
                WHERE metric_name IN (?, ?, ?) AND ts_ns >= ?
                GROUP BY ts_ns
            )
            SELECT ts_ns, flops, read_b + COALESCE(write_b, 0) AS bytes
            FROM per_step
            WHERE flops IS NOT NULL AND flops > 0
              AND read_b IS NOT NULL AND read_b > 0
            ORDER BY ts_ns
            """,
            [
                M.VLLM_PERF_FLOPS_PER_GPU,
                M.VLLM_PERF_READ_BYTES_PER_GPU,
                M.VLLM_PERF_WRITE_BYTES_PER_GPU,
                M.VLLM_PERF_FLOPS_PER_GPU,
                M.VLLM_PERF_READ_BYTES_PER_GPU,
                M.VLLM_PERF_WRITE_BYTES_PER_GPU,
                since_ns,
            ],
        ).fetchall()
    except Exception:
        return []

    points = []
    last_ts = None
    for ts, flops, total_b in rows:
        if last_ts is None:
            last_ts = ts
            continue
        dt_s = (ts - last_ts) / 1e9
        last_ts = ts
        if dt_s <= 0 or total_b <= 0:
            continue
        ai = flops / total_b
        throughput_tflops = flops / dt_s / 1e12
        points.append({"ai": ai, "throughput_tflops": throughput_tflops, "ts_ns": ts})
    return points


def rules_summary(conn: Any, since_ns: int) -> list[dict[str, Any]]:
    """每条规则的触发次数 + 最近触发时间。"""
    try:
        rows = conn.execute(
            """
            SELECT rule_id, severity, COUNT(*) AS cnt, MAX(ts_ns) AS last_ts
            FROM diagnoses WHERE ts_ns >= ?
            GROUP BY rule_id, severity
            ORDER BY cnt DESC
            """,
            [since_ns],
        ).fetchall()
    except Exception:
        return []
    return [
        {"rule_id": r[0], "severity": r[1], "fire_count": r[2], "last_ts_ns": r[3]}
        for r in rows
    ]


def config_audit(conn: Any, since_ns: int, vllm_config: Any) -> dict[str, Any]:
    """vLLM 配置 dump + 启发式建议。

    无 vllm_config（独立模式）只返回 audit suggestions。
    """
    config_dict = _config_to_dict(vllm_config)
    suggestions = _audit_heuristics(conn, since_ns, config_dict)
    return {"config": config_dict, "suggestions": suggestions}


def _config_to_dict(vllm_config: Any) -> dict[str, Any] | None:
    """Best-effort extraction of vLLM config fields. Returns None if unavailable."""
    if vllm_config is None:
        return None
    out = {}
    # Common vLLM v1 config layout: vllm_config.<sub_config>.<field>
    for sub_name in ("model_config", "scheduler_config", "cache_config", "parallel_config"):
        sub = getattr(vllm_config, sub_name, None)
        if sub is None:
            continue
        sub_data = {}
        for attr in dir(sub):
            if attr.startswith("_") or callable(getattr(sub, attr, None)):
                continue
            v = getattr(sub, attr, None)
            if isinstance(v, (str, int, float, bool)) or v is None:
                sub_data[attr] = v
        if sub_data:
            out[sub_name] = sub_data
    return out or None


def _audit_heuristics(conn: Any, since_ns: int, config: dict | None) -> list[dict[str, str]]:
    """简单启发式：根据观测数据 + 配置建议调参。"""
    suggestions = []

    # 1. batch 长期 ≤ 1 → 建议 chunked_prefill
    avg_batch = _scalar(
        conn,
        "SELECT AVG(value) FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
        [M.VLLM_SCHEDULER_RUNNING_REQS, since_ns],
    )
    if avg_batch is not None and avg_batch <= 2.0:
        suggestions.append({
            "title": "并发请求数偏低",
            "evidence": f"running_reqs 平均 {avg_batch:.1f}，未发挥 batching 优势",
            "action": "增加客户端并发；或检查 chunked_prefill 是否已开",
        })

    # 2. prefix cache hit < 10% → 建议开 prefix caching 或检查 prompt 模板
    hit_rate = _scalar(
        conn,
        "SELECT AVG(value) FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
        [M.VLLM_SCHEDULER_PREFIX_CACHE_HIT_RATIO, since_ns],
    )
    if hit_rate is not None and hit_rate < 0.10:
        suggestions.append({
            "title": "prefix cache 命中率低",
            "evidence": f"hit ratio = {hit_rate:.0%}",
            "action": "确认 enable_prefix_caching=True；检查 prompt 模板是否有公共前缀",
        })

    # 3. KV cache 长期 > 80% → 显存压力大
    kv_avg = _scalar(
        conn,
        "SELECT AVG(value) FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
        [M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO, since_ns],
    )
    if kv_avg is not None and kv_avg > 0.80:
        suggestions.append({
            "title": "KV cache 持续高位",
            "evidence": f"使用率平均 {kv_avg:.0%}，逼近抢占阈值",
            "action": "降 max_num_seqs 或 max_model_len；或升级显存更大 GPU",
        })

    # 4. padding > 30%
    pad_avg = _scalar(
        conn,
        "SELECT AVG(value) FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
        [M.VLLM_CUDAGRAPH_PADDING_RATIO, since_ns],
    )
    if pad_avg is not None and pad_avg > 0.30:
        suggestions.append({
            "title": "CUDA graph padding 偏高",
            "evidence": f"padding ratio 平均 {pad_avg:.0%}，约 {pad_avg:.0%} 算力浪费",
            "action": "调小 max_num_seqs；考虑 PIECEWISE 模式细粒度 capture",
        })

    return suggestions
