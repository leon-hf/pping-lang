"""时间戳时钟 —— 区分"落库的绝对时刻"和"测耗时的差值"。

落库到 metrics / diagnoses 表、之后要按窗口查询或跨进程比较的 ts,**必须用
wall-clock**(`time.time_ns()`):monotonic 的纪元随进程重启 / 机器重启而变,落库后
新旧两批数据的 ts 不可比 —— 窗口过滤失效、`ORDER BY ts DESC LIMIT` 把旧纪元数据
排到最前、把新数据挤出去(真实踩过：重启后 /api/diagnoses 只显示重启前的陈旧诊断)。

纯耗时测量(bench 时延、回调开销、eval 用时)仍用 `time.monotonic_ns()`：那是算
差值,monotonic 不受 NTP 回拨影响才稳。两者别混。

不变量：凡是 `MetricPoint.ts_ns` / `Diagnosis.ts_ns` 的生成,以及对这两张表的
窗口 cutoff,都用 `wall_ns()`。`grep wall_ns` 可一次看全所有持久化时间点。
"""
from __future__ import annotations

import time


def wall_ns() -> int:
    """Wall-clock 纳秒(epoch 起)—— 用于落库的绝对时间戳,跨进程 / 跨重启可比。"""
    return time.time_ns()
