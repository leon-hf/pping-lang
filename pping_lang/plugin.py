"""vLLM stat_logger_plugins entry point — see design §6.2 / pre-impl-rfc §4.

Day 3 status：完整数据流通了。
- log_engine_initialized: 创建 LocalSink + NvmlSampler + VllmStatsCollector
- record(): 提取真实 stats → push 到 sink；同时写 self-overhead 心跳
- NVML 后台线程独立采样 GPU 物理层
- GPU peak 表查找成功则计算 MFU / mem_bw_util_ratio

环境变量：
- PPING_LANG_DB_PATH              默认 ~/.pping-lang/local.duckdb
- PPING_LANG_INSTANCE_ID          默认 local-{engine_index}
- PPING_LANG_NVML_INTERVAL_S      默认 0.1（NVML 采样间隔，秒）
- PPING_LANG_DISABLE_NVML         设为 1 关闭 NVML 采样
- PPING_LANG_FLUSH_INTERVAL_S     默认 5.0（Sink → DuckDB 批量写间隔）
- PPING_LANG_RULE_EVAL_INTERVAL_S 默认 1.0
- PPING_LANG_DISABLE_RULES        设为 1 关闭规则引擎
- PPING_LANG_RULES_PATH           可选 JSON 文件覆盖默认规则
- PPING_LANG_DIAGNOSIS_PRINT      默认 1，设 0 关闭终端打印
- PPING_LANG_API_HOST             默认 127.0.0.1（容器场景设 0.0.0.0）
- PPING_LANG_API_PORT             默认 8765
- PPING_LANG_DISABLE_API          设为 1 关闭 HTTP API
"""
from __future__ import annotations

import atexit
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pping_lang.api.routes import build_app
from pping_lang.api.server import ApiServer
from pping_lang.collector.nvml import NvmlSampler, detect_first_gpu_name
from pping_lang.collector.vllm_stats import VllmStatsCollector
from pping_lang.hardware import GPUPeak, lookup_peak
from pping_lang.metrics_catalog import M
from pping_lang.rules.engine import RuleEngine
from pping_lang.rules.loader import get_active_rules
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
      log_engine_initialized — 创建 LocalSink + NvmlSampler + VllmStatsCollector
      record(...)            — 热路径：collector.collect + 心跳 metric
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
        self._nvml: NvmlSampler | None = None
        self._collector: VllmStatsCollector | None = None
        self._rule_engine: RuleEngine | None = None
        self._api: ApiServer | None = None
        self._rules: list = []
        # 重 I/O 延迟到 log_engine_initialized（pre-impl-rfc §4.2）
        logger.info(
            "[pping-lang] plugin instantiated for engine_index=%d (vllm_available=%s)",
            engine_index,
            _VLLM_AVAILABLE,
        )

    def log_engine_initialized(self) -> None:
        # 1) Sink
        db_path_str = os.environ.get("PPING_LANG_DB_PATH")
        db_path = Path(db_path_str) if db_path_str else DEFAULT_DB_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        instance_id = os.environ.get(
            "PPING_LANG_INSTANCE_ID", f"local-{self.engine_index}"
        )
        flush_interval = float(
            os.environ.get("PPING_LANG_FLUSH_INTERVAL_S", "5.0")
        )
        self._sink = LocalSink(
            db_path=db_path,
            instance_id=instance_id,
            flush_interval_s=flush_interval,
        )
        atexit.register(self._sink.close)

        # 2) GPU peak (best-effort) — skip if NVML disabled (no GPU expected)
        if os.environ.get("PPING_LANG_DISABLE_NVML") == "1":
            gpu_peak = None
        else:
            gpu_peak = self._detect_gpu_peak()

        # 3) vLLM stats collector
        self._collector = VllmStatsCollector(
            self._sink, engine_index=self.engine_index, gpu_peak=gpu_peak,
        )

        # 4) NVML sampler (optional — disabled by env or by absence of GPU)
        if os.environ.get("PPING_LANG_DISABLE_NVML") != "1":
            interval_s = float(
                os.environ.get("PPING_LANG_NVML_INTERVAL_S", "0.1")
            )
            self._nvml = NvmlSampler(
                self._sink,
                engine_index=self.engine_index,
                interval_s=interval_s,
            )
            self._nvml.start()
            atexit.register(self._nvml.stop)

        # 5) Rule engine (optional — disabled by env)
        self._rules = get_active_rules()
        if os.environ.get("PPING_LANG_DISABLE_RULES") != "1":
            eval_interval = float(
                os.environ.get("PPING_LANG_RULE_EVAL_INTERVAL_S", "1.0")
            )
            self._rule_engine = RuleEngine(
                db_path=str(db_path),
                rules=self._rules,
                sink=self._sink,
                engine_index=self.engine_index,
                eval_interval_s=eval_interval,
            )
            self._rule_engine.start()
            atexit.register(self._rule_engine.stop)

        # 6) HTTP API + dashboard (optional)
        if os.environ.get("PPING_LANG_DISABLE_API") != "1":
            api_host = os.environ.get("PPING_LANG_API_HOST", "127.0.0.1")
            api_port = int(os.environ.get("PPING_LANG_API_PORT", "8765"))
            from pping_lang import __version__ as _version
            app = build_app(
                db_path=str(db_path),
                instance_id=instance_id,
                engine_index=self.engine_index,
                sink=self._sink,
                rules=self._rules,
                rule_engine=self._rule_engine,
                nvml=self._nvml,
                version=_version,
            )
            self._api = ApiServer(app, host=api_host, port=api_port)
            self._api.start()
            atexit.register(self._api.stop)

        logger.info(
            "[pping-lang] ready: db=%s instance=%s engine=%d nvml=%s gpu_peak=%s "
            "rules=%d api=%s",
            db_path, instance_id, self.engine_index,
            self._nvml.enabled if self._nvml else False,
            gpu_peak,
            self._rule_engine.num_rules if self._rule_engine else 0,
            self._api.url if self._api else "disabled",
        )

    def record(
        self,
        scheduler_stats: SchedulerStats | None,
        iteration_stats: IterationStats | None,
        mm_cache_stats: MultiModalCacheStats | None = None,
        engine_idx: int = 0,
    ) -> None:
        if self._sink is None:
            return
        t0 = time.monotonic_ns()
        if self._collector is not None:
            self._collector.collect(scheduler_stats, iteration_stats)
        elapsed_us = (time.monotonic_ns() - t0) / 1000.0
        self._sink.push_metric(MetricPoint(
            ts_ns=time.monotonic_ns(),
            name=M.PPING_LANG_RECORD_OVERHEAD_US,
            value=elapsed_us,
            engine_idx=self.engine_index,
        ))

    def log(self) -> None:
        # v1 中几乎不被调用 (vllm-project/vllm#20175)。逻辑放 record()。
        pass

    def record_sleep_state(self, is_awake: int, level: int) -> None:
        # v0.1 no-op
        pass

    # === internals ===

    def _detect_gpu_peak(self) -> GPUPeak | None:
        name = detect_first_gpu_name()
        if name is None:
            return None
        peak = lookup_peak(name)
        if peak is None:
            logger.warning(
                "[pping-lang] unknown GPU %r — MFU / Roofline disabled. "
                "Add to pping_lang.hardware._GPU_PEAK_TABLE if needed.", name,
            )
        return peak
