"""诊断规则:每条规则 = 客观事实判断 + 触发守卫(precondition)+ 署名根因/处方。

铁律(本文件强制):
1. **规则名/条件只描述客观事实**(测到了什么),绝不写根因结论。
2. **根因(`hypothesis`)+ 处方(`suggestion`)是推断**,单独字段、署名,不当判决。
3. **阈值引 `DiagnosisConfig` 字段(`threshold_ref`),不内嵌魔数**;固定值(如 preempt>0)用 `threshold`。
4. **`precondition` = 前置规则 id(OR:任一触发即满足守卫)** —— 让规则"知道自己在什么前提下才成立",
   仅此而已,不是什么结构。

注:`regime-classify`(算术强度 vs 脊点 → 访存/计算受限)是分类器规则,引擎特殊计算、无 `checks`;
其它规则用 `requires_regime` 引用它的结果。
`D4b 权重占显存` / `D4c 并发撑爆 KV` / `D2c 通信受限` 机制不同(静态/派生/多卡),暂不收,见尾部 DEFERRED。
"""
from __future__ import annotations

from dataclasses import dataclass, fields

from pping_lang.metrics_catalog import ALLOWED_METRICS
from pping_lang.rules.diagnosis_config import DiagnosisConfig
from pping_lang.rules.schema import Claim, Op

# FactCheck 允许的聚合:含派生比值 p99_over_p50(尾部发散,引擎算)
FactAgg = ("avg", "p50", "p95", "p99", "max", "min", "sum", "count", "p99_over_p50")
Regime = ("memory_bound", "compute_bound")
RuleKind = ("classifier", "symptom", "fact")

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
class FactRule:
    """一条诊断规则。名字=事实;根因/处方=署名推断。"""

    id: str
    name: str                              # 纯事实,如 "等待队列偏长"
    kind: str = "fact"                     # classifier | symptom | fact
    checks: tuple[FactCheck, ...] = ()     # 多个 = 复合
    match: str = "all"                     # all | any(S4 用 any)
    precondition: tuple[str, ...] = ()     # 前置规则 id(OR:任一触发即满足守卫)
    requires_regime: str | None = None     # 还需处于某 regime
    claim: Claim = "derived"
    hypothesis: str = ""                   # 根因推断(署名,非判决)
    suggestion: str = ""                   # 处方


# ── 分类器规则(regime,其它规则用 requires_regime 引用)──────────────────
_REGIME = FactRule(
    id="regime-classify",
    name="算术强度 vs 脊点(Roofline 定位)",
    kind="classifier",
    claim="derived",
    hypothesis="AI < 脊点 → 访存受限;否则计算受限(定义性派生)。memory-bound 且 SM util 高时:"
               "util 虚高是物理极限,非优化空间。",
)

# ── 入口症状 ───────────────────────────────────────────────────────────
_S1 = FactRule(
    id="S1", name="TTFT p99 超 SLA", kind="symptom",
    checks=(FactCheck("vllm.req.ttft_ms", ">", "sla_ttft_p99_ms", None, 60, "p99"),),
    hypothesis="首 Token 慢(相对该业务 SLA)。",
)
_S2 = FactRule(
    id="S2", name="TPOT p99 超 SLA", kind="symptom",
    checks=(FactCheck("vllm.req.tpot_ms", ">", "sla_tpot_p99_ms", None, 60, "p99"),),
    hypothesis="出字慢(相对该业务 SLA)。",
)
_S4 = FactRule(
    id="S4", name="KV 用量高或发生抢占", kind="symptom", match="any",
    checks=(
        FactCheck("vllm.scheduler.kv_cache_usage_ratio", ">=", "kv_pressure_ratio", None, 10, "avg"),
        FactCheck("vllm.iter.preempted_reqs", ">", None, 0.0, 10, "sum"),
    ),
    hypothesis="显存吃紧 / 已在抢占。",
)
_S5 = FactRule(
    id="S5", name="TTFT p99/p50 偏大", kind="symptom",
    checks=(FactCheck("vllm.req.ttft_ms", ">", "tail_ratio", None, 60, "p99_over_p50"),),
    hypothesis="首 Token 时延尾部发散(多数快、少数奇慢)。",
)

# ── 判别规则(前置 = 父症状)────────────────────────────────────────────
_D1a = FactRule(
    id="D1a", name="平均 prompt 偏长", precondition=("S1", "S5"),
    checks=(FactCheck("vllm.req.prompt_tokens", ">", "long_prompt_tokens", None, 60, "avg"),),
    hypothesis="长输入 → prefill 重 / 长请求占 token budget,即便分块预填充也会拖尾。",
    suggestion="PD 分离 / 优先级调度 / 调 max_num_batched_tokens / 限 max_output_tokens。",
)
_D1b = FactRule(
    id="D1b", name="等待队列偏长", precondition=("S1", "S5"),
    checks=(FactCheck("vllm.scheduler.waiting_reqs", ">", "waiting_reqs", None, 30, "avg"),),
    hypothesis="请求在排队。",
    suggestion="扩 prefill 容量 / 降 max_num_seqs / 检查上游限流。",
)
_D1c = FactRule(
    id="D1c", name="MFU 偏低", precondition=("S1",), requires_regime="compute_bound",
    checks=(FactCheck("vllm.perf.mfu_ratio", "<", "mfu_low_ratio", None, 60, "avg"),),
    hypothesis="处于计算受限区但算力没打满(prefill 算力不足)。",
    suggestion="升级算力卡 / FP8→FP4 / 启用 FlashAttention-3。",
)
_D2a = FactRule(
    id="D2a", name="MBU 接近峰值", precondition=("S2",),
    checks=(FactCheck("gpu.mem_util_pct", ">", "mbu_high_pct", None, 30, "avg"),),
    hypothesis="贴近带宽屋顶(访存受限)。",
    suggestion="投机解码 / KV 量化(FP8) / 换更高带宽 GPU。",
)
_D2b = FactRule(
    id="D2b", name="并发(running)偏小", precondition=("S2",),
    checks=(FactCheck("vllm.scheduler.running_reqs", "<=", "batch_small_reqs", None, 30, "avg"),),
    hypothesis="并发太低,没摊薄权重搬运。",
    suggestion="提高客户端并发 / 调 max_num_seqs。",
)
_D3a = FactRule(
    id="D3a", name="MFU、MBU 双低",
    checks=(
        FactCheck("vllm.perf.mfu_ratio", "<", "mfu_low_ratio", None, 60, "avg"),
        FactCheck("gpu.mem_util_pct", "<", "mbu_low_pct", None, 30, "avg"),
    ),
    hypothesis="两个屋顶都没贴近 —— 算力、带宽都有富余,瓶颈不在硬件(可能是 batch 没拼起来 / "
               "launch 开销 / 小算子未融合)。",
    suggestion="检查 batch 是否拼起来 / Continuous Batching / CUDA Graph / 算子融合。",
)
_D3c = FactRule(
    id="D3c", name="前缀缓存命中率低",
    checks=(FactCheck("vllm.scheduler.prefix_cache_hit_ratio", "<", "prefix_hit_low", None, 60, "avg"),),
    hypothesis="前缀缓存命中低(若 workload 有公共前缀,则有复用空间;否则正常)。",
    suggestion="检查 prompt 模板公共前缀 / 开 enable_prefix_caching / RadixAttention。",
)
_D4a = FactRule(
    id="D4a", name="KV 用量高且发生抢占", precondition=("S4",),
    checks=(
        FactCheck("vllm.scheduler.kv_cache_usage_ratio", ">=", "kv_pressure_ratio", None, 10, "avg"),
        FactCheck("vllm.iter.preempted_reqs", ">", None, 0.0, 10, "sum"),
    ),
    hypothesis="KV 池将满并已触发抢占。",
    suggestion="KV 量化(FP8) / 降 max_model_len / KV offload。",
)

DIAGNOSIS_RULES: tuple[FactRule, ...] = (
    _REGIME,
    _S1, _S2, _S4, _S5,
    _D1a, _D1b, _D1c, _D2a, _D2b, _D3a, _D3c, _D4a,
)
# DEFERRED(机制不同,本表暂不收):
#   D4b 权重占显存比例高 —— 静态:model 权重字节 / HBM,启动期算一次,非窗口指标。
#   D4c 并发×KV 撑爆     —— 派生:running × 单请求 KV / 池容量。
#   D2c TPOT·通信受限    —— comm kernel 占比,仅多卡有数(kernel.time_share.comm_pct)。


def validate_rules(rules: tuple[FactRule, ...] = DIAGNOSIS_RULES) -> None:
    """加载期强校验:挡住手写错误。"""
    ids = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate rule ids: {ids}")
    idset = set(ids)
    for r in rules:
        if r.kind not in RuleKind:
            raise ValueError(f"{r.id}: bad kind {r.kind!r}")
        if r.match not in ("all", "any"):
            raise ValueError(f"{r.id}: bad match {r.match!r}")
        if r.requires_regime is not None and r.requires_regime not in Regime:
            raise ValueError(f"{r.id}: bad requires_regime {r.requires_regime!r}")
        for p in r.precondition:
            if p not in idset:
                raise ValueError(f"{r.id}: precondition {p!r} not a known rule")
        if r.kind == "classifier":
            if r.checks:
                raise ValueError(f"{r.id}: classifier 不应有 checks(引擎特殊计算)")
            continue
        if not r.checks:
            raise ValueError(f"{r.id}: 非 classifier 必须有至少一个 check")
        for c in r.checks:
            if c.metric not in ALLOWED_METRICS:
                raise ValueError(f"{r.id}: unknown metric {c.metric!r}")
            if (c.threshold_ref is None) == (c.threshold is None):
                raise ValueError(f"{r.id}: check 须恰好给 threshold_ref 或 threshold 之一")
            if c.threshold_ref is not None and c.threshold_ref not in _CONFIG_FIELDS:
                raise ValueError(f"{r.id}: threshold_ref {c.threshold_ref!r} 不是 DiagnosisConfig 字段")
            if c.aggregation not in FactAgg:
                raise ValueError(f"{r.id}: bad aggregation {c.aggregation!r}")
            if c.window_seconds <= 0:
                raise ValueError(f"{r.id}: window_seconds must be > 0")
