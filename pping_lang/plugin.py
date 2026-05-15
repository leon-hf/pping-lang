"""vLLM stat_logger_plugins entry point — see design §6.2 / pre-impl-rfc §4.

Day 1 stub：能被 vLLM entry point 加载、能实例化、不崩。
Day 2 接入 Sink；Day 3 接入 Collector + NVML；Day 6 启 FastAPI。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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


class PpingLangStatLogger(StatLoggerBase):
    """v0.1 主入口。每个 vLLM EngineCore 进程实例化一次。

    生命周期：
      __init__               — 构造（不做重 I/O）
      log_engine_initialized — Day 6 在此启 FastAPI、连 DuckDB
      record(...)            — 热路径，Day 2/3 接 Collector + Sink
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
        # 重 I/O（FastAPI、DuckDB、NVML 线程）延迟到 log_engine_initialized
        # 见 pre-impl-rfc §4.2
        logger.info(
            "[pping-lang] plugin instantiated for engine_index=%d (vllm_available=%s)",
            engine_index,
            _VLLM_AVAILABLE,
        )

    def record(
        self,
        scheduler_stats: SchedulerStats | None,
        iteration_stats: IterationStats | None,
        mm_cache_stats: MultiModalCacheStats | None = None,
        engine_idx: int = 0,
    ) -> None:
        # Day 2/3：Collector.collect(...) + Sink.push_metric(...)
        # 当前 Day 1 stub：no-op，仅保证接口可调
        pass

    def log(self) -> None:
        # v1 中几乎不被调用 (vllm-project/vllm#20175)。逻辑放 record()。
        pass

    def log_engine_initialized(self) -> None:
        # Day 6：
        #   1. 连 DuckDB
        #   2. 起 NVML 后台线程
        #   3. 启 FastAPI（uvicorn in thread）
        #   4. 打印 dashboard URL
        logger.info(
            "[pping-lang] engine initialized, engine_index=%d (dashboard not yet wired)",
            self.engine_index,
        )

    def record_sleep_state(self, is_awake: int, level: int) -> None:
        # v0.1 no-op
        pass
