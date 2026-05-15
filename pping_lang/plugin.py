"""vLLM stat_logger_plugins entry point — see design §6.2 / pre-impl-rfc §4.

Day 2 status：Sink 已接入，record() 推送 self-observability 心跳指标。
Day 3 接入 Collector + NVML，从 scheduler_stats / iteration_stats 提取真实指标。
Day 6 在 log_engine_initialized 启 FastAPI 并打印 dashboard URL。

环境变量：
- PPING_LANG_DB_PATH      默认 ~/.pping-lang/local.duckdb
- PPING_LANG_INSTANCE_ID  默认 local-{engine_index}
"""
from __future__ import annotations

import atexit
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pping_lang.metrics_catalog import M
from pping_lang.sink.base import Sink
from pping_lang.sink.local import LocalSink
from pping_lang.types import MetricPoint

logger = logging.getLogger(__name__)

# 允许 vllm 缺失时也能 import（dev / test 环境无需安装巨型 vllm）。
# 真实 `vllm serve` 时一定能 import 成功，此分支永不走。
try:
    from vllm.v1.metrics.loggers import StatLoggerBase

    _VLLM_AVAILABLE = True
except ImportError:
    _VLLM_AVAILABLE = False

    class StatLoggerBase:  # type: ignore[no-redef]
        """Fallback used only when vllm is not installed (dev/test only)."""

        def __init__(self, vllm_config=None, engine_index: int = 0):  # noqa: D401
            pass


if TYPE_CHECKING:
    from vllm.config import VllmConfig
    from vllm.v1.metrics.stats import (
        IterationStats,
        MultiModalCacheStats,
        SchedulerStats,
    )


DEFAULT_DB_PATH = Path.home() / ".pping-lang" / "local.duckdb"


class PpingLangStatLogger(StatLoggerBase):
    """v0.1 主入口。每个 vLLM EngineCore 进程实例化一次。

    生命周期：
      __init__               — 构造（不做重 I/O）
      log_engine_initialized — 创建 LocalSink (DB + bg flush thread)
      record(...)            — 热路径：推送 self-overhead 心跳；Day 3 加 stats 提取
      log()                  — v1 几乎不调用，逻辑放 record()
      record_sleep_state(..) — v0.1 no-op
    """

    def __init__(
        self,
        vllm_config: VllmConfig | None = None,
        engine_index: int = 0,
    ) -> None:
        super().__init__(vllm_config, engine_index)
        self.engine_index = engine_index
        self.vllm_config = vllm_config
        self._sink: Sink | None = None
        # 重 I/O 延迟到 log_engine_initialized（pre-impl-rfc §4.2）
        logger.info(
            "[pping-lang] plugin instantiated for engine_index=%d (vllm_available=%s)",
            engine_index,
            _VLLM_AVAILABLE,
        )

    def log_engine_initialized(self) -> None:
        db_path_str = os.environ.get("PPING_LANG_DB_PATH")
        db_path = Path(db_path_str) if db_path_str else DEFAULT_DB_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        instance_id = os.environ.get(
            "PPING_LANG_INSTANCE_ID", f"local-{self.engine_index}"
        )
        self._sink = LocalSink(db_path=db_path, instance_id=instance_id)
        atexit.register(self._sink.close)
        logger.info(
            "[pping-lang] LocalSink ready: db=%s instance=%s engine=%d",
            db_path, instance_id, self.engine_index,
        )

    def record(
        self,
        scheduler_stats: SchedulerStats | None,
        iteration_stats: IterationStats | None,
        mm_cache_stats: MultiModalCacheStats | None = None,
        engine_idx: int = 0,
    ) -> None:
        # No-op until log_engine_initialized has wired the sink (defensive).
        if self._sink is None:
            return
        t0 = time.monotonic_ns()
        # ── Day 3 will extract real metrics here:
        #   self._collector.collect(scheduler_stats, iteration_stats) → push_metric() per field
        # ── Day 2 ships only the self-observability heartbeat below.
        elapsed_us = (time.monotonic_ns() - t0) / 1000.0
        self._sink.push_metric(
            MetricPoint(
                ts_ns=time.monotonic_ns(),
                name=M.PPING_LANG_RECORD_OVERHEAD_US,
                value=elapsed_us,
                engine_idx=self.engine_index,
            )
        )

    def log(self) -> None:
        # v1 中几乎不被调用 (vllm-project/vllm#20175)。逻辑放 record()。
        pass

    def record_sleep_state(self, is_awake: int, level: int) -> None:
        # v0.1 no-op
        pass
