"""pping-lang — vLLM 性能诊断插件。

主入口：PpingLangStatLogger（通过 vllm.stat_logger_plugins entry point 自动加载）。
"""
from pping_lang.plugin import PpingLangStatLogger
from pping_lang.sink import LocalSink, Sink
from pping_lang.types import Diagnosis, MetricPoint, Severity

__version__ = "0.0.1.dev0"
__all__ = [
    "PpingLangStatLogger",
    "MetricPoint",
    "Diagnosis",
    "Severity",
    "Sink",
    "LocalSink",
    "__version__",
]
