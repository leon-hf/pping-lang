"""OTelSink — 把 MetricPoint / Diagnosis 推到 OpenTelemetry collector。

策略（v0.1 简化版）：
- 所有 metric 统一用 Histogram（distribution 视图通用，不需要按类型选 instrument）
- ObservableGauge 语义在 v0.2 加（callback 维护 last-value 状态）
- Diagnosis → OTel log signal（v0.1 只 log；event API 在 v0.2 加）

OTel 内部已有自己的 batch + bg flush 机制（PeriodicExportingMetricReader）。
我们的 _flush 只把 batch 喂进 instrument，OTel SDK 负责真正的网络出站。

测试用 InMemoryMetricExporter 注入避免起真 OTLP collector。
"""
from __future__ import annotations

import logging
from typing import Any

from pping_lang.metrics_catalog import to_otel_name
from pping_lang.sink.base import Sink
from pping_lang.types import Diagnosis, MetricPoint

logger = logging.getLogger(__name__)


class OTelSink(Sink):
    """OpenTelemetry exporter sink. Each unique metric name gets its own Histogram."""

    def __init__(
        self,
        endpoint: str | None = None,
        meter_provider: Any = None,
        service_name: str = "pping-lang",
        instance_id: str = "default",
        export_interval_ms: int = 5000,
        **base_kwargs: Any,
    ) -> None:
        super().__init__(**base_kwargs)
        self._endpoint = endpoint
        self._service_name = service_name
        self._instance_id = instance_id
        self._instruments: dict[str, Any] = {}
        self._meter = self._build_meter(meter_provider, export_interval_ms)
        self._logger_handle = self._build_logger()

    # === SDK setup ===

    def _build_meter(self, custom_provider: Any, export_interval_ms: int) -> Any:
        from opentelemetry import metrics
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        if custom_provider is not None:
            provider = custom_provider
        else:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                    OTLPMetricExporter,
                )
                exporter = OTLPMetricExporter(endpoint=self._endpoint, insecure=True)
            except Exception as e:
                logger.warning(
                    "[pping-lang] OTLP gRPC exporter unavailable, OTel disabled: %s", e,
                )
                return None
            reader = PeriodicExportingMetricReader(
                exporter, export_interval_millis=export_interval_ms,
            )
            resource = Resource.create({
                "service.name": self._service_name,
                "service.instance.id": self._instance_id,
            })
            provider = MeterProvider(
                metric_readers=[reader], resource=resource,
            )
        # Don't call set_meter_provider — keep us isolated from globals
        return provider.get_meter(__name__) if provider else None

    def _build_logger(self) -> logging.Logger | None:
        # v0.1: just use stdlib logger; v0.2 wires real OTel log signal
        return logger

    # === Sink override ===

    def _flush(
        self,
        metrics: list[MetricPoint],
        diags: list[Diagnosis],
    ) -> None:
        if self._meter is None:
            return
        for m in metrics:
            inst = self._get_histogram(m.name)
            if inst is not None:
                attrs = {"engine_idx": m.engine_idx}
                if m.gpu_idx >= 0:
                    attrs["gpu_idx"] = m.gpu_idx
                if m.labels:
                    attrs.update(m.labels)
                inst.record(m.value, attributes=attrs)
        for d in diags:
            # v0.1: just log; v0.2 OTel LogRecord with severity/attributes
            if self._logger_handle is not None:
                self._logger_handle.warning(
                    "[pping-lang otel] diagnosis: rule=%s severity=%s value=%s threshold=%s message=%s",
                    d.rule_id, d.severity, d.triggered_value, d.threshold, d.message,
                )

    def _get_histogram(self, internal_name: str) -> Any:
        if internal_name in self._instruments:
            return self._instruments[internal_name]
        try:
            otel_name = to_otel_name(internal_name)
            inst = self._meter.create_histogram(
                name=otel_name,
                description=f"pping-lang metric: {internal_name}",
            )
            self._instruments[internal_name] = inst
            return inst
        except Exception:
            logger.exception("[pping-lang otel] failed to create instrument %s", internal_name)
            return None
