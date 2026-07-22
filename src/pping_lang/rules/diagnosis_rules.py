"""诊断规则:只围绕 4 个瓶颈(A 双低 / B 带宽瓶颈 / C 算力瓶颈 / D 容量瓶颈)。

每个瓶颈有**多条独立检测手段**(`Detector`),分布在不同测量层(L1 roofline 实测 / L2 内核 stall /
L3 调度态),**任一手段命中即认该瓶颈**(OR)—— 多手段 = 三角互证(几条同时中 = 高置信)+ 优雅降级
(某层探针缺失仍能诊断)。一条 `Detector` = 一组 `FactCheck` 的 AND(全成立该手段才算命中)。

铁律:
1. 规则名/手段条件只描述客观事实;根因(hypothesis)+ 处方(suggestion)是署名推断。
2. 阈值引 `DiagnosisConfig` 字段(threshold_ref),不内嵌魔数;固定值(如 preempt>0)用 threshold。
3. 诊断不碰 SLA:是否达标是首页的事。这里只判"卡在哪类硬件墙"。

rule_id 直接就是瓶颈字母(A/B/C/D)。
"""
from __future__ import annotations

from dataclasses import dataclass, fields

from pping_lang.metrics_catalog import ALLOWED_METRICS
from pping_lang.rules.diagnosis_config import DiagnosisConfig
from pping_lang.rules.schema import Op, Severity

FactAgg = ("avg", "p50", "p95", "p99", "max", "min", "sum", "count")
RuleKind = ("fact",)
# 测量层:L1 roofline 实测(perf_stats)/ L2 内核 stall(CUPTI)/ L3 调度态(永远在)/ L4 时延 / L5 NVML
LAYERS = ("L1", "L2", "L3", "L4", "L5")

_CONFIG_FIELDS = frozenset(f.name for f in fields(DiagnosisConfig))


@dataclass(frozen=True)
class FactCheck:
    """一个客观事实判断:某 metric 的窗口聚合 与 阈值 比较。"""

    metric: str
    op: Op
    threshold_ref: str | None = None   # DiagnosisConfig 字段名(运行时取值)
    threshold: float | None = None     # 固定阈值(如 preempt>0);与 ref 二选一
    window_seconds: int = 60
    aggregation: str = "avg"


@dataclass(frozen=True)
class Detector:
    """一条独立检测手段:一组 check 的 AND;命名 + 标注测量层。全成立 → 该手段命中。"""

    key: str            # 短标识(同一瓶颈内唯一)
    name: str           # 人话:这条手段叫什么
    layer: str          # 测量层 L1..L5(看独立性/可用性)
    checks: tuple[FactCheck, ...]


@dataclass(frozen=True)
class FactRule:
    """一个瓶颈。多条 detector,任一命中即触发(OR);名字=事实,根因/处方=署名推断。"""

    id: str                                # = 瓶颈字母 A/B/C/D
    name: str                              # 纯事实
    kind: str = "fact"
    detectors: tuple[Detector, ...] = ()
    severity: Severity = "info"
    hypothesis: str = ""                   # 根因推断(署名)
    suggestion: str = ""                   # 处方


# ── A 双低:有在途请求,但算力、带宽两上限都未逼近。两条独立手段(都带空载守卫)──
_A = FactRule(
    id="A", name="双低(算力、带宽均有余量)", severity="info",
    detectors=(
        Detector("roofline", "算力/带宽双低(MFU 低 + HBM 控制器空闲)", "L1", (
            FactCheck("vllm.scheduler.running_reqs", ">", "min_running_reqs", None, 30, "avg"),
            FactCheck("vllm.perf.mfu_ratio", "<", "mfu_low_ratio", None, 60, "avg"),
            FactCheck("gpu.mem_util_pct", "<", "mbu_low_pct", None, 30, "avg"),
        )),
        Detector("kernel_slack", "内核 scheduler_slack(warp 就绪未发射)", "L2", (
            FactCheck("vllm.scheduler.running_reqs", ">", "min_running_reqs", None, 30, "avg"),
            FactCheck("kernel.stall.scheduler_slack_pct", ">", "stall_scheduler_slack_pct", None, 60, "avg"),
        )),
    ),
    hypothesis="存在在途请求,但 GPU 未饱和 —— 算力与带宽均有余量,瓶颈不在硬件"
               "(可能为批处理规模不足 / kernel launch 开销 / 小算子未融合)。空载守卫:无在途请求时不触发。",
    suggestion="提高 max-num-seqs(并发未填满)/ 调整 chunked-prefill 的 partial-prefills。"
               "注:Continuous Batching、CUDA Graph、chunked-prefill 在 0.21 默认启用,"
               "请先确认未被 enforce-eager 等关闭,避免重复启用(无效操作)。",
)
# ── B 带宽瓶颈:三条独立手段(L1 实测 + 两条 L2 内核)──
_B = FactRule(
    id="B", name="带宽瓶颈(逼近显存带宽上限)", severity="warning",
    detectors=(
        Detector("hbm_busy", "HBM 控制器占用(NVML mem-util)", "L5", (
            FactCheck("gpu.mem_util_pct", ">", "mbu_high_pct", None, 30, "avg"),
        )),
        Detector("kernel_throttle", "内核访存管线 throttle(LSU 饱和)", "L2", (
            FactCheck("kernel.stall.memory_throttle_pct", ">", "stall_memory_throttle_pct", None, 60, "avg"),
        )),
        Detector("kernel_memdep", "内核访存延迟(long_scoreboard 等待访存)", "L2", (
            FactCheck("kernel.stall.memory_dependency_pct", ">", "stall_memory_dep_pct", None, 60, "avg"),
        )),
    ),
    hypothesis="访存受限:decode 每步重新读取权重与 KV,带宽为上限。"
               "NVML HBM 控制器占用 / 内核 memory_throttle(访存管线饱和)/ memory_dependency(等待访存)交叉印证。"
               "(注:perf 实测 MBU 在小模型上因 L2 复用 >1,故不作为阈值,仅用于 roofline 定位。)",
    suggestion="投机解码(关注接受率,避免转为计算受限)/ KV 量化(FP8)/ 升级至更高带宽 GPU。",
)
# ── C 算力瓶颈:两条独立手段(L1 实测 + L2 内核)──
_C = FactRule(
    id="C", name="算力瓶颈(算力饱和)", severity="warning",
    detectors=(
        Detector("measured_mfu", "实测 MFU 逼近上限", "L1", (
            FactCheck("vllm.perf.mfu_ratio", ">", "mfu_high_ratio", None, 60, "avg"),
        )),
        Detector("kernel_mathpipe", "内核算力管线饱和(FMA/ALU/Tensor)", "L2", (
            FactCheck("kernel.stall.math_pipe_pct", ">", "stall_math_pipe_pct", None, 60, "avg"),
        )),
    ),
    hypothesis="计算受限:FLOPs 饱和,算力为上限(长 prompt prefill 的固有特征)。"
               "实测 MFU 逼近上限 / 内核 math_pipe(计算管线饱和)交叉印证。",
    suggestion="更换更快的 attention backend(0.21 按硬件自动选择,可用 --attention-backend 覆盖)"
               "/ 权重量化(FP8/FP4)/ 升级算力更强的 GPU。",
)
# ── D 容量瓶颈:两条独立手段(都 L3 调度态,永远在)──
_D = FactRule(
    id="D", name="容量瓶颈(KV 耗尽并触发抢占)", severity="critical",
    detectors=(
        Detector("kv_pressure", "KV 池接近耗尽", "L3", (
            FactCheck("vllm.scheduler.kv_cache_usage_ratio", ">=", "kv_pressure_ratio", None, 10, "avg"),
        )),
        Detector("preemption", "已发生抢占", "L3", (
            FactCheck("vllm.iter.preempted_reqs", ">", None, 0.0, 10, "sum"),
        )),
    ),
    hypothesis="显存无法容纳 KV → 并发受限 → 触发抢占。V1 抢占为纯重算(丢弃 KV、从头 re-prefill),"
               "一旦发生,decode 吞吐急剧下降。",
    suggestion="KV 量化(FP8)/ 降低 max-model-len / KV offload / 降低 max-num-seqs。",
)

DIAGNOSIS_RULES: tuple[FactRule, ...] = (_A, _B, _C, _D)


def validate_rules(rules: tuple[FactRule, ...] = DIAGNOSIS_RULES) -> None:
    """加载期强校验:挡住手写错误。"""
    ids = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate rule ids: {ids}")
    for r in rules:
        if r.kind not in RuleKind:
            raise ValueError(f"{r.id}: bad kind {r.kind!r}")
        if not r.detectors:
            raise ValueError(f"{r.id}: 必须有至少一条 detector")
        keys = [d.key for d in r.detectors]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{r.id}: detector key 重复 {keys}")
        for det in r.detectors:
            if det.layer not in LAYERS:
                raise ValueError(f"{r.id}/{det.key}: bad layer {det.layer!r}")
            if not det.checks:
                raise ValueError(f"{r.id}/{det.key}: 必须有至少一个 check")
            for c in det.checks:
                if c.metric not in ALLOWED_METRICS:
                    raise ValueError(f"{r.id}/{det.key}: unknown metric {c.metric!r}")
                if (c.threshold_ref is None) == (c.threshold is None):
                    raise ValueError(f"{r.id}/{det.key}: check 须恰好给 threshold_ref 或 threshold 之一")
                if c.threshold_ref is not None and c.threshold_ref not in _CONFIG_FIELDS:
                    raise ValueError(f"{r.id}/{det.key}: threshold_ref {c.threshold_ref!r} 不是 DiagnosisConfig 字段")
                if c.aggregation not in FactAgg:
                    raise ValueError(f"{r.id}/{det.key}: bad aggregation {c.aggregation!r}")
                if c.window_seconds <= 0:
                    raise ValueError(f"{r.id}/{det.key}: window_seconds must be > 0")
