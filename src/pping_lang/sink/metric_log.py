"""Append-only JSONL 持久化 —— 替代进程内 DuckDB 做指标/诊断落盘。

为什么:插件进程内塞嵌入式分析库 + 每秒批量 INSERT,在指标洪流下和 serving 抢
GIL/IO。改成**顺序追加 JSONL**:无查询引擎、无事务、无索引,写入近乎零争用。
长窗回放时按需**扫文件过滤**(不频繁,可接受全扫)。

**保留 = 时间为主、磁盘兜底**:N 卷滚动,当前卷满 `volume_seconds`(时间)**或**撑爆
`max_bytes`(大小)任一即轮转,只留最近 `keep_volumes` 卷。正常负载下拿到约
`volume_seconds × keep_volumes` 的时间窗口;指标洪流下 `max_bytes` 提前触发轮转 →
**时间窗口自动缩水但磁盘有界**(上界 ≈ keep_volumes × max_bytes),绝不写爆 GPU 机盘
(和 sink 背压降采样同哲学:有界 + 代表性,不无界增长)。读取按时间序扫全部存活卷。

为什么不能纯时间:append-only 文件没法廉价地从头删旧行;而 sidecar 与 serving 同机,
磁盘安全必须优先。故时间是默认意图、磁盘是硬上界,二者冲突时磁盘赢。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认:保留 ~2h,切成 8 卷(每卷 15min);每卷大小兜底 32MB → 磁盘上界 ~256MB。
DEFAULT_RETENTION_S = 2 * 3600
DEFAULT_VOLUMES = 8
DEFAULT_VOLUME_SECONDS = DEFAULT_RETENTION_S / DEFAULT_VOLUMES  # 900s
DEFAULT_MAX_BYTES = 32 * 1024 * 1024  # 每卷兜底;须 ≥ 一个 volume_seconds 的正常负载量

# 落盘文件名(写端 LocalSink 与读端 JsonlStore 必须一致)。指标是洪流,诊断稀疏。
METRICS_FILE = "metrics.jsonl"
DIAG_FILE = "diagnoses.jsonl"


def metrics_path(store_dir: str | Path) -> Path:
    return Path(store_dir) / METRICS_FILE


def diag_path(store_dir: str | Path) -> Path:
    return Path(store_dir) / DIAG_FILE


class AppendLog:
    """JSONL 追加日志,N 卷按「时间或大小先到者」轮转。写入加锁(单 flush 线程)。

    卷文件:当前卷 = `path`,轮转后依次 `path.1`(最新轮转)… `path.{keep-1}`(最旧)。
    `volume_seconds` 用注入的 `clock`(默认 monotonic)量当前卷年龄,与记录 ts 解耦。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        volume_seconds: float = DEFAULT_VOLUME_SECONDS,
        keep_volumes: int = DEFAULT_VOLUMES,
        clock: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._path = Path(path)
        self._max = max_bytes
        self._volume_ns = int(volume_seconds * 1e9)
        self._keep = max(1, keep_volumes)
        self._clock = clock
        self._lock = threading.Lock()
        self._fh: Any = None
        self._vol_start = clock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _vol(self, i: int) -> Path:
        """卷 i 的路径。i==0 = 当前卷;i>=1 = 轮转卷 `.i`。"""
        return self._path if i == 0 else self._path.with_name(f"{self._path.name}.{i}")

    def _ensure(self):
        if self._fh is None:
            self._fh = open(self._path, "a", encoding="utf-8")  # noqa: SIM115
        return self._fh

    def append(self, records: Iterable[dict]) -> None:
        with self._lock:
            try:
                fh = self._ensure()
                for d in records:
                    fh.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
                    fh.write("\n")
                fh.flush()
                aged = (self._clock() - self._vol_start) >= self._volume_ns
                full = fh.tell() >= self._max
                if aged or full:
                    self._rotate()
            except Exception:
                logger.exception("[pping-lang] append-log 写入失败 %s", self._path)

    def _rotate(self) -> None:
        """当前卷 → .1,旧卷整体下移一位,丢弃超出 keep_volumes 的最旧卷。"""
        try:
            if self._fh is not None:
                self._fh.close()
            self._fh = None
            oldest = self._vol(self._keep - 1)
            if oldest.exists():
                oldest.unlink()
            for i in range(self._keep - 2, 0, -1):  # .{keep-2} → .{keep-1} … .1 → .2
                src = self._vol(i)
                if src.exists():
                    src.rename(self._vol(i + 1))
            if self._path.exists():
                self._path.rename(self._vol(1))
        except Exception:
            logger.exception("[pping-lang] append-log 轮转失败 %s", self._path)
        finally:
            self._fh = None
            self._vol_start = self._clock()

    def _volume_paths(self) -> list[Path]:
        """全部卷,旧 → 新(.{keep-1} … .1, 当前卷),供时间序读取。"""
        return [self._vol(i) for i in range(self._keep - 1, -1, -1)]

    def read(self) -> Iterator[dict]:
        """按时间序 yield 记录(最旧卷 → 当前卷)。坏行/缺卷跳过。"""
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                except Exception:
                    pass
        for p in self._volume_paths():
            if not p.exists():
                continue
            try:
                with open(p, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except Exception:
                            continue
            except Exception:
                logger.exception("[pping-lang] append-log 读取失败 %s", p)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None


def _quantile(values: list[float], q: float) -> float | None:
    """Sorted-list linear-interpolation percentile (matches DuckDB QUANTILE_CONT
    and routes._percentile / engine._agg_in_memory). q in [0, 1]; None if empty."""
    n = len(values)
    if n == 0:
        return None
    if n == 1:
        return values[0]
    s = sorted(values)
    rank = q * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


class JsonlStore:
    """读端 —— 对 AppendLog 的 JSONL 做按需全扫,复刻原 DuckDB 查询语义。

    只服务**冷/长窗**路径(>环窗口的历史回放、报告、规则 test)。实时短窗仍走
    sink 内存环。每次调用全扫全部存活卷(磁盘上界 ~keep_volumes×max_bytes,不频繁,
    可接受)—— 扫描重了就缩短保留窗口(调小 retention)。

    与写端 LocalSink 共享同一组文件(按路径),靠写端每批 flush 保证读到整行;
    最后一个未 flush 批次(≤flush_interval)不可见 —— 与旧 DuckDB WAL 滞后等价。
    `keep_volumes` 须与写端一致,reader 据此枚举要扫的卷。
    """

    def __init__(
        self,
        store_dir: str | Path,
        instance_id: str,
        keep_volumes: int = DEFAULT_VOLUMES,
    ) -> None:
        self._instance_id = instance_id
        # 只读用途:复用 AppendLog.read() 的多卷时序扫描;keep_volumes 决定扫几卷。
        self._mlog = AppendLog(metrics_path(store_dir), keep_volumes=keep_volumes)
        self._dlog = AppendLog(diag_path(store_dir), keep_volumes=keep_volumes)

    def recent_metric_points(
        self, name: str, since_ns: int, limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """`name` 在 since_ns 之后的最近 `limit` 个点,时序(旧→新)。"""
        from collections import deque
        keep: deque[dict[str, Any]] = deque(maxlen=limit)
        for r in self._mlog.read():
            if r.get("n") != name or r.get("t", 0) < since_ns:
                continue
            keep.append({
                "ts_ns": r["t"], "value": r["v"],
                "engine_idx": r.get("e", 0), "gpu_idx": r.get("g", -1),
            })
        return list(keep)

    def _values(self, name: str, since_ns: int, until_ns: int | None = None) -> list[float]:
        out: list[float] = []
        for r in self._mlog.read():
            if r.get("n") != name:
                continue
            t = r.get("t", 0)
            if t < since_ns or (until_ns is not None and t >= until_ns):
                continue
            out.append(float(r["v"]))
        return out

    def aggregate_metric(self, name: str, since_ns: int, agg: str = "avg") -> float | None:
        """单指标窗口聚合。None = 窗口无数据或不支持的聚合。"""
        vals = self._values(name, since_ns)
        if not vals:
            return None
        if agg == "avg":
            return sum(vals) / len(vals)
        if agg == "min":
            return min(vals)
        if agg == "max":
            return max(vals)
        if agg == "sum":
            return float(sum(vals))
        if agg == "count":
            return float(len(vals))
        if agg in ("p50", "p95", "p99"):
            return _quantile(vals, {"p50": 0.50, "p95": 0.95, "p99": 0.99}[agg])
        return None

    def bucketed_quantiles(
        self, name: str, since_ns: int, until_ns: int, buckets: int = 30,
    ) -> list[dict[str, Any]]:
        """时间分桶,返回 [{t, avg, p50, p99, n}, ...]。空桶省略。复刻 queries.bucketed_quantiles。"""
        span_ns = until_ns - since_ns
        if span_ns <= 0 or buckets <= 0:
            return []
        width = max(1, span_ns // buckets)
        groups: dict[int, list[float]] = {}
        for r in self._mlog.read():
            if r.get("n") != name:
                continue
            t = r.get("t", 0)
            if t < since_ns or t >= until_ns:
                continue
            b = int((t - since_ns) // width)
            if 0 <= b < buckets:
                groups.setdefault(b, []).append(float(r["v"]))
        out: list[dict[str, Any]] = []
        for b in sorted(groups):
            vals = groups[b]
            out.append({
                "t": (b * width) / 1e9,
                "avg": sum(vals) / len(vals),
                "p50": _quantile(vals, 0.50),
                "p99": _quantile(vals, 0.99),
                "n": len(vals),
            })
        return out

    def list_instances(self) -> list[str]:
        """嵌入模式下每进程一个 instance —— 即本 store 的 instance_id。"""
        return [self._instance_id]
