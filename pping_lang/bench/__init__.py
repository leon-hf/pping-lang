"""Microbenchmarks — 验证热路径耗时不超 RFC §3 / design §8.3 预算。

子模块：
- microbench: 运行式 + 函数式 API
"""
from pping_lang.bench.microbench import (
    bench_collect_scheduler,
    bench_push_metric,
    bench_record_overhead,
    main,
)

__all__ = [
    "bench_push_metric",
    "bench_collect_scheduler",
    "bench_record_overhead",
    "main",
]
