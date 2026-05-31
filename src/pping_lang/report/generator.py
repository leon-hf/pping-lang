"""Report generator — 把分析数据 + plotly 图表填进 Jinja2 模板，输出单 HTML。

CDN plotly：默认 < 30KB HTML。
inline plotly：完全自包含，~3.5MB（邮件友好性 vs 离线可看的取舍——v0.1 默认 CDN）。
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import plotly.io as pio
from jinja2 import Environment, FileSystemLoader, select_autoescape

from pping_lang.api.queries import open_conn
from pping_lang.hardware import GPUPeak
from pping_lang.report.analysis import (
    config_audit,
    executive_summary,
    roofline_data,
    rules_summary,
    top_diagnoses,
    trend_data,
)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


def generate_report(
    db_path: str,
    instance_id: str,
    *,
    seconds: int = 86400,
    version: str = "0.0.1.dev0",
    vllm_config: Any = None,
    gpu_peak: GPUPeak | None = None,
    plotly_mode: str = "cdn",  # "cdn" or "inline"
) -> str:
    """Return self-contained HTML report. Caller writes/downloads as needed."""
    now_ns = time.monotonic_ns()
    since_ns = now_ns - int(seconds * 1e9)

    conn = open_conn(db_path)
    try:
        exec_summary = executive_summary(conn, since_ns)
        diagnoses = top_diagnoses(conn, since_ns)
        trends = trend_data(conn, since_ns)
        roof_pts = roofline_data(conn, since_ns)
        audit = config_audit(conn, since_ns, vllm_config)
        rules = rules_summary(conn, since_ns)
    finally:
        conn.close()

    # === Charts ===
    # First chart includes plotly.js (inline or CDN); subsequent ones reference it
    plotly_first_include = (
        "cdn" if plotly_mode == "cdn" else "inline"
    )
    trend_html = _build_trend_chart(trends, plotly_first_include)
    # Subsequent charts skip the library load (already loaded by trend chart)
    roof_html = _build_roofline_chart(roof_pts, gpu_peak)

    template = _env.get_template("report.html.j2")
    return template.render(
        instance_id=instance_id,
        version=version,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        window_seconds=seconds,
        window_label=_human_window(seconds),
        executive=exec_summary,
        diagnoses=diagnoses,
        trend_html=trend_html,
        roof_html=roof_html,
        config_data=audit["config"],
        audit_suggestions=audit["suggestions"],
        rules_summary=rules,
        has_trend_data=bool(trends),
        has_roof_data=bool(roof_pts),
        plotly_mode=plotly_mode,
    )


def _human_window(seconds: int) -> str:
    if seconds < 60:
        return f"最近 {seconds} 秒"
    if seconds < 3600:
        return f"最近 {seconds // 60} 分钟"
    if seconds < 86400:
        return f"最近 {seconds // 3600} 小时"
    return f"最近 {seconds // 86400} 天"


def _build_trend_chart(
    series: dict[str, list[tuple[float, float]]],
    plotly_include: str,
) -> str:
    if not series:
        return ""
    fig = go.Figure()
    for label, points in series.items():
        x = [p[0] for p in points]
        y = [p[1] for p in points]
        fig.add_trace(go.Scatter(x=x, y=y, name=label, mode="lines", line=dict(width=1.5)))
    fig.update_layout(
        height=400,
        margin=dict(t=20, l=50, r=20, b=40),
        xaxis_title="秒（窗口起点为 0）",
        yaxis_title="% / ratio×100",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="#fafafa",
    )
    return pio.to_html(
        fig,
        include_plotlyjs=plotly_include,
        full_html=False,
        config={"displayModeBar": False},
    )


def _build_roofline_chart(
    points: list[dict[str, float]],
    peak: GPUPeak | None,
) -> str:
    if not points:
        return ""
    ai = [p["ai"] for p in points]
    tp = [p["throughput_tflops"] for p in points]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ai, y=tp, mode="markers", name="实测",
        marker=dict(color="#0ea5e9", size=6, opacity=0.6),
    ))
    if peak is not None:
        # Memory-bound roof: y = peak_bw_gbs * x * 1e9 / 1e12 = peak_bw_gbs * x / 1000
        # Compute-bound roof: y = peak_bf16_tflops
        # Intersection: peak_bf16_tflops = peak_bw_gbs * x / 1000
        # → x_knee = peak_bf16_tflops * 1000 / peak_bw_gbs
        max_ai = max(ai)
        x_knee = peak.bf16_tflops * 1000 / peak.mem_bw_gbs
        x_max = max(max_ai * 1.5, x_knee * 1.5, 100)
        x_axis = [0.1, x_knee, x_max]
        y_axis = [
            min(peak.mem_bw_gbs * 0.1 / 1000, peak.bf16_tflops),
            peak.bf16_tflops,
            peak.bf16_tflops,
        ]
        fig.add_trace(go.Scatter(
            x=x_axis, y=y_axis, mode="lines",
            name=f"理论上界 (峰值 {peak.bf16_tflops:.0f} TFLOPS / {peak.mem_bw_gbs:.0f} GB/s)",
            line=dict(color="#ef4444", dash="dash", width=1.5),
        ))
    fig.update_layout(
        height=400,
        margin=dict(t=20, l=50, r=20, b=40),
        xaxis_title="Arithmetic Intensity (FLOP / Byte)",
        yaxis_title="实测 throughput (TFLOPS)",
        xaxis_type="log",
        yaxis_type="log",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="#fafafa",
    )
    return pio.to_html(
        fig,
        include_plotlyjs=False,  # already loaded by trend chart
        full_html=False,
        config={"displayModeBar": False},
    )
