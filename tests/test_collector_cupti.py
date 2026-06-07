"""CuptiKernelCollector 测试 — 用 FakeActivitySource + 注入时钟,无需 GPU。

覆盖阶段 1a 的纯逻辑部分(语言边界以下会被原生替换的采集绑定不在此测):
分类器记忆化、窗口聚合派生指标、collector 编排 + roll-up、故障隔离。
"""
from __future__ import annotations

from pping_lang.collector.cupti import (
    CuptiKernelCollector,
    FakeActivitySource,
    FlamegraphAggregator,
    KernelClassifier,
    KernelEvent,
    StallAggregator,
    StallClassifier,
    StallSample,
    TimelineBuffer,
    WindowAggregator,
)
from pping_lang.metrics_catalog import M
from pping_lang.sink.base import Sink
from pping_lang.types import Diagnosis, MetricPoint


class _CollectingSink(Sink):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.flushed_metrics: list[MetricPoint] = []
        self.flushed_diags: list[Diagnosis] = []

    def _flush(self, metrics, diags):
        self.flushed_metrics.extend(metrics)
        self.flushed_diags.extend(diags)


class _FakeClock:
    """单调时钟,测试手动推进 —— roll-up 时机完全可控。"""

    def __init__(self, start_ns: int = 0):
        self.now = start_ns

    def __call__(self) -> int:
        return self.now

    def advance_s(self, seconds: float) -> None:
        self.now += int(seconds * 1e9)


def _kernel(name: str, dur_us: float, start_ns: int = 0, graph_id: int = 0) -> KernelEvent:
    return KernelEvent("kernel", name, start_ns, start_ns + int(dur_us * 1e3), graph_id)


# === KernelClassifier ===

def test_classifier_maps_known_vllm_kernels():
    clf = KernelClassifier()
    assert clf.classify("flash_fwd_kernel") == "attention"
    assert clf.classify("paged_attention_v2_kernel") == "attention"
    assert clf.classify("ncclDevKernel_AllReduce_Sum") == "comm"
    assert clf.classify("ampere_fp16_s16816gemm_fp16") == "gemm"
    assert clf.classify("cutlass_80_tensorop_gemm") == "gemm"
    assert clf.classify("rms_norm_kernel") == "norm"
    assert clf.classify("rotary_embedding_kernel") == "rotary"
    assert clf.classify("silu_and_mul_kernel") == "activation"


def test_classifier_unknown_falls_to_other():
    assert KernelClassifier().classify("some_mystery_kernel_xyz") == "other"


def test_classifier_memoizes():
    clf = KernelClassifier()
    assert clf.cache_size == 0
    clf.classify("flash_fwd_kernel")
    clf.classify("flash_fwd_kernel")
    clf.classify("flash_fwd_kernel")
    assert clf.cache_size == 1  # 同名只算一次


def test_classifier_comm_wins_over_gemm_ordering():
    # nccl reduce kernel 名里可能也含通用词,comm 规则在前应优先命中
    assert KernelClassifier().classify("nccl_reduce_scatter_kernel") == "comm"


# === WindowAggregator ===

def test_aggregator_kernel_class_shares_sum_to_100():
    agg = WindowAggregator(KernelClassifier())
    agg.add([
        _kernel("flash_fwd_kernel", 40.0),       # attention
        _kernel("ampere_s16816gemm", 30.0),      # gemm
        _kernel("rms_norm_kernel", 10.0),        # norm
        _kernel("mystery_kernel", 20.0),         # other
    ])
    out = agg.snapshot_and_reset(wall_ns=int(0.1 * 1e9))
    assert out[M.KERNEL_SHARE_ATTENTION_PCT] == 40.0
    assert out[M.KERNEL_SHARE_GEMM_PCT] == 30.0
    assert out[M.KERNEL_SHARE_NORM_PCT] == 10.0
    assert out[M.KERNEL_SHARE_OTHER_PCT] == 20.0
    total = sum(out[M.__dict__[k]] for k in dir(M) if k.startswith("KERNEL_SHARE_"))
    assert abs(total - 100.0) < 1e-6


def test_aggregator_gpu_busy_and_launch_rate():
    agg = WindowAggregator(KernelClassifier())
    # 100ms 窗里有 80ms kernel 计算 → busy 80%;4 个 launch → 40/s
    agg.add([_kernel("flash_fwd_kernel", 20_000.0) for _ in range(4)])  # 4 × 20ms = 80ms
    out = agg.snapshot_and_reset(wall_ns=int(0.1 * 1e9))
    assert abs(out[M.KERNEL_GPU_BUSY_PCT] - 80.0) < 1e-6
    assert abs(out[M.KERNEL_LAUNCH_COUNT_PER_S] - 40.0) < 1e-6
    assert abs(out[M.KERNEL_MEAN_DUR_US] - 20_000.0) < 1e-6


def test_aggregator_memcpy_and_sync_share_of_wall():
    agg = WindowAggregator(KernelClassifier())
    agg.add([
        _kernel("flash_fwd_kernel", 50_000.0),                  # 50ms kernel
        KernelEvent("memcpy", "HtoD", 0, int(10_000 * 1e3)),    # 10ms memcpy
        KernelEvent("sync", "sync", 0, int(5_000 * 1e3)),       # 5ms sync
    ])
    out = agg.snapshot_and_reset(wall_ns=int(0.1 * 1e9))  # 100ms 窗
    assert abs(out[M.KERNEL_MEMCPY_SHARE_PCT] - 10.0) < 1e-6
    assert abs(out[M.KERNEL_SYNC_SHARE_PCT] - 5.0) < 1e-6


def test_aggregator_in_graph_pct():
    agg = WindowAggregator(KernelClassifier())
    agg.add([
        _kernel("flash_fwd_kernel", 30.0, graph_id=7),   # in graph
        _kernel("gemm_kernel", 70.0, graph_id=0),        # not in graph
    ])
    out = agg.snapshot_and_reset(wall_ns=int(0.1 * 1e9))
    assert abs(out[M.KERNEL_IN_GRAPH_PCT] - 30.0) < 1e-6


def test_aggregator_kernel_table_raw_names():
    """kernel_table 出未聚合的原始 mangled 名 + 次数/占比,按占比降序。"""
    agg = WindowAggregator(KernelClassifier())
    agg.add([
        _kernel("flash_fwd_kernel", 40.0),
        _kernel("flash_fwd_kernel", 40.0),                 # 同名累加
        _kernel("cutlass_80_tensorop_s16816gemm", 30.0),
        _kernel("rms_norm_kernel", 10.0, graph_id=5),      # in graph
    ])
    table = agg.kernel_table(limit=10)
    by_name = {r["name"]: r for r in table}
    assert by_name["flash_fwd_kernel"]["count"] == 2
    assert by_name["flash_fwd_kernel"]["cls"] == "attention"
    assert abs(by_name["flash_fwd_kernel"]["mean_us"] - 40.0) < 1e-6
    # 总 kernel = 120us;flash = 80us → 66.67%
    assert abs(by_name["flash_fwd_kernel"]["pct"] - (80.0 / 120.0 * 100)) < 1e-6
    assert by_name["rms_norm_kernel"]["in_graph_pct"] == 100.0
    # 降序:flash 占比最大排第一
    assert table[0]["name"] == "flash_fwd_kernel"


def test_aggregator_kernel_table_limit():
    agg = WindowAggregator(KernelClassifier())
    agg.add([_kernel(f"kernel_{i}", float(i + 1)) for i in range(30)])
    assert len(agg.kernel_table(limit=5)) == 5


def test_aggregator_empty_window_is_safe():
    agg = WindowAggregator(KernelClassifier())
    out = agg.snapshot_and_reset(wall_ns=int(0.1 * 1e9))
    assert out[M.KERNEL_SHARE_ATTENTION_PCT] == 0.0
    assert out[M.KERNEL_GPU_BUSY_PCT] == 0.0
    assert out[M.KERNEL_MEAN_DUR_US] == 0.0


def test_aggregator_reset_between_windows():
    agg = WindowAggregator(KernelClassifier())
    agg.add([_kernel("flash_fwd_kernel", 40.0)])
    agg.snapshot_and_reset(wall_ns=int(0.1 * 1e9))
    # 第二窗只有 gemm,attention 应清零
    agg.add([_kernel("gemm_kernel", 40.0)])
    out = agg.snapshot_and_reset(wall_ns=int(0.1 * 1e9))
    assert out[M.KERNEL_SHARE_ATTENTION_PCT] == 0.0
    assert out[M.KERNEL_SHARE_GEMM_PCT] == 100.0


def test_aggregator_skips_negative_duration():
    agg = WindowAggregator(KernelClassifier())
    agg.add([KernelEvent("kernel", "flash_fwd_kernel", 100, 50)])  # end < start
    out = agg.snapshot_and_reset(wall_ns=int(0.1 * 1e9))
    assert out[M.KERNEL_SHARE_ATTENTION_PCT] == 0.0


# === CuptiKernelCollector ===

def test_collector_disabled_when_source_unavailable():
    sink = _CollectingSink(flush_interval_s=10.0)
    src = FakeActivitySource(available=False)
    coll = CuptiKernelCollector(sink, source=src)
    try:
        coll.start()  # no-op
        assert not coll.enabled
    finally:
        coll.stop()
        sink.close()
    assert sink.flushed_metrics == []


def test_collector_rolls_up_on_interval():
    sink = _CollectingSink(flush_interval_s=10.0)
    src = FakeActivitySource()
    clock = _FakeClock()
    coll = CuptiKernelCollector(sink, source=src, rollup_interval_s=1.0, clock=clock)
    try:
        coll.start()
        assert coll.enabled
        # 第一批:设定 last_rollup 基线,不 push
        src.emit([_kernel("flash_fwd_kernel", 40.0)])
        clock.advance_s(1.0)
        # 第二批:已过 interval → roll-up
        src.emit([_kernel("flash_fwd_kernel", 60.0)])
    finally:
        coll.stop()
        sink.close()

    by_name = {m.name: m.value for m in sink.flushed_metrics}
    assert M.KERNEL_SHARE_ATTENTION_PCT in by_name
    assert by_name[M.KERNEL_SHARE_ATTENTION_PCT] == 100.0
    # 自我观测指标也应 push 出来
    assert M.PPING_LANG_CUPTI_CB_MS in by_name
    assert M.PPING_LANG_CUPTI_DROPPED_TOTAL in by_name


def test_collector_final_rollup_on_stop():
    sink = _CollectingSink(flush_interval_s=10.0)
    src = FakeActivitySource()
    clock = _FakeClock()
    coll = CuptiKernelCollector(sink, source=src, rollup_interval_s=100.0, clock=clock)
    coll.start()
    src.emit([_kernel("flash_fwd_kernel", 40.0)])  # 设基线
    clock.advance_s(0.3)
    src.emit([_kernel("gemm_kernel", 60.0)])       # 未到 interval,不 push
    assert all(m.name != M.KERNEL_SHARE_GEMM_PCT for m in sink.flushed_metrics)
    clock.advance_s(0.2)
    coll.stop()  # 收尾应把残留窗 flush 出去
    sink.close()
    names = {m.name for m in sink.flushed_metrics}
    assert M.KERNEL_SHARE_ATTENTION_PCT in names  # 残留数据出来了


def test_collector_propagates_engine_index():
    sink = _CollectingSink(flush_interval_s=10.0)
    src = FakeActivitySource()
    clock = _FakeClock()
    coll = CuptiKernelCollector(sink, engine_index=3, source=src, rollup_interval_s=1.0, clock=clock)
    coll.start()
    src.emit([_kernel("flash_fwd_kernel", 40.0)])
    clock.advance_s(1.0)
    src.emit([_kernel("flash_fwd_kernel", 40.0)])
    coll.stop()
    sink.close()
    assert all(m.engine_idx == 3 for m in sink.flushed_metrics)


def test_collector_swallows_callback_exceptions():
    """聚合层异常绝不传播到采集源 / vLLM。"""
    sink = _CollectingSink(flush_interval_s=10.0)
    src = FakeActivitySource()

    class _BoomClassifier(KernelClassifier):
        def classify(self, name):
            raise RuntimeError("simulated classify failure")

    coll = CuptiKernelCollector(sink, source=src, classifier=_BoomClassifier())
    coll.start()
    # 不应抛
    src.emit([_kernel("flash_fwd_kernel", 40.0)])
    coll.stop()
    sink.close()


def test_collector_skips_empty_window():
    """窗内无 GPU 活动时不应 push 全 0(避免 stop 收尾空窗覆盖真值 / idle 噪声)。"""
    sink = _CollectingSink(flush_interval_s=10.0)
    src = FakeActivitySource()
    clock = _FakeClock()
    coll = CuptiKernelCollector(sink, source=src, rollup_interval_s=1.0, clock=clock)
    coll.start()
    # 一窗有数据 → push 真值
    src.emit([_kernel("flash_fwd_kernel", 40.0)])  # baseline
    clock.advance_s(1.0)
    src.emit([_kernel("flash_fwd_kernel", 60.0)])  # rollup with data
    # 下一窗为空 → 不应再 push
    clock.advance_s(1.0)
    src.emit([])  # empty window crosses interval → rollup, but no data → skip
    coll.stop()
    sink.close()

    by_name = {m.name: m.value for m in sink.flushed_metrics}
    # attention 真值留住,没被空窗 0 覆盖
    assert by_name[M.KERNEL_SHARE_ATTENTION_PCT] == 100.0


def test_flamegraph_aggregator_builds_tree():
    """火焰图:(Python 栈 + kernel) → 时间 累成前缀树。"""
    fg = FlamegraphAggregator()
    fg.add([
        KernelEvent("kernel", "flash_fwd", 0, 40000, 0, ("step", "forward", "attn")),
        KernelEvent("kernel", "flash_fwd", 0, 60000, 0, ("step", "forward", "attn")),
        KernelEvent("kernel", "gemm_k", 0, 30000, 0, ("step", "forward", "mlp")),
    ])
    tree = fg.snapshot_and_reset(min_share=0.0)
    assert tree["name"] == "root" and tree["value"] == 130000
    step = tree["children"][0]
    assert step["name"] == "step" and step["kind"] == "python"
    fwd = step["children"][0]
    assert fwd["name"] == "forward"
    assert {c["name"] for c in fwd["children"]} == {"attn", "mlp"}
    attn = next(c for c in fwd["children"] if c["name"] == "attn")
    leaf = attn["children"][0]
    assert leaf["name"] == "flash_fwd" and leaf["kind"] == "kernel" and leaf["value"] == 100000


def test_flamegraph_ignores_events_without_stack():
    fg = FlamegraphAggregator()
    fg.add([KernelEvent("kernel", "k", 0, 40000, 0, None)])  # no stack
    assert not fg.has_data
    assert fg.snapshot_and_reset() is None


def test_flamegraph_prunes_small_branches():
    fg = FlamegraphAggregator()
    fg.add([KernelEvent("kernel", "big", 0, 100000, 0, ("a",))])
    fg.add([KernelEvent("kernel", "tiny", 0, 100, 0, ("b",))])  # 0.1% < min_share
    tree = fg.snapshot_and_reset(min_share=0.01)
    names = {c["name"] for c in tree["children"]}
    assert "a" in names and "b" not in names


def test_collector_exposes_flamegraph():
    sink = _CollectingSink(flush_interval_s=10.0)
    src = FakeActivitySource()
    clock = _FakeClock()
    coll = CuptiKernelCollector(sink, source=src, rollup_interval_s=1.0, clock=clock)
    coll.start()
    assert coll.flamegraph() is None  # 还没数据
    src.emit([KernelEvent("kernel", "flash_fwd", 0, 40000, 0, ("step", "forward"))])
    clock.advance_s(1.0)
    src.emit([KernelEvent("kernel", "flash_fwd", 0, 40000, 0, ("step", "forward"))])
    fg = coll.flamegraph()
    assert fg is not None and fg["name"] == "root"
    coll.stop()
    sink.close()


def test_cupti_source_graph_kernels_share_stack():
    """回归:一次 cuGraphLaunch 下的多个 kernel 共享 correlation_id,应都拿到栈。
    修复前用 .pop → 除第一个外全丢栈 → graph kernel(decode 大头)从火焰图消失。"""
    from pping_lang.collector.cupti import CuptiPythonSource

    class _FakeCupti:
        class ActivityKind:
            CONCURRENT_KERNEL = 10
            KERNEL = 3
            MEMCPY = 1
            SYNCHRONIZATION = 9

    src = CuptiPythonSource(capture_stacks=True)
    src._cupti = _FakeCupti()           # 注入假模块(Windows 无真 cupti)
    src._pending[42] = ("step", "replay")
    got: list = []
    src._cb = lambda evs: got.extend(evs)

    def _act(name, cid, dur):
        a = type("A", (), {})()
        a.kind, a.name, a.start, a.end = 10, name, 0, dur
        a.correlation_id, a.graph_id = cid, 7
        return a

    # 两个 kernel 共享 correlation_id 42(模拟 graph 内多 kernel)
    src._buffer_completed([_act("k1", 42, 100), _act("k2", 42, 200)])
    assert got[0].stack == ("step", "replay")
    assert got[1].stack == ("step", "replay")  # 修复前这里是 None


def test_timeline_buffer_normalizes_and_groups_streams():
    """时间线:保留原始 start/end,归一化到 t0,按 stream 分组。"""
    tb = TimelineBuffer()
    clf = KernelClassifier()
    tb.add([
        KernelEvent("kernel", "flash_fwd", 100, 200, 0, None, 7),
        KernelEvent("kernel", "gemm_k", 200, 500, 0, None, 7),
        KernelEvent("memcpy", "HtoD", 50, 60, 0, None, 3),
    ], clf)
    snap = tb.snapshot(800)
    assert snap["count"] == 3
    assert snap["span_ns"] == 450          # t0=50, max end=500
    assert set(snap["streams"]) == {3, 7}
    flash = next(e for e in snap["events"] if e["name"] == "flash_fwd")
    assert flash["start"] == 50 and flash["dur"] == 100  # 归一化:100-50
    assert flash["cls"] == "attention" and flash["stream"] == 7


def test_timeline_buffer_empty_is_none():
    assert TimelineBuffer().snapshot() is None


def test_timeline_chrome_trace_format():
    """Chrome Trace 导出:ph=M 命名 track + ph=X 事件,ts/dur 微秒,归一化到 t0。"""
    tb = TimelineBuffer()
    tb.add([KernelEvent("kernel", "flash_fwd", 1000, 1100, 0, None, 7)], KernelClassifier())
    tr = tb.chrome_trace()
    assert tr["displayTimeUnit"] == "ms"
    evs = tr["traceEvents"]
    assert any(e["ph"] == "M" and e["name"] == "thread_name" and e["args"]["name"] == "stream 7" for e in evs)
    x = next(e for e in evs if e["ph"] == "X")
    assert x["name"] == "flash_fwd" and x["tid"] == 7
    assert x["ts"] == 0.0 and x["dur"] == 0.1   # 1000ns→t0, 100ns=0.1µs
    assert x["cat"] == "attention"


def test_timeline_buffer_bounded():
    tb = TimelineBuffer(maxlen=5)
    clf = KernelClassifier()
    tb.add([KernelEvent("kernel", f"k{i}", i, i + 1, 0, None, 0) for i in range(20)], clf)
    assert tb.snapshot(800)["count"] == 5  # 只留最近 5 条


def test_collector_exposes_timeline():
    sink = _CollectingSink(flush_interval_s=10.0)
    src = FakeActivitySource()
    coll = CuptiKernelCollector(sink, source=src, rollup_interval_s=1.0, clock=_FakeClock())
    coll.start()
    assert coll.timeline() is None
    src.emit([KernelEvent("kernel", "flash_fwd", 100, 200, 0, None, 7)])  # 立刻入环,不等 rollup
    snap = coll.timeline()
    assert snap is not None and snap["count"] == 1
    coll.stop()
    sink.close()


def test_collector_exposes_top_kernels():
    """collector.top_kernels() 在 rollup 时捕获本窗原始 kernel 明细。"""
    sink = _CollectingSink(flush_interval_s=10.0)
    src = FakeActivitySource()
    clock = _FakeClock()
    coll = CuptiKernelCollector(sink, source=src, rollup_interval_s=1.0, clock=clock)
    coll.start()
    assert coll.top_kernels() == []  # 还没 rollup
    src.emit([_kernel("flash_fwd_kernel", 40.0), _kernel("cutlass_gemm", 60.0)])  # baseline
    clock.advance_s(1.0)
    src.emit([_kernel("flash_fwd_kernel", 40.0)])  # 触发 rollup → 捕获明细
    names = [k["name"] for k in coll.top_kernels()]
    assert "flash_fwd_kernel" in names
    assert "cutlass_gemm" in names
    # 快照时刻 + 窗宽被记录(前端据此显示"X 秒前 / 最近 Ys 窗口")
    assert coll.last_snapshot_ts is not None
    assert coll.last_window_ns > 0
    coll.stop()
    sink.close()


# === StallClassifier(阶段 2 PC Sampling)===

def _reason(short: str) -> str:
    """构造 Ada/sm_89 PerfWorks 全名,如 long_scoreboard → smsp__...long_scoreboard。"""
    return f"smsp__pcsamp_warps_issue_stalled_{short}"


def test_stall_classifier_maps_perfworks_reasons():
    clf = StallClassifier()
    assert clf.classify(_reason("long_scoreboard")) == "memory_dependency"
    assert clf.classify(_reason("short_scoreboard")) == "shared_dependency"
    assert clf.classify(_reason("mio_throttle")) == "memory_throttle"
    assert clf.classify(_reason("lg_throttle")) == "memory_throttle"
    assert clf.classify(_reason("imc_miss")) == "memory_throttle"
    assert clf.classify(_reason("math_pipe_throttle")) == "math_pipe"
    assert clf.classify(_reason("wait")) == "exec_dependency"
    assert clf.classify(_reason("barrier")) == "sync"
    assert clf.classify(_reason("membar")) == "sync"
    assert clf.classify(_reason("no_instructions")) == "fetch_control"
    assert clf.classify(_reason("branch_resolving")) == "fetch_control"
    assert clf.classify(_reason("dispatch_stall")) == "dispatch"


def test_stall_classifier_not_selected_before_selected():
    """顺序敏感:not_selected 含 selected 子串,必须先命中 scheduler_slack。"""
    clf = StallClassifier()
    assert clf.classify(_reason("not_selected")) == "scheduler_slack"
    assert clf.classify(_reason("selected")) == "issued"  # 已发射,非 stall


def test_stall_classifier_not_issued_variants_same_class():
    """`_not_issued` 变体归到与基 reason 同类。"""
    clf = StallClassifier()
    assert clf.classify(_reason("math_pipe_throttle_not_issued")) == "math_pipe"
    assert clf.classify(_reason("long_scoreboard_not_issued")) == "memory_dependency"


def test_stall_classifier_unknown_and_memoize():
    clf = StallClassifier()
    assert clf.classify(_reason("brand_new_reason_xyz")) == "other"
    assert clf.cache_size == 1
    clf.classify(_reason("brand_new_reason_xyz"))
    assert clf.cache_size == 1


# === StallAggregator ===

def _s(kernel: str, short: str, n: int) -> StallSample:
    return StallSample(kernel, _reason(short), n)


def test_stall_aggregator_categories_sum_to_100_excluding_issued():
    agg = StallAggregator()
    agg.add([
        _s("flash_fwd", "long_scoreboard", 50),   # memory_dependency
        _s("flash_fwd", "math_pipe_throttle", 30),  # math_pipe
        _s("flash_fwd", "wait", 20),                # exec_dependency
        _s("flash_fwd", "selected", 100),           # issued — 不进 stall 分母
    ])
    out = agg.snapshot_and_reset()
    assert abs(out[M.KERNEL_STALL_MEMORY_DEP_PCT] - 50.0) < 1e-6   # 50/100 stall
    assert abs(out[M.KERNEL_STALL_MATH_PIPE_PCT] - 30.0) < 1e-6
    assert abs(out[M.KERNEL_STALL_EXEC_DEP_PCT] - 20.0) < 1e-6
    total = sum(out[STALL_METRIC] for STALL_METRIC in (
        M.KERNEL_STALL_MEMORY_DEP_PCT, M.KERNEL_STALL_SHARED_DEP_PCT,
        M.KERNEL_STALL_MEMORY_THROTTLE_PCT, M.KERNEL_STALL_MATH_PIPE_PCT,
        M.KERNEL_STALL_EXEC_DEP_PCT, M.KERNEL_STALL_SYNC_PCT,
        M.KERNEL_STALL_FETCH_CONTROL_PCT, M.KERNEL_STALL_DISPATCH_PCT,
        M.KERNEL_STALL_SCHEDULER_SLACK_PCT, M.KERNEL_STALL_OTHER_PCT,
    ))
    assert abs(total - 100.0) < 1e-6
    # issued 占总样本 100/200 = 50%
    assert abs(out[M.KERNEL_STALL_ISSUED_PCT] - 50.0) < 1e-6
    assert out[M.KERNEL_STALL_SAMPLE_TOTAL] == 200.0


def test_stall_aggregator_empty_is_safe():
    out = StallAggregator().snapshot_and_reset()
    assert out[M.KERNEL_STALL_MEMORY_DEP_PCT] == 0.0
    assert out[M.KERNEL_STALL_ISSUED_PCT] == 0.0
    assert out[M.KERNEL_STALL_SAMPLE_TOTAL] == 0.0


def test_stall_aggregator_skips_nonpositive():
    agg = StallAggregator()
    agg.add([_s("k", "long_scoreboard", 0), _s("k", "wait", -5)])
    assert not agg.has_data


def test_stall_aggregator_kernel_table_dominant_excludes_slack_and_issued():
    """主导 stall 类排除 issued 与 scheduler_slack(高 not_selected 非瓶颈)。"""
    agg = StallAggregator()
    agg.add([
        _s("attn", "not_selected", 70),       # scheduler_slack(占比最大但不算主导)
        _s("attn", "long_scoreboard", 25),    # memory_dependency → 应是主导
        _s("attn", "selected", 200),          # issued
        _s("gemm", "math_pipe_throttle", 40),
    ])
    table = agg.kernel_stall_table()
    by = {r["kernel"]: r for r in table}
    assert by["attn"]["dominant_stall"] == "memory_dependency"
    # attn 总样本最多(70+25+200=295)→ 排第一
    assert table[0]["kernel"] == "attn"
    assert by["gemm"]["dominant_stall"] == "math_pipe"


def test_collector_reports_dropped_records():
    sink = _CollectingSink(flush_interval_s=10.0)
    src = FakeActivitySource()
    src.set_dropped(42)
    clock = _FakeClock()
    coll = CuptiKernelCollector(sink, source=src, rollup_interval_s=1.0, clock=clock)
    coll.start()
    src.emit([_kernel("flash_fwd_kernel", 40.0)])
    clock.advance_s(1.0)
    src.emit([_kernel("flash_fwd_kernel", 40.0)])
    coll.stop()
    sink.close()
    by_name = {m.name: m.value for m in sink.flushed_metrics}
    assert by_name[M.PPING_LANG_CUPTI_DROPPED_TOTAL] == 42.0
