"""CUPTI kernel 级采集 — 阶段 1a，部署级调优(见 _design-notes/phase-1a-采集设计.md)。

把 GPU kernel 的时间分解采集进现有 Sink/DuckDB 管道,填上"层级 2"空缺:
不只是"MFU 低 / padding 多",而是"attention kernel 占 step 42%"。

架构基石 —— 语言边界:
    采集源藏在 `KernelActivitySource` 接口后面,可替换。
    1a:   `CuptiPythonSource`(进程内 cupti-python,纯 Python,Linux-only)
    1b/2: 未来换成原生 libppingcupti.so 经共享内存喂同一聚合层,本文件其余部分不动。
    所以本模块除 `CuptiPythonSource` 那段绑定外,都是会长期沉淀的 Python 逻辑,
    在 Windows 上也能写 + 跑单测(用 `FakeActivitySource` 喂合成记录)。

故障隔离(design §3.1):source 不可用(Win / 无 cupti / 无 GPU)→ 优雅 no-op + warning,
永不阻塞 vLLM。回调里的任何异常被吞掉并计数,不向上传播。

5% 预算自守:回调里只做记忆化分类 + 累加(O(1)/条),周期 roll-up 才 push 派生标量。
丢弃数与回调耗时作为自我观测指标暴露;后续可据此自动降级(拉长 rollup / 砍 kind)。
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any, Protocol, runtime_checkable

from pping_lang.clock import wall_ns
from pping_lang.metrics_catalog import M
from pping_lang.sink.base import Sink
from pping_lang.types import MetricPoint

logger = logging.getLogger(__name__)

DEFAULT_ROLLUP_INTERVAL_S = 1.0

# kernel 语义类 → 占比 metric 名。分类器产出的 class 必须 ∈ 这些 key。
KERNEL_CLASS_TO_METRIC: dict[str, str] = {
    "attention": M.KERNEL_SHARE_ATTENTION_PCT,
    "gemm": M.KERNEL_SHARE_GEMM_PCT,
    "norm": M.KERNEL_SHARE_NORM_PCT,
    "rotary": M.KERNEL_SHARE_ROTARY_PCT,
    "activation": M.KERNEL_SHARE_ACTIVATION_PCT,
    "comm": M.KERNEL_SHARE_COMM_PCT,
    "other": M.KERNEL_SHARE_OTHER_PCT,
}
KERNEL_CLASSES: tuple[str, ...] = tuple(KERNEL_CLASS_TO_METRIC)

# stall 语义类 → 占比 metric 名(阶段 2 PC Sampling)。"issued" 不在此(非 stall,
# 单独走 KERNEL_STALL_ISSUED_PCT)。各类相加 ≈ 100(占 stall 样本)。映射见设计文档 §11。
STALL_CLASS_TO_METRIC: dict[str, str] = {
    "memory_dependency": M.KERNEL_STALL_MEMORY_DEP_PCT,
    "shared_dependency": M.KERNEL_STALL_SHARED_DEP_PCT,
    "memory_throttle": M.KERNEL_STALL_MEMORY_THROTTLE_PCT,
    "math_pipe": M.KERNEL_STALL_MATH_PIPE_PCT,
    "exec_dependency": M.KERNEL_STALL_EXEC_DEP_PCT,
    "sync": M.KERNEL_STALL_SYNC_PCT,
    "fetch_control": M.KERNEL_STALL_FETCH_CONTROL_PCT,
    "dispatch": M.KERNEL_STALL_DISPATCH_PCT,
    "scheduler_slack": M.KERNEL_STALL_SCHEDULER_SLACK_PCT,
    "other": M.KERNEL_STALL_OTHER_PCT,
}
STALL_CLASSES: tuple[str, ...] = tuple(STALL_CLASS_TO_METRIC)


# === 采集源接口(可替换边界) =========================================

@dataclass(slots=True, frozen=True)
class KernelEvent:
    """一条 GPU 活动记录,从采集源传给聚合层的最小载荷。

    对应 cupti `ActivityKernel10` / `ActivityMemcpy6` / `ActivitySynchronization2`
    的字段子集 —— 只保留 1a 部署级诊断真正用到的。kind 区分三类活动。
    """

    kind: str          # "kernel" | "memcpy" | "sync"
    name: str          # kernel 名(mangled);memcpy/sync 为方向/类型字符串
    start_ns: int      # GPU 时钟时间戳(ns)
    end_ns: int
    graph_id: int = 0  # CUDA Graph 归因;0 = 非 graph 内 launch
    # 发起该 kernel 的 Python 调用栈(root→leaf 帧名),火焰图用。
    # 仅在 capture_stacks 模式下填充(贵,on-demand);否则 None。
    stack: tuple[str, ...] | None = None
    stream_id: int = 0  # GPU stream;时间线按 stream 分行(看并发/通信重叠)


# 采集源在自己的线程(CUPTI worker)里,每个 flush 周期回调一次,传一批 KernelEvent。
RecordsCallback = Callable[[list[KernelEvent]], None]


@runtime_checkable
class KernelActivitySource(Protocol):
    """kernel 活动采集源。1a=cupti-python;1b/2=原生 .so。"""

    def available(self) -> bool:
        """本环境能否采集(Linux + cupti + GPU)。False → collector 优雅禁用。"""
        ...

    def start(self, on_records: RecordsCallback) -> None:
        """开始采集,记录成批回调到 on_records。"""
        ...

    def stop(self) -> None:
        """停止采集并 flush 残留记录。幂等。"""
        ...

    def dropped_records(self) -> int:
        """累计丢弃记录数(缓冲溢出),供预算自守判断。无则返回 0。"""
        ...


# === kernel 名 → 语义类(vLLM 语义层,带记忆化) =======================

# (子串列表, 类名)。小写子串匹配,首个命中胜出 —— 顺序敏感:
# 先 comm(nccl 含 reduce)再 gemm,先 attention 再其它。未命中 → "other"。
DEFAULT_CLASSIFY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("flash", "attention", "paged_attn", "paged_attention", "fmha", "mha"), "attention"),
    (("nccl", "allreduce", "all_reduce", "reduce_scatter", "all_gather", "reducescatter",
      "allgather", "sendrecv"), "comm"),
    (("gemm", "cutlass", "s16816", "wgmma", "cublas", "matmul", "sm80_", "sm90_",
      "ampere_", "hopper_", "_mm_", "linear"), "gemm"),
    (("rms_norm", "rmsnorm", "layernorm", "layer_norm", "fused_add_rms"), "norm"),
    (("rotary", "rope"), "rotary"),
    (("silu", "swiglu", "geglu", "gelu", "act_and_mul", "activation"), "activation"),
    # 采样/解码(softmax/argmax/分布采样)—— 必须在 elementwise 之前:分布采样核名里
    # 含 "elementwise"(distribution_elementwise_grid_stride_kernel),否则会被误归逐元素。
    (("softmax", "argmax", "distribution", "exponential", "multinomial",
      "topk", "top_k", "gumbel"), "sampling"),
    # 索引/聚集/embedding 查表(comm 的 all_gather/reduce_scatter 已在前面先被吃掉)
    (("indexselect", "index_select", "embedding", "gather", "scatter"), "index"),
    # 逐元素 / 拷贝 / 类型转换(大算子之间的 glue:add/mul/div/copy/cast)
    (("elementwise", "direct_copy", "copy_kernel", "_cast", "fill_kernel"), "elementwise"),
]


class KernelClassifier:
    """mangled kernel 名 → 语义类,结果按名记忆化。

    记忆化是 5% 预算的关键:同批就那 ~20 个 kernel 反复出现,首次分类后退化成
    一次 dict 命中(~80ns/条),避免每条都跑子串匹配(见采集设计 §0)。
    """

    def __init__(self, rules: list[tuple[tuple[str, ...], str]] | None = None) -> None:
        self._rules = rules if rules is not None else DEFAULT_CLASSIFY_RULES
        self._cache: dict[str, str] = {}

    def classify(self, name: str) -> str:
        cls = self._cache.get(name)
        if cls is None:
            cls = self._match(name)
            self._cache[name] = cls
        return cls

    def _match(self, name: str) -> str:
        low = name.lower()
        for needles, cls in self._rules:
            for n in needles:
                if n in low:
                    return cls
        return "other"

    @property
    def cache_size(self) -> int:
        return len(self._cache)


# === stall reason → 语义类(阶段 2 PC Sampling,带记忆化) ==============

# (子串列表, 语义类)。小写子串匹配,首个命中胜出 —— **顺序敏感**。
# 输入是 Ada/sm_89 的 PerfWorks 名,如 `smsp__pcsamp_warps_issue_stalled_long_scoreboard`;
# `_not_issued` 变体(同 reason 但该 cycle 未发射)由子串自然归到同类。
# 归类口径见设计文档 §11(Codex 评审细化):
#   - long_scoreboard=访存依赖;short_scoreboard 不并入(shared/MIO);throttle/miss=子系统压力;
#   - not_selected 必须在 selected 之前(前者含后者子串);selected=已发射,非 stall。
DEFAULT_STALL_RULES: list[tuple[tuple[str, ...], str]] = [
    (("not_selected",), "scheduler_slack"),          # 必须先于 selected
    (("selected",), "issued"),                        # 非 stall:已发射指令
    (("long_scoreboard",), "memory_dependency"),
    (("short_scoreboard",), "shared_dependency"),
    (("mio_throttle", "lg_throttle", "tex_throttle", "imc_miss"), "memory_throttle"),
    (("math_pipe", "fma", "alu", "tensor"), "math_pipe"),
    (("barrier", "membar", "sync"), "sync"),
    (("wait",), "exec_dependency"),
    (("no_instruction", "inst_fetch", "branch_resolving"), "fetch_control"),
    (("dispatch",), "dispatch"),
    (("drain", "sleeping", "misc"), "other"),
]


class StallClassifier:
    """PerfWorks stall reason 名 → 语义类,记忆化(同 KernelClassifier 套路)。

    产出的类 ∈ STALL_CLASSES ∪ {"issued"}。未命中 → "other"。
    """

    def __init__(self, rules: list[tuple[tuple[str, ...], str]] | None = None) -> None:
        self._rules = rules if rules is not None else DEFAULT_STALL_RULES
        self._cache: dict[str, str] = {}

    def classify(self, reason: str) -> str:
        cls = self._cache.get(reason)
        if cls is None:
            cls = self._match(reason)
            self._cache[reason] = cls
        return cls

    def _match(self, reason: str) -> str:
        low = reason.lower()
        for needles, cls in self._rules:
            for n in needles:
                if n in low:
                    return cls
        return "other"

    @property
    def cache_size(self) -> int:
        return len(self._cache)


# === 滚动窗聚合 → 派生标量 ============================================

class WindowAggregator:
    """累加一个时间窗内的 kernel 时间,roll-up 时算出派生占比指标。

    线程安全:add() 在采集源线程调用,snapshot_and_reset() 在 collector 调用,
    用一把锁护住(都很轻)。tumbling 窗(roll-up 即清零),非滑动窗 —— 1a 够用。
    """

    def __init__(self, classifier: KernelClassifier) -> None:
        self._clf = classifier
        self._lock = Lock()
        self._reset_locked()

    def _reset_locked(self) -> None:
        self._class_ns: dict[str, int] = defaultdict(int)
        # per-kernel 原始明细:raw name → [count, total_ns, in_graph_ns]。
        # 这是"未洗过"的钻取数据,kernel_table() 读它出原始表。
        self._kernel_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        self._memcpy_ns = 0
        self._sync_ns = 0
        self._in_graph_ns = 0
        self._launch_count = 0
        self._has_data = False

    @property
    def has_data(self) -> bool:
        """本窗自上次 reset 以来是否收到过有效活动。bool 读 GIL 原子,无需锁。"""
        return self._has_data

    def add(self, events: list[KernelEvent]) -> None:
        with self._lock:
            for e in events:
                dur = e.end_ns - e.start_ns
                if dur < 0:
                    continue  # 时钟乱序/坏记录,跳过
                self._has_data = True
                if e.kind == "kernel":
                    self._class_ns[self._clf.classify(e.name)] += dur
                    self._launch_count += 1
                    ks = self._kernel_stats[e.name]
                    ks[0] += 1
                    ks[1] += dur
                    if e.graph_id:
                        self._in_graph_ns += dur
                        ks[2] += dur
                elif e.kind == "memcpy":
                    self._memcpy_ns += dur
                elif e.kind == "sync":
                    self._sync_ns += dur

    def snapshot_and_reset(self, wall_ns: int) -> dict[str, float]:
        """算出派生指标(metric 名 → 值)并清零。wall_ns = 本窗墙钟跨度。"""
        with self._lock:
            total_kernel_ns = sum(self._class_ns.values())
            out: dict[str, float] = {}

            # kernel 类占比:占总 kernel 计算时间(各类相加 ≈ 100)
            for cls in KERNEL_CLASSES:
                pct = 100.0 * self._class_ns.get(cls, 0) / total_kernel_ns if total_kernel_ns else 0.0
                out[KERNEL_CLASS_TO_METRIC[cls]] = pct

            # memcpy / sync / gpu_busy:占墙钟窗口
            wall = wall_ns if wall_ns > 0 else 0
            if wall:
                out[M.KERNEL_MEMCPY_SHARE_PCT] = 100.0 * self._memcpy_ns / wall
                out[M.KERNEL_SYNC_SHARE_PCT] = 100.0 * self._sync_ns / wall
                # busy 可能因多 stream 并发 Σ 时长 >wall,夹到 100
                out[M.KERNEL_GPU_BUSY_PCT] = min(100.0, 100.0 * total_kernel_ns / wall)
                out[M.KERNEL_LAUNCH_COUNT_PER_S] = self._launch_count / (wall / 1e9)
            else:
                out[M.KERNEL_MEMCPY_SHARE_PCT] = 0.0
                out[M.KERNEL_SYNC_SHARE_PCT] = 0.0
                out[M.KERNEL_GPU_BUSY_PCT] = 0.0
                out[M.KERNEL_LAUNCH_COUNT_PER_S] = 0.0

            out[M.KERNEL_MEAN_DUR_US] = (
                (total_kernel_ns / self._launch_count) / 1e3 if self._launch_count else 0.0
            )
            out[M.KERNEL_IN_GRAPH_PCT] = (
                100.0 * self._in_graph_ns / total_kernel_ns if total_kernel_ns else 0.0
            )

            self._reset_locked()
            return out

    def kernel_table(self, limit: int = 25) -> list[dict[str, Any]]:
        """当前窗的 per-kernel 原始明细(未聚合),按占比降序取 top-N。

        只读,不 reset —— collector 在 snapshot_and_reset 前调它快照本窗明细。
        每行:raw name(未洗的 mangled 名)+ 分类 + 调用次数 + 总/平均耗时 + 占比。
        """
        with self._lock:
            total = sum(v[1] for v in self._kernel_stats.values())
            rows: list[dict[str, Any]] = []
            for name, (count, total_ns, in_graph_ns) in self._kernel_stats.items():
                rows.append({
                    "name": name,
                    "cls": self._clf.classify(name),
                    "count": count,
                    "total_ms": total_ns / 1e6,
                    "mean_us": (total_ns / count / 1e3) if count else 0.0,
                    "pct": (100.0 * total_ns / total) if total else 0.0,
                    "in_graph_pct": (100.0 * in_graph_ns / total_ns) if total_ns else 0.0,
                })
            rows.sort(key=lambda r: r["pct"], reverse=True)
            return rows[:limit]


# === stall 聚合(阶段 2 PC Sampling / Deep Evidence) ===================

@dataclass(slots=True, frozen=True)
class StallSample:
    """一条已聚合的 stall 记录 —— 原生 .so 每次 drain 在库内预聚合后吐过来的最小载荷。

    不是单个 PC 样本(那是百万/s,绝不过桥);而是 (kernel, reason) 在本批的累计样本数。
    这就是 5% 预算的命门:原生侧聚合,Python 只收这种小行。
    """

    kernel: str       # kernel 名(mangled / demangled)
    reason: str       # 原始 PerfWorks stall reason 名(如 ..._long_scoreboard)
    samples: int      # 该 (kernel, reason) 本批样本数


@dataclass(slots=True, frozen=True)
class PcSample:
    """P3:一条 per-PC 聚合行(某 kernel 内某指令地址的累计样本)。

    cubinCrc + pcOffset 是连到源码行/SASS 偏移的钥匙(见 native/sass_source.py)。
    同样在库内预聚合,只在 deep-evidence 窗末取一次,不过单样本桥。
    """

    kernel: str       # kernel 函数名(关联用,须与 cubin 符号同源)
    cubin_crc: int    # CUpti cubinCrc(匹配磁盘 cubin)
    pc_offset: int    # 函数内指令偏移
    samples: int      # 该 PC 的累计样本数


@dataclass(slots=True, frozen=True)
class LaunchSample:
    """P3 launch 栈(MVP):某 kernel 的 native 启动栈 + 本批 launch 次数。

    向外归因 —— 即便闭源 GEMM 进不去,也知道它从哪段 host 代码(nn.Linear / vLLM 自定义算子)
    launch。kernel 名可能是 cuFuncGetName 解析的真名,或解析失败时的 func_<ptr>(此时靠 stack
    里的算子帧识别)。
    """

    kernel: str       # cuFuncGetName 名,或 func_<ptr>(runtime 注册 kernel 解析不到名)
    launches: int     # 本批 launch 次数
    stack: str        # 符号化 native 栈(" <- " 连接,top→down 到 Python 解释器边界)


# launch 栈里的"启动原语"帧(跳过它们,下一帧才是真正的算子 / kernel 身份)
_LAUNCH_PRIM_PREFIXES = ("cudaLaunchKernel", "cuLaunchKernel", "cublas")


def _launch_identity(stack: str) -> str:
    """从 launch 栈取算子身份 token —— 跳过启动原语帧,取第一帧算子名的基名。
    例:'cudaLaunchKernel <- fused_add_rms_norm(at::Tensor&...) <- ...' → 'fused_add_rms_norm'。"""
    for frame in stack.split(" <- "):
        f = frame.strip()
        if any(f.startswith(p) for p in _LAUNCH_PRIM_PREFIXES):
            continue
        base = f[5:] if f.startswith("void ") else f
        for sep in ("(", "<"):
            i = base.find(sep)
            if i > 0:
                base = base[:i]
        base = base.strip().split("::")[-1]
        token = "".join(ch for ch in base if ch.isalnum() or ch == "_")
        if len(token) >= 4:
            return token
    return ""


class StallAggregator:
    """累加一窗内的 stall 样本,roll-up 时算出语义类占比 + per-kernel stall 画像。

    口径(设计文档 §11):各语义类占 **stall 样本**(= 总样本 − issued);`issued`(selected)
    是已发射、非 stall,单独占总样本。线程安全同 WindowAggregator。
    """

    def __init__(self, classifier: StallClassifier | None = None) -> None:
        self._clf = classifier or StallClassifier()
        self._kcls = KernelClassifier()  # mangled kernel 名 → 算子类(gemm/attention/comm/...)
        self._lock = Lock()
        self._reset_locked()

    def _reset_locked(self) -> None:
        self._cat_samples: dict[str, int] = defaultdict(int)  # 语义类(不含 issued)→ 样本
        self._issued = 0                                      # selected
        self._total = 0
        # per-kernel:kernel → {语义类/issued → 样本, "_total" → 样本}
        self._kernel: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # 语义类 → {原始 PerfWorks reason 名 → 样本}(专家下钻用,保留归并前的真实指标名)
        self._reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._has_data = False

    @property
    def has_data(self) -> bool:
        return self._has_data

    def add(self, samples: list[StallSample]) -> None:
        with self._lock:
            for s in samples:
                if s.samples <= 0:
                    continue
                self._has_data = True
                cat = self._clf.classify(s.reason)
                self._total += s.samples
                if cat == "issued":
                    self._issued += s.samples
                else:
                    self._cat_samples[cat] += s.samples
                self._reasons[cat][s.reason] += s.samples  # 留住原始 reason 名
                k = self._kernel[s.kernel]
                k[cat] += s.samples
                k["_total"] += s.samples

    def snapshot_and_reset(self) -> dict[str, float]:
        """语义类占比(占 stall 样本)+ issued 占比(占总样本)+ 总样本数,然后清零。"""
        with self._lock:
            stall_total = sum(self._cat_samples.values())     # = total − issued
            out: dict[str, float] = {}
            for cls in STALL_CLASSES:
                pct = 100.0 * self._cat_samples.get(cls, 0) / stall_total if stall_total else 0.0
                out[STALL_CLASS_TO_METRIC[cls]] = pct
            out[M.KERNEL_STALL_ISSUED_PCT] = (
                100.0 * self._issued / self._total if self._total else 0.0
            )
            out[M.KERNEL_STALL_SAMPLE_TOTAL] = float(self._total)
            self._reset_locked()
            return out

    def kernel_stall_table(self, limit: int = 25) -> list[dict[str, Any]]:
        """per-kernel stall 画像(未 reset):每行含样本数 + 主导 stall 类 + 明细。

        主导类**排除** issued(非 stall)与 scheduler_slack(高值常是好事,非瓶颈),
        这样"主导"指的是真正值得动手的 stall。按总样本降序。
        """
        with self._lock:
            # 总样本数:PC sampling 按固定周期采样,故某 kernel 的样本数 ∝ 它占用的 GPU
            # 时间。time_pct = 该 kernel 样本 / 全部样本 = 这个 kernel 的 GPU 时间占比
            # (采样估计,非精确 μs)。这让同一次采样既给"为什么慢",也给"时间花在哪"。
            grand_total = sum(c.get("_total", 0) for c in self._kernel.values())
            rows: list[dict[str, Any]] = []
            for kname, cats in self._kernel.items():
                ktotal = cats.get("_total", 0)
                if ktotal <= 0:
                    continue
                stall_total = ktotal - cats.get("issued", 0)
                dom: str | None = None
                dom_pct = 0.0
                for cls, n in cats.items():
                    if cls in ("_total", "issued", "scheduler_slack"):
                        continue
                    pct = 100.0 * n / stall_total if stall_total else 0.0
                    if pct > dom_pct:
                        dom_pct = pct
                        dom = cls
                rows.append({
                    "kernel": kname,
                    "cls": self._kcls.classify(kname),   # 算子类(gemm/attention/comm/...)
                    "samples": ktotal,
                    "time_pct": (100.0 * ktotal / grand_total) if grand_total else 0.0,
                    "stall_samples": stall_total,
                    "dominant_stall": dom,
                    "dominant_pct": dom_pct,
                    "breakdown": {c: v for c, v in cats.items() if c != "_total"},
                })
            rows.sort(key=lambda r: r["samples"], reverse=True)
            return rows[:limit]

    def kernel_class_shares(self) -> list[dict[str, Any]]:
        """按算子类聚合的 GPU 时间占比(全部 kernel,非 top-N)。样本数 ∝ GPU 时间,
        故每类样本占比 = 该类算子吃掉的 GPU 时间占比。按占比降序。"""
        with self._lock:
            grand_total = sum(c.get("_total", 0) for c in self._kernel.values())
            acc: dict[str, int] = defaultdict(int)
            for kname, cats in self._kernel.items():
                acc[self._kcls.classify(kname)] += cats.get("_total", 0)
            shares = [
                {"cls": cls, "time_pct": (100.0 * n / grand_total) if grand_total else 0.0}
                for cls, n in acc.items()
            ]
            shares.sort(key=lambda d: d["time_pct"], reverse=True)
            return shares

    def stall_reason_detail(self, top_per_class: int = 6) -> dict[str, list[dict[str, Any]]]:
        """每个语义类底下的原始 PerfWorks stall reason 名 + 样本(专家下钻)。
        排除 issued(非 stall)。每类按样本降序取 top。"""
        with self._lock:
            out: dict[str, list[dict[str, Any]]] = {}
            for cls, reasons in self._reasons.items():
                if cls == "issued":
                    continue
                rows = sorted(
                    ({"reason": r, "samples": n} for r, n in reasons.items()),
                    key=lambda d: d["samples"], reverse=True,
                )
                if rows:
                    out[cls] = rows[:top_per_class]
            return out


# === 火焰图聚合(on-demand 深度模式) ==================================

class FlamegraphAggregator:
    """把 (Python 调用栈 + kernel) → GPU 时间 累成火焰图前缀树。

    仅消费带 stack 的 kernel 事件(capture_stacks 模式)。tumbling 窗。
    对标 zymtrace 的 Python→kernel 火焰图(graph 内 kernel 归到 replay 调用点)。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._reset_locked()

    @staticmethod
    def _node(name: str, kind: str) -> dict[str, Any]:
        return {"name": name, "kind": kind, "value": 0, "children": {}}

    def _reset_locked(self) -> None:
        self._root = self._node("root", "root")
        self._total = 0

    def add(self, events: list[KernelEvent]) -> None:
        with self._lock:
            for e in events:
                if e.kind != "kernel" or e.stack is None:
                    continue
                dur = e.end_ns - e.start_ns
                if dur < 0:
                    continue
                self._total += dur
                self._root["value"] += dur
                node = self._root
                for frame in e.stack:                       # Python 帧 root→leaf
                    child = node["children"].get(frame)
                    if child is None:
                        child = self._node(frame, "python")
                        node["children"][frame] = child
                    child["value"] += dur
                    node = child
                leaf = node["children"].get(e.name)          # kernel 叶子
                if leaf is None:
                    leaf = self._node(e.name, "kernel")
                    node["children"][e.name] = leaf
                leaf["value"] += dur

    @property
    def has_data(self) -> bool:
        return self._total > 0

    def snapshot_and_reset(self, min_share: float = 0.004) -> dict[str, Any] | None:
        """转嵌套 list 树并清零。裁掉占比 < min_share 的小分支(降噪+控体积)。"""
        with self._lock:
            total = self._total
            if total <= 0:
                self._reset_locked()
                return None

            def conv(node: dict[str, Any]) -> dict[str, Any]:
                kids = [
                    conv(c) for c in sorted(node["children"].values(), key=lambda n: -n["value"])
                    if c["value"] / total >= min_share
                ]
                return {"name": node["name"], "kind": node["kind"],
                        "value": node["value"], "children": kids}

            tree = conv(self._root)
            self._reset_locked()
            return tree


# === 执行时间线(Nsight-style:保留最近 N 条原始 kernel 的时间戳) =========

class TimelineBuffer:
    """最近 N 条 kernel/memcpy 的有界环,带 GPU 时间戳。时间线视图用。

    不聚合 —— 时间线要的是单个 kernel 的 start/end/stream,看串行/空隙/通信重叠。
    """

    def __init__(self, maxlen: int = 2000) -> None:
        self._lock = Lock()
        self._buf: deque[tuple[int, int, str, str, int, int, str]] = deque(maxlen=maxlen)

    def add(self, events: list[KernelEvent], classifier: KernelClassifier) -> None:
        with self._lock:
            for e in events:
                if e.kind == "kernel":
                    self._buf.append((e.start_ns, e.end_ns, classifier.classify(e.name),
                                      "kernel", e.stream_id, 1 if e.graph_id else 0, e.name))
                elif e.kind == "memcpy":
                    self._buf.append((e.start_ns, e.end_ns, "memcpy", "memcpy",
                                      e.stream_id, 0, "memcpy"))

    def snapshot(self, max_events: int = 800) -> dict[str, Any] | None:
        """最近 max_events 条,归一化到 [0, span]。空 → None。"""
        with self._lock:
            items = list(self._buf)[-max_events:]
        items = [i for i in items if i[1] >= i[0]]  # 丢坏记录
        if not items:
            return None
        t0 = min(i[0] for i in items)
        span = max(i[1] for i in items) - t0
        streams = sorted({i[4] for i in items})
        evs = [
            {"start": i[0] - t0, "dur": i[1] - i[0], "cls": i[2], "kind": i[3],
             "stream": i[4], "in_graph": i[5], "name": i[6]}
            for i in items
        ]
        return {"span_ns": span, "streams": streams, "count": len(evs), "events": evs}

    def chrome_trace(self) -> dict[str, Any] | None:
        """导出 Chrome Trace Event 格式(Perfetto / chrome://tracing 直接打开)。

        对齐 PyTorch Kineto 的写法:ph=M 元数据命名 track(stream),ph=X 完整事件,
        ts/dur 单位微秒。专业 trace 查看器(缩放/搜索/测量)直接用,不自己画。
        """
        with self._lock:
            items = [i for i in self._buf if i[1] >= i[0]]
        if not items:
            return None
        t0 = min(i[0] for i in items)
        streams = sorted({i[4] for i in items})
        events: list[dict[str, Any]] = [
            {"ph": "M", "name": "process_name", "pid": 0, "tid": 0,
             "args": {"name": "GPU kernels (CUPTI)"}},
        ]
        for s in streams:
            events.append({"ph": "M", "name": "thread_name", "pid": 0, "tid": s,
                           "args": {"name": f"stream {s}"}})
        for start, end, cls, kind, stream, ingraph, name in items:
            events.append({
                "ph": "X", "name": name, "cat": cls, "pid": 0, "tid": stream,
                "ts": (start - t0) / 1000.0, "dur": (end - start) / 1000.0,
                "args": {"class": cls, "kind": kind, "in_graph": bool(ingraph)},
            })
        return {"displayTimeUnit": "ms", "traceEvents": events}


# === collector(编排:source → classifier → aggregator → sink) =========

class CuptiKernelCollector:
    """编排 kernel 采集。镜像 NvmlSampler 的生命周期与故障隔离哲学。

    roll-up 在采集源回调线程里按 clock 节奏触发(无额外线程):每批记录累加进
    aggregator,距上次 roll-up ≥ interval 就 snapshot + push 派生指标。
    """

    def __init__(
        self,
        sink: Sink,
        engine_index: int = 0,
        source: KernelActivitySource | None = None,
        classifier: KernelClassifier | None = None,
        rollup_interval_s: float = DEFAULT_ROLLUP_INTERVAL_S,
        clock: Callable[[], int] = wall_ns,
        top_n: int = 100,
        capture_stacks: bool = False,
        pc_sampling: PcSamplingController | None = None,
    ) -> None:
        self._sink = sink
        self._engine_index = engine_index
        self._source = source if source is not None else _default_source(capture_stacks)
        # Deep Evidence(阶段 2 PC Sampling)按需取证控制器;None=未配置(优雅缺省)
        self._pcs = pc_sampling
        self._classifier = classifier or KernelClassifier()
        self._agg = WindowAggregator(self._classifier)
        self._flame = FlamegraphAggregator()           # 火焰图(仅 capture_stacks 模式有数据)
        self._timeline = TimelineBuffer()              # 执行时间线(最近 N 条原始 kernel)
        self._rollup_interval_ns = int(rollup_interval_s * 1e9)
        self._clock = clock
        self._top_n = top_n
        self._enabled = False
        self._started = Event()
        self._last_rollup_ns: int | None = None
        self._cb_total_ns = 0  # 本窗回调累计耗时(自我观测)
        # 最近一窗的 per-kernel 原始明细(API 直接读,不走 metric 管道)
        self._last_kernels: list[dict[str, Any]] = []
        self._last_flame: dict[str, Any] | None = None   # 最近一窗火焰图树
        # 这一窗快照的时刻(monotonic ns)+ 窗宽,让前端能显示"X 秒前 / 最近 Ys 窗口"
        self._last_kernels_ts: int | None = None
        self._last_window_ns = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def top_kernels(self) -> list[dict[str, Any]]:
        """最近一窗的 per-kernel 原始明细表(未聚合)。供 API / dashboard 钻取。"""
        return self._last_kernels

    def flamegraph(self) -> dict[str, Any] | None:
        """最近一窗的 Python 调用栈 → kernel 火焰图树。None=未开 capture_stacks 或无数据。"""
        return self._last_flame

    def timeline(self, max_events: int = 800) -> dict[str, Any] | None:
        """最近 max_events 条 kernel 的执行时间线(start/end/stream)。供时间线视图。"""
        return self._timeline.snapshot(max_events)

    def chrome_trace(self) -> dict[str, Any] | None:
        """最近 kernel 的 Chrome Trace JSON(Perfetto/chrome://tracing 打开)。"""
        return self._timeline.chrome_trace()

    # === Deep Evidence(阶段 2 PC Sampling 按需取证)===

    def pc_sampling_available(self) -> bool:
        """PC Sampling 取证当前能不能用。

        优先看 `started`(已 prime/start,可靠且与线程无关)——`available` 走
        `pping_pcs_available()` 查的是**当前线程**的 CUDA context,在 API 线程池里
        没 context 会误报 False,虽然采样在 worker 线程好好跑着。已 prime 即视为可用。
        """
        if self._pcs is None:
            return False
        return self._pcs.started or self._pcs.available

    def run_deep_evidence(self, window_s: float = 5.0, period_log2: int = 16) -> dict[str, Any]:
        """跑一个 PC Sampling 取证短窗(阻塞 ~window_s),返回 stall 分解结论。
        未配置 / 不可用 → fail-closed 的 {available: False, error: ...}。"""
        if self._pcs is None:
            return {"available": False, "error": "PC Sampling 未配置(Deep Evidence 不可用)"}
        return self._pcs.run_window(window_s=window_s, period_log2=period_log2)

    def last_stall_result(self) -> dict[str, Any] | None:
        """最近一次取证结果(给 API 读)。None=从未跑过。"""
        return self._pcs.last_result() if self._pcs is not None else None

    @property
    def last_snapshot_ts(self) -> int | None:
        """最近一次"有数据"的 rollup 快照时刻(monotonic ns)。None=从未有过。
        注意:空窗(无流量)会被跳过,所以这个时间戳只在有 GPU 活动时推进 ——
        前端据此判断数据是实时还是"无流量后停在最后一窗"。"""
        return self._last_kernels_ts

    @property
    def last_window_ns(self) -> int:
        """最近快照覆盖的窗宽(ns)。"""
        return self._last_window_ns

    def start(self) -> None:
        if self._source is None or not self._source.available():
            logger.warning(
                "[pping-lang] CUPTI source unavailable; kernel tracing disabled "
                "(needs Linux + cupti-python + GPU)"
            )
            return
        if self._started.is_set():
            return
        try:
            self._source.start(self._on_records)
            self._started.set()
            self._enabled = True
            logger.info("[pping-lang] CUPTI kernel tracing started")
        except Exception as e:
            logger.warning("[pping-lang] CUPTI start failed, kernel tracing disabled: %s", e)
            self._enabled = False

    def stop(self) -> None:
        if not self._started.is_set():
            return
        try:
            self._source.stop()
        except Exception:
            logger.exception("[pping-lang] CUPTI source stop failed")
        # 最后一窗 flush 出去,别丢
        if self._last_rollup_ns is not None:
            try:
                self._rollup(self._clock())
            except Exception:
                logger.exception("[pping-lang] CUPTI final rollup failed")
        self._started.clear()
        self._enabled = False

    # === 采集源回调(运行在 CUPTI worker 线程) ===

    def _on_records(self, events: list[KernelEvent]) -> None:
        try:
            t0 = self._clock()
            self._agg.add(events)
            self._flame.add(events)
            self._timeline.add(events, self._classifier)
            self._cb_total_ns += self._clock() - t0
            now = self._clock()
            if self._last_rollup_ns is None:
                self._last_rollup_ns = now
                return
            if now - self._last_rollup_ns >= self._rollup_interval_ns:
                self._rollup(now)
        except Exception:
            # 回调异常绝不传播给 CUPTI / vLLM
            logger.exception("[pping-lang] CUPTI record handling failed")

    def _rollup(self, now: int) -> None:
        # 显式 None 判断,不能用 `or`:last_rollup 合法地可能为 0(单调时钟起点)
        if self._last_rollup_ns is None:
            self._last_rollup_ns = now
            return
        # 本窗墙钟跨度(时长,非绝对时刻)—— 别和 clock.wall_ns() 那个时间戳函数混淆
        window_span_ns = now - self._last_rollup_ns
        if window_span_ns <= 0:
            # 零跨度窗(如 rollup 后立即 stop)无意义,跳过 —— 别用空窗 0 覆盖真值
            self._last_rollup_ns = now
            return
        if not self._agg.has_data:
            # 窗内无任何 GPU 活动(stop 收尾的空窗 / 纯 idle):不 push 误导性全 0
            self._last_rollup_ns = now
            return
        # 先快照 per-kernel 原始明细(读,不 reset),再 snapshot_and_reset 出标量指标
        self._last_kernels = self._agg.kernel_table(self._top_n)
        if self._flame.has_data:
            self._last_flame = self._flame.snapshot_and_reset()
        self._last_kernels_ts = now      # 只在"有数据"时推进 → 前端可判断是否过期
        self._last_window_ns = window_span_ns
        stats = self._agg.snapshot_and_reset(window_span_ns)
        push = self._sink.push_metric
        ei = self._engine_index
        for name, value in stats.items():
            push(MetricPoint(now, name, value, ei))
        # 自我观测:回调耗时 + 累计丢弃(预算自守的输入)
        push(MetricPoint(now, M.PPING_LANG_CUPTI_CB_MS, self._cb_total_ns / 1e6, ei))
        try:
            dropped = self._source.dropped_records()
        except Exception:
            dropped = 0
        push(MetricPoint(now, M.PPING_LANG_CUPTI_DROPPED_TOTAL, float(dropped), ei))
        self._cb_total_ns = 0
        self._last_rollup_ns = now


# === 采集源实现 ======================================================

class FakeActivitySource:
    """测试 / 开发用采集源 —— 手动 emit 合成记录。让全部聚合逻辑在无 GPU
    的机器(如 Windows)上可测。"""

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self._cb: RecordsCallback | None = None
        self._dropped = 0

    def available(self) -> bool:
        return self._available

    def start(self, on_records: RecordsCallback) -> None:
        self._cb = on_records

    def stop(self) -> None:
        self._cb = None

    def dropped_records(self) -> int:
        return self._dropped

    # --- 测试钩子 ---
    def emit(self, events: list[KernelEvent]) -> None:
        if self._cb is not None:
            self._cb(events)

    def set_dropped(self, n: int) -> None:
        self._dropped = n


class CuptiPythonSource:
    """进程内 cupti-python Activity API 采集源(阶段 1a)。

    Linux x86_64 only。import 失败(Windows / 无包)→ available() False → 优雅禁用。
    真实采集只能在 Linux/WSL/远程 GPU 验证(见采集设计 §7 烟雾测试)。
    """

    # 抓 Python 栈的 launch API（运行时 + 驱动）。capture_stacks 模式才订阅。
    _RUNTIME_LAUNCH_CBIDS = ("cudaLaunchKernel_v13000", "cudaGraphLaunch_v10000",
                             "cudaLaunchKernelExC_v11060")
    _DRIVER_LAUNCH_CBIDS = ("cuLaunchKernel", "cuGraphLaunch", "cuLaunchKernelEx")

    def __init__(self, buffer_size: int = 8 * 1024 * 1024,
                 capture_stacks: bool = False, stack_depth: int = 32) -> None:
        self._buffer_size = buffer_size
        self._capture_stacks = capture_stacks
        self._stack_depth = stack_depth
        self._cb: RecordsCallback | None = None
        self._cupti = _import_cupti()
        # correlation_id → 发起 kernel 的 Python 栈(callback 线程写,buffer 线程消费)
        self._pending: dict[int, tuple[str, ...]] = {}
        self._pending_lock = Lock()
        self._subscriber: int | None = None
        self._cb_fn: Any = None     # 保活:cupti 持有回调引用
        self._enter_site: Any = None

    def available(self) -> bool:
        return self._cupti is not None

    def start(self, on_records: RecordsCallback) -> None:
        if self._cupti is None:
            raise RuntimeError("cupti-python not importable")
        self._cb = on_records
        c = self._cupti
        for kind in (
            c.ActivityKind.CONCURRENT_KERNEL,
            c.ActivityKind.MEMCPY,
            c.ActivityKind.SYNCHRONIZATION,
        ):
            c.activity_enable(kind)
        c.activity_register_callbacks(self._buffer_requested, self._buffer_completed)
        c.activity_flush_period(1000)  # ~1s 自动 flush,贴合实时
        if self._capture_stacks:
            self._start_stack_capture()

    def _start_stack_capture(self) -> None:
        c = self._cupti
        self._enter_site = c.ApiCallbackSite.API_ENTER
        self._cb_fn = self._launch_callback
        self._subscriber = c.subscribe(self._cb_fn, 0)
        for nm in self._RUNTIME_LAUNCH_CBIDS:
            cbid = getattr(c.runtime_api_trace_cbid, nm, None)
            if cbid is not None:
                c.enable_callback(1, self._subscriber, c.CallbackDomain.RUNTIME_API, int(cbid))
        for nm in self._DRIVER_LAUNCH_CBIDS:
            cbid = getattr(c.driver_api_trace_cbid, nm, None)
            if cbid is not None:
                c.enable_callback(1, self._subscriber, c.CallbackDomain.DRIVER_API, int(cbid))

    def stop(self) -> None:
        if self._cupti is None:
            return
        c = self._cupti
        c.activity_flush_all(1)  # flag 1 = forced:强制交付未满 buffer 的残留记录,别丢尾部
        for kind in (
            c.ActivityKind.CONCURRENT_KERNEL,
            c.ActivityKind.MEMCPY,
            c.ActivityKind.SYNCHRONIZATION,
        ):
            try:
                c.activity_disable(kind)
            except Exception:
                pass
        if self._subscriber is not None:
            try:
                c.unsubscribe(self._subscriber)
            except Exception:
                pass
            self._subscriber = None
        self._cb = None

    def dropped_records(self) -> int:
        # TODO(1a 验证): cupti.activity_get_num_dropped_records 需要 context/stream
        # 句柄,接线时补。原型先返回 0。
        return 0

    # --- Callback API:在 launch 处抓 Python 栈(launch 线程,同步) ---
    def _launch_callback(self, userdata, domain, cbid, cbdata) -> None:  # noqa: ANN001
        try:
            if cbdata.callback_site != self._enter_site:
                return
            # sys._getframe(1) = 调 launch 的 Python 帧;沿 f_back 走到 root。
            # 用 co_name 而非 traceback.extract_stack(避免读源码,开销低很多)。
            frames: list[str] = []
            f: Any = sys._getframe(1)
            depth = self._stack_depth
            while f is not None and len(frames) < depth:
                frames.append(f.f_code.co_name)
                f = f.f_back
            frames.reverse()  # root→leaf
            with self._pending_lock:
                if len(self._pending) > 300_000:  # 安全上限:防未匹配栈无限涨
                    self._pending.clear()
                self._pending[cbdata.correlation_id] = tuple(frames)
        except Exception:
            pass

    # --- cupti 回调(CUPTI worker 线程) ---
    def _buffer_requested(self) -> tuple[int, int]:
        return self._buffer_size, 0  # (size, max_records=0 不限)

    def _buffer_completed(self, activities: list) -> None:
        if self._cb is None:
            return
        c = self._cupti
        kernel_kinds = (c.ActivityKind.CONCURRENT_KERNEL, c.ActivityKind.KERNEL)
        events: list[KernelEvent] = []
        pending = self._pending
        max_cid = 0
        for a in activities:
            kind = a.kind
            if kind in kernel_kinds:
                stack = None
                cid = a.correlation_id
                if cid > max_cid:
                    max_cid = cid
                if self._capture_stacks:
                    # .get 不 pop:一次 cuGraphLaunch 下的所有 kernel 共享同一个
                    # correlation_id,pop 会让除第一个外的 graph kernel 全丢栈。
                    with self._pending_lock:
                        stack = pending.get(cid)
                events.append(KernelEvent(
                    "kernel", a.name, a.start, a.end,
                    getattr(a, "graph_id", 0) or 0, stack, getattr(a, "stream_id", 0) or 0,
                ))
            elif kind == c.ActivityKind.MEMCPY:
                events.append(KernelEvent(
                    "memcpy", "memcpy", a.start, a.end, getattr(a, "graph_id", 0) or 0,
                    None, getattr(a, "stream_id", 0) or 0,
                ))
            elif kind == c.ActivityKind.SYNCHRONIZATION:
                events.append(KernelEvent("sync", "sync", a.start, a.end))
        # 批末清理:本批已消费的 launch(cid <= 本批最大 kernel cid)删掉,防膨胀;
        # cid > max 的留着(其 kernel 还没交付)。比 pop 安全,又不无限涨。
        if self._capture_stacks and max_cid:
            with self._pending_lock:
                stale = [k for k in self._pending if k <= max_cid]
                for k in stale:
                    del self._pending[k]
        self._cb(events)


def _import_cupti():  # noqa: ANN202 - 动态模块
    try:
        from cupti import cupti
        return cupti
    except Exception:
        return None


def _default_source(capture_stacks: bool = False) -> KernelActivitySource:
    # 总是返回实例;collector.start() 用 available() 决定启用还是优雅禁用。
    return CuptiPythonSource(capture_stacks=capture_stacks)


# === PC Sampling 原生桥(阶段 2 Deep Evidence)========================
#
# 这是 libppingcupti.so 的 ctypes 封装。**Deep Evidence Mode**(设计文档 §11):
# PC Sampling 不是 always-on,而是操作员/规则按需触发的短窗取证。失败一律 fail-closed
# (库加载失败 / start 失败 → available()=False),绝不阻塞 always-on 主干。
#
# 集成现状(§12):in-process 与 torch 共存时 start 可能失效(torch 占 CUPTI),
# 彻底解决靠 1b 注入式。这里的 fail-closed 让"不可用"成为优雅降级而非崩溃。


class _PpingStallRow(ctypes.Structure):
    """镜像 pping_lang/native/ppingcupti.h 的 PpingStallRow(固定 272 字节)。"""
    _fields_ = [
        ("stall_reason", ctypes.c_uint),
        ("_pad", ctypes.c_uint),
        ("samples", ctypes.c_ulonglong),
        ("kernel", ctypes.c_char * 256),
    ]


class _PpingPcRow(ctypes.Structure):
    """镜像 PpingPcRow(P3 per-PC 直方图行):cubinCrc + pcOffset + samples + kernel。"""
    _fields_ = [
        ("cubin_crc", ctypes.c_ulonglong),
        ("pc_offset", ctypes.c_ulonglong),
        ("samples", ctypes.c_ulonglong),
        ("kernel", ctypes.c_char * 256),
    ]


class _PpingLaunchRow(ctypes.Structure):
    """镜像 PpingLaunchRow(P3 launch 栈 MVP):launches + kernel 名 + 符号化 native 栈。"""
    _fields_ = [
        ("launches", ctypes.c_ulonglong),
        ("kernel", ctypes.c_char * 256),
        ("stack", ctypes.c_char * 768),
    ]


def _default_so_path() -> str:
    """libppingcupti.so 路径:env 覆盖 > 仓库内 native/ 构建产物。"""
    env = os.environ.get("PPING_LANG_PCS_SO")
    if env:
        return env
    return str(Path(__file__).resolve().parents[3] / "native" / "ppingcupti" / "libppingcupti.so")


class PcSamplingLib(Protocol):
    """libppingcupti.so 的窄接口。真实=ctypes;测试=FakePcSamplingLib。"""

    def available(self) -> bool: ...
    def start(self, period_log2: int) -> int: ...        # 0=成功,负=错误码
    def stop(self) -> int: ...
    def drain(self) -> list[StallSample]: ...            # 已聚合行(reason 已解析成名)
    def drain_pc(self) -> list[PcSample]: ...            # P3 per-PC 直方图(可空=未开/老 .so)
    def drain_launches(self) -> list[LaunchSample]: ...  # P3 launch 栈(可空=未开/老 .so)
    def overhead(self) -> tuple[float, int, int]: ...    # (getdata_ms, dropped, hwfull)
    def last_error(self) -> str: ...


class CtypesPcSamplingLib:
    """真实 libppingcupti.so via ctypes(RTLD_DEEPBIND)。

    加载失败(非 Linux / 无 .so / 缺 CUPTI)→ available() False,fail-closed。
    DEEPBIND:进程里可能并存多个 libcupti,让 .so 优先用自己 rpath 的版本(§12)。
    """

    _MAX_ROWS = 16384
    _MAX_PC_ROWS = 200000   # per-PC 直方图行远多于 (kernel,reason);宽裕些,溢出计入 dropped

    def __init__(self, so_path: str | None = None) -> None:
        self._lib: Any = None
        self._pc_buf: Any = None
        self._lc_buf: Any = None
        self._reason_cache: dict[int, str] = {}
        path = so_path or _default_so_path()
        try:
            mode = getattr(os, "RTLD_NOW", 2) | getattr(os, "RTLD_DEEPBIND", 0)
            self._lib = ctypes.CDLL(path, mode=mode)
            self._bind()
        except Exception as e:  # noqa: BLE001 — 加载失败必须优雅降级
            logger.warning("[pping-lang] libppingcupti 加载失败(PC Sampling 禁用): %s", e)
            self._lib = None

    def _bind(self) -> None:
        c = self._lib
        c.pping_pcs_available.restype = ctypes.c_int
        c.pping_pcs_start.argtypes = [ctypes.c_int]
        c.pping_pcs_start.restype = ctypes.c_int
        c.pping_pcs_stop.restype = ctypes.c_int
        c.pping_pcs_drain.argtypes = [ctypes.POINTER(_PpingStallRow), ctypes.c_int]
        c.pping_pcs_drain.restype = ctypes.c_int
        # P3 per-PC 直方图 drain(老 .so 没有此符号 → getattr 容错,降级为空)
        if hasattr(c, "pping_pcs_drain_pc"):
            c.pping_pcs_drain_pc.argtypes = [ctypes.POINTER(_PpingPcRow), ctypes.c_int]
            c.pping_pcs_drain_pc.restype = ctypes.c_int
        # P3 launch 栈 drain(同上,老 .so 无此符号则降级)
        if hasattr(c, "pping_pcs_drain_launches"):
            c.pping_pcs_drain_launches.argtypes = [ctypes.POINTER(_PpingLaunchRow), ctypes.c_int]
            c.pping_pcs_drain_launches.restype = ctypes.c_int
        c.pping_pcs_stall_reason_name.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.c_int]
        c.pping_pcs_stall_reason_name.restype = ctypes.c_int
        c.pping_pcs_overhead.argtypes = [
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
        ]
        c.pping_pcs_last_error.restype = ctypes.c_char_p
        self._buf = (_PpingStallRow * self._MAX_ROWS)()

    def available(self) -> bool:
        if self._lib is None:
            return False
        try:
            return bool(self._lib.pping_pcs_available())
        except Exception:
            return False

    def start(self, period_log2: int) -> int:
        if self._lib is None:
            return -100
        return int(self._lib.pping_pcs_start(period_log2))

    def stop(self) -> int:
        if self._lib is None:
            return -100
        return int(self._lib.pping_pcs_stop())

    def _reason_name(self, idx: int) -> str:
        nm = self._reason_cache.get(idx)
        if nm is None:
            buf = ctypes.create_string_buffer(160)
            self._lib.pping_pcs_stall_reason_name(idx, buf, 160)
            nm = buf.value.decode(errors="replace") or f"reason_{idx}"
            self._reason_cache[idx] = nm
        return nm

    def drain(self) -> list[StallSample]:
        if self._lib is None:
            return []
        n = int(self._lib.pping_pcs_drain(self._buf, self._MAX_ROWS))
        out: list[StallSample] = []
        for i in range(max(0, n)):
            row = self._buf[i]
            out.append(StallSample(
                kernel=row.kernel.decode(errors="replace"),
                reason=self._reason_name(row.stall_reason),
                samples=int(row.samples),
            ))
        return out

    def drain_pc(self) -> list[PcSample]:
        """P3:拉走 per-PC 直方图(snapshot-swap)。需 .so 带 drain_pc 符号 + 运行时
        PPING_LANG_PCS_PC_HIST=1 才有数据;否则返回空(优雅降级)。"""
        if self._lib is None or not hasattr(self._lib, "pping_pcs_drain_pc"):
            return []
        if getattr(self, "_pc_buf", None) is None:
            self._pc_buf = (_PpingPcRow * self._MAX_PC_ROWS)()
        n = int(self._lib.pping_pcs_drain_pc(self._pc_buf, self._MAX_PC_ROWS))
        out: list[PcSample] = []
        for i in range(max(0, n)):
            row = self._pc_buf[i]
            out.append(PcSample(
                kernel=row.kernel.decode(errors="replace"),
                cubin_crc=int(row.cubin_crc),
                pc_offset=int(row.pc_offset),
                samples=int(row.samples),
            ))
        return out

    def drain_launches(self) -> list[LaunchSample]:
        """P3:拉走 per-kernel launch 栈。需 .so 带符号 + PPING_LANG_PCS_LAUNCH_STACK=1。"""
        if self._lib is None or not hasattr(self._lib, "pping_pcs_drain_launches"):
            return []
        if getattr(self, "_lc_buf", None) is None:
            self._lc_buf = (_PpingLaunchRow * 4096)()
        n = int(self._lib.pping_pcs_drain_launches(self._lc_buf, 4096))
        out: list[LaunchSample] = []
        for i in range(max(0, n)):
            row = self._lc_buf[i]
            out.append(LaunchSample(
                kernel=row.kernel.decode(errors="replace"),
                launches=int(row.launches),
                stack=row.stack.decode(errors="replace"),
            ))
        return out

    def overhead(self) -> tuple[float, int, int]:
        if self._lib is None:
            return (0.0, 0, 0)
        gd = ctypes.c_double()
        dr = ctypes.c_ulonglong()
        hf = ctypes.c_ulonglong()
        self._lib.pping_pcs_overhead(ctypes.byref(gd), ctypes.byref(dr), ctypes.byref(hf))
        return (gd.value, int(dr.value), int(hf.value))

    def last_error(self) -> str:
        if self._lib is None:
            return "libppingcupti not loaded"
        try:
            return (self._lib.pping_pcs_last_error() or b"").decode(errors="replace")
        except Exception:
            return ""


class FakePcSamplingLib:
    """测试 / 无 GPU 用的 PC Sampling 库替身。脚本化 drain 返回的合成行。"""

    def __init__(self, *, available: bool = True, start_rc: int = 0,
                 drain_batches: list[list[StallSample]] | None = None,
                 pc_batches: list[list[PcSample]] | None = None,
                 launch_batches: list[list[LaunchSample]] | None = None) -> None:
        self._available = available
        self._start_rc = start_rc
        self._batches = list(drain_batches or [])
        self._pc_batches = list(pc_batches or [])
        self._launch_batches = list(launch_batches or [])
        self._overhead = (0.0, 0, 0)
        self.started = False

    def available(self) -> bool:
        return self._available

    def start(self, period_log2: int) -> int:
        if self._start_rc == 0:
            self.started = True
        return self._start_rc

    def stop(self) -> int:
        self.started = False
        return 0

    def drain(self) -> list[StallSample]:
        return self._batches.pop(0) if self._batches else []

    def drain_pc(self) -> list[PcSample]:
        return list(self._pc_batches.pop(0)) if self._pc_batches else []

    def drain_launches(self) -> list[LaunchSample]:
        return list(self._launch_batches.pop(0)) if self._launch_batches else []

    def overhead(self) -> tuple[float, int, int]:
        return self._overhead

    def set_overhead(self, getdata_ms: float, dropped: int, hwfull: int) -> None:
        self._overhead = (getdata_ms, dropped, hwfull)

    def last_error(self) -> str:
        return "" if self._start_rc == 0 else f"fake start_rc={self._start_rc}"


class PcSamplingController:
    """Deep Evidence 编排:**早 prime 一次** + 按需 drain 短窗,聚合成"为什么慢"的结论。

    ★ 关键(真机验证,设计文档 §12):PC Sampling 的 enable 必须在 workload 干重活**之前**
    调(否则 `getNumStallReasons` 返 0)。所以模型不是"按需晚 start/stop",而是:
      prime()  —— 早期(vLLM 启动前/warmup 后)enable+start 一次,drain 线程持续累。
      run_window() —— 按需 drain 一段时间,取这段窗的 stall 分解;**不重新 start、不 stop**。
    所有失败 fail-closed 成 {available: False, error: ...}。
    """

    def __init__(
        self,
        lib: PcSamplingLib | None = None,
        *,
        classifier: StallClassifier | None = None,
        sink: Sink | None = None,
        engine_index: int = 0,
        top_n: int = 50,
    ) -> None:
        self._lib = lib if lib is not None else CtypesPcSamplingLib()
        self._classifier = classifier or StallClassifier()
        self._sink = sink
        self._engine_index = engine_index
        self._top_n = top_n
        self._busy = Lock()
        self._started = False
        self._last_result: dict[str, Any] | None = None
        self._correlator: Any = None       # P3 SourceCorrelator,惰性建
        self._correlator_init = False

    @property
    def available(self) -> bool:
        return self._lib.available()

    @property
    def started(self) -> bool:
        return self._started

    def last_result(self) -> dict[str, Any] | None:
        return self._last_result

    def prime(self, period_log2: int = 16) -> dict[str, Any]:
        """早期 enable+start 一次(幂等)。必须在 workload 干重活前调。"""
        if self._started:
            return {"available": True}
        if not self._lib.available():
            return self._fail("PC Sampling 不可用(需 Linux + libppingcupti + 放开 profiling 权限)")
        rc = self._lib.start(period_log2)
        if rc != 0:
            return self._fail(f"start 失败 rc={rc}: {self._lib.last_error()}(enable 须早于 workload 重活)")
        self._started = True
        return {"available": True}

    def reprime(self, period_log2: int = 16) -> dict[str, Any]:
        """停止再启动采样 —— 自愈被打断的采样(实测:cudagraph capture 会把早 prime 的
        采样打停,之后 drain 全是空窗;capture 后重启即恢复)。start-after-workload 实测可行
        (与 §12 的"enable 须早于 workload"不冲突:那是首次 enable 的硬约束,重启是已 enable
        过的 reconfigure)。"""
        try:
            self._lib.stop()
        except Exception:  # noqa: BLE001
            pass
        self._started = False
        return self.prime(period_log2)

    def close(self) -> None:
        """停止采样(进程收尾)。幂等。"""
        if self._started:
            try:
                self._lib.stop()
            except Exception:
                pass
            self._started = False

    def run_window(
        self,
        *,
        window_s: float = 5.0,
        period_log2: int = 16,   # 2^16:防 HW 缓冲溢出楔死(见 engine_pcs 注释)
        drain_interval_s: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict[str, Any]:
        """drain 一段窗,返回这段的 stall 结论。同一时刻只允许一个窗(busy 锁)。
        若尚未 prime 则懒 prime(注意:晚 prime 在 vLLM 已跑重活时会失败)。"""
        if not self._busy.acquire(blocking=False):
            return {"available": False, "error": "another deep-evidence window is running"}
        try:
            if not self._started:
                p = self.prime(period_log2)
                if not p.get("available"):
                    return p
            agg = StallAggregator(self._classifier)
            self._lib.drain()  # 清掉窗开始前已累计的,只取本窗
            self._lib.drain_pc()  # P3:同样清掉 per-PC 直方图,只取本窗
            t0 = clock()
            while clock() - t0 < window_s:
                sleep(drain_interval_s)
                agg.add(self._lib.drain())
            agg.add(self._lib.drain())  # 收尾再 drain 一次
            pcs = self._lib.drain_pc()  # P3:窗末取 per-PC 直方图(整窗累计)
            launches = self._lib.drain_launches()  # P3:per-kernel launch 栈
            getdata_ms, dropped, hwfull = self._lib.overhead()
            table = agg.kernel_stall_table(self._top_n)
            class_shares = agg.kernel_class_shares()    # 须在 snapshot_and_reset(清零)之前取
            reason_detail = agg.stall_reason_detail()   # 同上,清零前取原始 reason 名
            stats = agg.snapshot_and_reset()
            shares = sorted(
                ({"cls": cls, "pct": stats[STALL_CLASS_TO_METRIC[cls]]} for cls in STALL_CLASSES),
                key=lambda d: d["pct"], reverse=True,
            )
            hotspots = self._pc_hotspots(pcs)
            self._attach_launch_stacks(hotspots, launches)
            result: dict[str, Any] = {
                "available": True,
                "window_s": window_s,
                "period_log2": period_log2,
                "sample_total": stats[M.KERNEL_STALL_SAMPLE_TOTAL],
                "issued_pct": stats[M.KERNEL_STALL_ISSUED_PCT],
                "stall_shares": shares,
                "kernel_class_shares": class_shares,
                "reason_detail": reason_detail,
                "kernel_table": table,
                "pc_hotspots": hotspots,
                "overhead": {"getdata_ms": getdata_ms, "dropped": dropped, "hwfull": hwfull},
                "error": None,
            }
            self._push_metrics(stats)
            self._last_result = result
            return result
        finally:
            self._busy.release()

    # P3 行级归因:每窗最多关联的 kernel 数 / 每 kernel 取样关联的热点 PC 数(界定开销)
    _PC_TOP_KERNELS = 12
    _PC_TOP_OFFSETS = 40
    # top-N 之外再多扫这么多 kernel,专门捞"能映射到源码行"的(让源码行轨即使不在最热也可见)
    _PC_EXTRA_SCAN = 20

    def _get_correlator(self) -> Any:
        if not self._correlator_init:
            self._correlator_init = True
            try:
                from pping_lang.native.sass_source import SourceCorrelator  # noqa: PLC0415
                self._correlator = SourceCorrelator()
            except Exception:  # noqa: BLE001 — 关联是增益,失败不影响采集
                self._correlator = None
        return self._correlator

    def _pc_hotspots(self, pcs: list[PcSample]) -> list[dict[str, Any]]:
        """把 per-PC 直方图压成 per-kernel"最深热点"(双轨:源码行 / SASS 偏移+名解码)。

        可映射(Triton/带 lineinfo)→ 按源码行聚合,给 top 行 + 占比;
        闭源(cutlass/flash)→ 给最热 SASS 偏移 + kernel 名解码(tile/dtype)。
        """
        if not pcs:
            return []
        # 按 kernel 聚合
        by_kernel: dict[str, list[PcSample]] = {}
        for p in pcs:
            by_kernel.setdefault(p.kernel, []).append(p)
        ranked = sorted(by_kernel.items(),
                        key=lambda kv: sum(s.samples for s in kv[1]), reverse=True)
        corr = self._get_correlator()
        from pping_lang.native.sass_source import decode_kernel_name  # noqa: PLC0415

        def build_entry(kernel: str, samples: list[PcSample],
                        *, minor: bool = False) -> dict[str, Any] | None:
            total = sum(s.samples for s in samples)
            if total <= 0:
                return None
            samples.sort(key=lambda s: s.samples, reverse=True)
            entry: dict[str, Any] = {
                "kernel": kernel, "samples": total, "minor": minor,
                "decode": decode_kernel_name(kernel),
                "mappable": False, "lines": [], "sass": [],
            }
            # 源码行聚合(只关联本 kernel 最热的若干 PC,界定开销)
            by_line: dict[str, dict[str, Any]] = {}
            if corr is not None and corr.available:
                for s in samples[:self._PC_TOP_OFFSETS]:
                    src = corr.correlate(s.cubin_crc, s.pc_offset, kernel)
                    if src:
                        loc = f"{os.path.basename(src[1])}:{src[0]}"
                        rec = by_line.setdefault(loc, {"loc": loc, "samples": 0,
                                                       "path": src[1], "line": src[0]})
                        rec["samples"] += s.samples
            if by_line:
                entry["mappable"] = True
                for r in sorted(by_line.values(), key=lambda r: r["samples"], reverse=True)[:4]:
                    entry["lines"].append({"loc": r["loc"], "path": r["path"], "line": r["line"],
                                           "pct": round(100.0 * r["samples"] / total, 1),
                                           "code": corr.source_line(r["path"], r["line"])})
            else:
                for s in samples[:3]:  # 闭源轨:最热 SASS 偏移(top 3)
                    entry["sass"].append({"offset": f"0x{s.pc_offset:x}",
                                          "pct": round(100.0 * s.samples / total, 1)})
            return entry

        out: list[dict[str, Any]] = []
        for kernel, samples in ranked[:self._PC_TOP_KERNELS]:
            e = build_entry(kernel, samples)
            if e:
                out.append(e)
        # 额外扫一截,把能映射到源码行的 kernel 也带出来(标 minor)——让源码行轨即便不在最热也可见。
        # 闭源(映射不到)的不重复加,避免一堆小 SASS 条目噪音。
        seen = {e["kernel"] for e in out}
        for kernel, samples in ranked[self._PC_TOP_KERNELS:self._PC_TOP_KERNELS + self._PC_EXTRA_SCAN]:
            if kernel in seen:
                continue
            e = build_entry(kernel, samples, minor=True)
            if e and e["mappable"]:
                out.append(e)
        return out

    def _attach_launch_stacks(self, hotspots: list[dict[str, Any]],
                              launches: list[LaunchSample]) -> None:
        """P3:把 launch 栈接到 pc_hotspots —— 向外归因(闭源 GEMM ← nn.Linear)。

        join:① 精确名匹配(cuFuncGetName 解析到名的 cutlass/cuBLAS,与 PC functionName 同源);
        ② 解析不到名(runtime 注册 kernel,func_<ptr>)→ 从栈里第一帧算子名取 token,
        看是否是该 hotspot mangled 名的子串(覆盖 vLLM 自定义算子 fused_add_rms_norm 等)。
        """
        if not hotspots or not launches:
            return
        by_name = {lc.kernel: lc for lc in launches}
        # 只为"解析不到名"的 launch 算 token(已解析的走精确匹配,避免 ::impl 之类泛 token 误配)
        ident: list[tuple[str, LaunchSample]] = []
        for lc in launches:
            if lc.kernel.startswith("func_"):
                tok = _launch_identity(lc.stack)
                if tok:
                    ident.append((tok, lc))
        for h in hotspots:
            k = h["kernel"]
            chosen = by_name.get(k)
            if chosen is None:
                best: LaunchSample | None = None
                for tok, lc in ident:
                    if tok in k and (best is None or lc.launches > best.launches):
                        best = lc
                chosen = best
            if chosen is not None and chosen.stack:
                h["launch"] = {"stack": chosen.stack, "launches": chosen.launches}

    def _push_metrics(self, stats: dict[str, float]) -> None:
        if self._sink is None:
            return
        now = wall_ns()
        for name, value in stats.items():
            self._sink.push_metric(MetricPoint(now, name, value, self._engine_index))

    def _fail(self, msg: str) -> dict[str, Any]:
        res = {"available": False, "error": msg}
        self._last_result = res
        return res
