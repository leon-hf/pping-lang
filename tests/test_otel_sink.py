"""OTelSink — 用 InMemoryMetricExporter 注入避免起真 OTLP collector。"""
from __future__ import annotations

import pytest

from pping_lang.otel.sink import OTelSink
from pping_lang.types import MetricPoint

# Skip whole module if SDK pieces missing
pytest.importorskip("opentelemetry.sdk.metrics")
pytest.importorskip("opentelemetry.sdk.metrics.export")


@pytest.fixture
def in_memory_provider():
    """Build a MeterProvider backed by InMemoryMetricReader for inspection."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.resources import Resource

    reader = InMemoryMetricReader()
    provider = MeterProvider(
        metric_readers=[reader],
        resource=Resource.create({"service.name": "pping-lang-test"}),
    )
    yield provider, reader


def _mp(name="gpu.utilization_pct", value=75.0, gpu_idx=0):
    return MetricPoint(ts_ns=1, name=name, value=value, engine_idx=0, gpu_idx=gpu_idx)


def test_otel_sink_constructs_with_custom_provider(in_memory_provider):
    provider, _ = in_memory_provider
    sink = OTelSink(meter_provider=provider, flush_interval_s=10.0)
    try:
        assert sink._meter is not None
    finally:
        sink.close()


def test_metric_pushed_to_otel_exporter(in_memory_provider):
    provider, reader = in_memory_provider
    sink = OTelSink(meter_provider=provider, flush_interval_s=10.0)
    try:
        sink.push_metric(_mp(name="gpu.utilization_pct", value=75.0))
        sink.push_metric(_mp(name="gpu.utilization_pct", value=80.0))
        # Force a sync flush so the InMemoryReader sees the data
        sink._drain()
    finally:
        sink.close()

    # Read what the in-memory reader collected
    metrics_data = reader.get_metrics_data()
    assert metrics_data is not None
    # Walk: resource_metrics → scope_metrics → metrics
    found_names = set()
    for rm in metrics_data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                found_names.add(m.name)
    # OTel name applies our prefix
    assert "pping_lang.gpu.utilization_pct" in found_names


def test_metric_attributes_include_engine_idx_and_gpu_idx(in_memory_provider):
    provider, reader = in_memory_provider
    sink = OTelSink(meter_provider=provider, flush_interval_s=10.0)
    try:
        sink.push_metric(MetricPoint(
            ts_ns=1, name="gpu.utilization_pct", value=42.0,
            engine_idx=3, gpu_idx=1,
        ))
        sink._drain()
    finally:
        sink.close()

    metrics_data = reader.get_metrics_data()
    found_attrs = []
    for rm in metrics_data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == "pping_lang.gpu.utilization_pct":
                    for dp in m.data.data_points:
                        found_attrs.append(dict(dp.attributes))
    assert any(a.get("engine_idx") == 3 and a.get("gpu_idx") == 1 for a in found_attrs)


def test_unknown_metric_doesnt_crash(in_memory_provider):
    provider, _ = in_memory_provider
    sink = OTelSink(meter_provider=provider, flush_interval_s=10.0)
    try:
        # Use a metric name not in catalog — OTelSink doesn't validate (catalog is for rules)
        sink.push_metric(MetricPoint(
            ts_ns=1, name="random.test.metric", value=1.0,
        ))
        sink._drain()
    finally:
        sink.close()
    # Should not raise


def test_otel_sink_close_idempotent(in_memory_provider):
    provider, _ = in_memory_provider
    sink = OTelSink(meter_provider=provider, flush_interval_s=10.0)
    sink.close()
    sink.close()  # must not raise


def test_no_otlp_endpoint_disables_silently(monkeypatch):
    """If OTLP exporter import fails or endpoint unreachable, sink degrades."""
    # Don't pass meter_provider AND mock out the OTLP exporter to simulate import error
    import sys

    # Simulate the OTLP package being unavailable
    monkeypatch.setattr(
        "pping_lang.otel.sink.OTelSink._build_meter",
        lambda self, custom, interval: None,
    )
    sink = OTelSink(endpoint="http://nowhere:4317", flush_interval_s=10.0)
    try:
        # _meter is None → push_metric goes to deque, _flush no-ops
        sink.push_metric(_mp())
        sink._drain()
    finally:
        sink.close()
    # Just verify it didn't crash
