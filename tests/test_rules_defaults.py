"""默认规则集合 — 数量、指标白名单、消息模板格式可渲染。"""
from __future__ import annotations

import pytest

from pping_lang.metrics_catalog import ALLOWED_METRICS
from pping_lang.rules.defaults import DEFAULT_RULES
from pping_lang.rules.schema import validate_rule


def test_default_count_is_ten():
    """v0.1 内置 10 条（11 - ttft-tpot-imbalance 推到 v0.2 复合条件）。"""
    assert len(DEFAULT_RULES) == 10


def test_default_ids_unique():
    ids = [r.id for r in DEFAULT_RULES]
    assert len(ids) == len(set(ids)), f"duplicate ids in defaults: {ids}"


def test_marquee_rules_present():
    """marquee 卖点规则必须在内置中。"""
    ids = {r.id for r in DEFAULT_RULES}
    assert "high-cudagraph-padding" in ids  # GPU duty-cycle 揭穿
    assert "low-mfu" in ids                  # 计算资源浪费
    assert "memory-bw-saturated" in ids      # roofline 上沿


def test_all_defaults_validate():
    for r in DEFAULT_RULES:
        validate_rule(r)


def test_all_metrics_in_allowlist():
    """所有规则引用的 metric 必须在 metrics_catalog 白名单内。"""
    for r in DEFAULT_RULES:
        assert r.condition.metric in ALLOWED_METRICS, (
            f"rule {r.id} references unknown metric {r.condition.metric}"
        )


@pytest.mark.parametrize("rule", DEFAULT_RULES, ids=lambda r: r.id)
def test_message_template_renders(rule):
    """所有 message 模板必须能用标准变量 {value}/{threshold}/{window} 渲染。"""
    rendered = rule.message.format(
        value=rule.condition.threshold * 0.5,  # arbitrary plausible value
        threshold=rule.condition.threshold,
        window=rule.condition.window_seconds,
    )
    assert rendered  # non-empty
    assert "{" not in rendered  # all placeholders substituted


def test_severity_distribution():
    """至少有 1 条 critical（preemption-spike）和若干 warning。"""
    sevs = [r.severity for r in DEFAULT_RULES]
    assert "critical" in sevs
    assert sevs.count("warning") >= 5
