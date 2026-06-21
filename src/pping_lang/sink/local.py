"""LocalSink — Embedded mode: 顺序追加 JSONL,同进程落盘(替代进程内 DuckDB)。

为什么不放 DuckDB:嵌入式分析库在每秒指标洪流下批量 INSERT,会和 colocated 的
vLLM serving 抢 GIL/IO。改成**顺序追加 JSONL**(AppendLog):无查询引擎、无事务、
无索引,写入近乎零争用。长窗历史回放按需扫文件(JsonlStore),那是冷路径。

落盘两份:metrics.jsonl(洪流) + diagnoses.jsonl(稀疏)。文件落在 `db_path` 同目录
(`db_path` 这个历史参数名沿用:嵌入模式它是一个文件路径,我们取其父目录当 store 目录;
bench 仍用它的 .duckdb 存可变的 bench_runs 行)。instance_id 在 Sink 出站边界打上
(RFC §1.1);嵌入模式每进程一个 instance,故不逐行重复存,由 JsonlStore 统一报告。

实时 dashboard 读不走这里 —— 那是 Sink 的内存环(latest/recent)。
"""
from __future__ import annotations

import logging
from pathlib import Path

from pping_lang.sink.base import Sink
from pping_lang.sink.metric_log import (
    DEFAULT_RETENTION_S,
    DEFAULT_VOLUMES,
    AppendLog,
    diag_path,
    metrics_path,
)
from pping_lang.types import Diagnosis, MetricPoint

logger = logging.getLogger(__name__)


class LocalSink(Sink):
    """JSONL-backed sink for Embedded mode. 写端;读端见 metric_log.JsonlStore。

    `retention_s` = 目标保存时间窗口(时间为主);切成 DEFAULT_VOLUMES 卷滚动,洪流下
    每卷大小兜底,故实际窗口在重压时缩水、磁盘有界(见 AppendLog)。
    """

    def __init__(
        self,
        db_path: str | Path,
        instance_id: str,
        retention_s: float = DEFAULT_RETENTION_S,
        **base_kwargs,
    ) -> None:
        store_dir = Path(db_path).parent
        self._instance_id = instance_id
        vol_s = retention_s / DEFAULT_VOLUMES
        self._mlog = AppendLog(metrics_path(store_dir), volume_seconds=vol_s)
        self._dlog = AppendLog(diag_path(store_dir), volume_seconds=vol_s)
        super().__init__(**base_kwargs)

    def _flush(
        self,
        metrics: list[MetricPoint],
        diags: list[Diagnosis],
    ) -> None:
        if metrics:
            self._mlog.append(
                {
                    "t": m.ts_ns,
                    "e": m.engine_idx,
                    "g": m.gpu_idx,
                    "n": m.name,
                    "v": m.value,
                    "l": dict(m.labels) if m.labels else None,
                }
                for m in metrics
            )
        if diags:
            self._dlog.append(
                {
                    "t": d.ts_ns,
                    "e": d.engine_idx,
                    "g": d.gpu_idx,
                    "i": self._instance_id,
                    "rule_id": d.rule_id,
                    "severity": d.severity,
                    "triggered_value": d.triggered_value,
                    "threshold": d.threshold,
                    "window_seconds": d.window_seconds,
                    "message": d.message,
                    "suggestion": d.suggestion,
                    "context": dict(d.context) if d.context else None,
                }
                for d in diags
            )

    def close(self) -> None:
        super().close()
        self._mlog.close()
        self._dlog.close()
