"""动作空间 —— 把 vLLM 参数面变成「按诊断索引的结构空间」(§4)。

设计要点(§4.1–4.6):
- **全量面 introspect + 个位数有效动作**:vLLM 0.21 ~258 flag,大半是引擎已自调好的背景
  (§4.2 a 类),真·可调杠杆 ≈ 个位数(b 类)。本模块 curate 高杠杆旋钮 + 手工 4 标签,
  `introspect_defaults()` 在 vllm 可导入时读当前生效默认值(`default_0_21`),否则用静态值。
- **4 标签**(§4.4):`lever` / `helps`·`hurts`(同旋钮换 regime 从解药变毒药)/ `default_on`
  (分类一:默认已开,提议前跳过,§4.4)/ `output_impact`(T1=none / T2=equivalence / T3=correctness)。
- **诊断→动作 = 标签交集 + D 余量守卫 + 约束图可行性 + 跳过已到位**(§4.4 / §4.5)。

注:vllm flag 名用连字符(--max-num-seqs);config dict 的 key 用下划线(max_num_seqs)。
"""
from __future__ import annotations

from dataclasses import dataclass

# regime 词汇与诊断引擎一致:A 喂不饱 / B 带宽墙 / C 算力墙 / D 容量墙(§4.3)
REGIMES = ("A", "B", "C", "D")


@dataclass(frozen=True)
class Knob:
    key: str                        # config dict 键(下划线)
    flag: str                       # vllm flag(连字符)
    kind: str                       # "int" | "float" | "choice"
    lever: str                      # 提利用率/加预算/腾容量/减字节/减FLOPs/减开销/抬屋顶
    helps: tuple[str, ...]          # 利于哪些瓶颈(朝 helpful 方向走)
    hurts: tuple[str, ...]          # 加剧哪些瓶颈(该 regime 下别选)
    primary_slo: str                # "ttft" | "tpot" | "throughput"(排序提示,非硬筛)
    output_impact: str = "none"     # none(T1) / equivalence(T2) / correctness(T3)
    default: object = None          # default_0_21;introspect 当前生效值,否则静态
    default_on: bool = False        # 分类(一):默认已开,提议时跳过(除非诊断显式点名)
    lo: float = 0.0
    hi: float = 0.0
    choices: tuple = ()             # kind="choice" 的取值序(从弱到强,helpful 在尾)
    big_batch: bool = False         # 推大-batch 类 → 选前先查 D 余量(§4.4 守卫)
    needs: tuple[str, ...] = ()     # 约束图前置:这些 flag 必须为真才可行(§4.5)
    conflicts: tuple[str, ...] = ()  # 约束图冲突:与这些同开则不可行(§4.5)
    unsupported: bool = False       # 当前钉定的 vLLM 版本硬拒非默认值 → 永不提议(烧轮)


# === curated 高杠杆旋钮(§4.4 两张表)。default 为 0.21 静态值,introspect 可覆盖。===

# 分类(一)——默认已开,通常别动(§4.4):诊断 A 去"开"它们 = 开已开的开关,白烧一轮。
_DEFAULT_ON: tuple[Knob, ...] = (
    Knob("enable_chunked_prefill", "--enable-chunked-prefill", "choice", "减气泡",
         helps=("A",), hurts=(), primary_slo="ttft", default=True, default_on=True,
         choices=(False, True)),
    Knob("enable_prefix_caching", "--enable-prefix-caching", "choice", "省重算",
         helps=("C",), hurts=(), primary_slo="ttft", default=True, default_on=True,
         choices=(False, True)),
    Knob("async_scheduling", "--async-scheduling", "choice", "消调度气泡",
         helps=("A",), hurts=(), primary_slo="tpot", default=True, default_on=True,
         choices=(False, True), conflicts=("speculative",)),
)

# 分类(二)——真·可调杠杆。T1(output_impact=none)= M0 默认放开;T2 走「质量门」开关。
_TUNABLE: tuple[Knob, ...] = (
    # —— T1 ——
    Knob("max_num_seqs", "--max-num-seqs", "int", "提并发/利用率",
         helps=("A", "B"), hurts=("D",), primary_slo="throughput", default=256,
         lo=1, hi=2048, big_batch=True),
    Knob("max_num_batched_tokens", "--max-num-batched-tokens", "int", "加大每step预算",
         helps=("A", "C"), hurts=("D",), primary_slo="ttft", default=2048,
         lo=256, hi=65536, big_batch=True),
    # vLLM 0.21 V1 硬拒任何非默认值:"No Concurrent Partial Prefills so far"
    # (arg_utils.py:2191 _check_feature_supported)。真机连烧两轮 LaunchError 后钉死。
    Knob("max_num_partial_prefills", "--max-num-partial-prefills", "int", "短prompt插队",
         helps=("A",), hurts=(), primary_slo="ttft", default=1, lo=1, hi=8,
         needs=("enable_chunked_prefill",), unsupported=True),
    Knob("long_prefill_token_threshold", "--long-prefill-token-threshold", "int", "长prompt降级",
         helps=("A",), hurts=(), primary_slo="ttft", default=0, lo=0, hi=8192),
    Knob("gpu_memory_utilization", "--gpu-memory-utilization", "float", "腾容量(扩KV池)",
         helps=("B", "D"), hurts=(), primary_slo="throughput", default=0.92,
         lo=0.5, hi=0.97),
    Knob("cudagraph_mode", "--cudagraph-mode", "choice", "减per-step开销",
         helps=("A", "B"), hurts=(), primary_slo="tpot", default="FULL_AND_PIECEWISE",
         choices=("PIECEWISE", "FULL_AND_PIECEWISE")),
    Knob("max_model_len", "--max-model-len", "int", "腾容量(降上下文)",
         helps=("D",), hurts=(), primary_slo="throughput", default=0, lo=512, hi=131072),
    Knob("cpu_offload_gb", "--cpu-offload-gb", "int", "腾容量(权重→CPU)",
         helps=("D",), hurts=(), primary_slo="tpot", default=0, lo=0, hi=128),
    Knob("performance_mode", "--performance-mode", "choice", "元旋钮(batch+cudagraph策略)",
         helps=("A", "B"), hurts=("D",), primary_slo="throughput", default="balanced",
         choices=("interactivity", "balanced", "throughput")),
    # —— T2(走「质量门」开关,output_impact != none)——
    Knob("kv_cache_dtype", "--kv-cache-dtype", "choice", "减字节+腾容量",
         helps=("B", "D"), hurts=(), primary_slo="tpot", output_impact="equivalence",
         default="auto", choices=("auto", "fp8"), conflicts=("attention_backend",)),
    Knob("quantization", "--quantization", "choice", "减字节(权重)",
         helps=("B", "D"), hurts=(), primary_slo="tpot", output_impact="equivalence",
         default=None, choices=(None, "fp8", "awq")),
    Knob("speculative", "--speculative-config", "choice", "减步数(摊权重读)",
         helps=("B",), hurts=("C",), primary_slo="tpot", output_impact="equivalence",
         default=None, choices=(None, "ngram"), conflicts=("async_scheduling",)),
    Knob("attention_backend", "--attention-backend", "choice", "抬有效屋顶",
         helps=("B", "C"), hurts=(), primary_slo="ttft", output_impact="equivalence",
         default="auto", choices=("auto", "FLASHINFER"), conflicts=("kv_cache_dtype",)),
)

KNOBS: tuple[Knob, ...] = _DEFAULT_ON + _TUNABLE
_BY_KEY = {k.key: k for k in KNOBS}


# === introspect:vllm 可导入时读当前生效默认值,填 default_0_21(§4.6.1)===

def introspect_defaults() -> dict:
    """读 vllm EngineArgs 当前默认值(可导入时);否则空 → 用 Knob.default 静态值。

    M0 价值:让 propose 跳过"已到位/默认已开"的旋钮,不烧空轮(§4.4「先读当前生效值」)。
    """
    out: dict = {}
    try:  # pragma: no cover - 需装 vllm
        from vllm.engine.arg_utils import EngineArgs  # noqa: PLC0415
        ea = EngineArgs(model="x")
        for k in KNOBS:
            if hasattr(ea, k.key):
                out[k.key] = getattr(ea, k.key)
    except Exception:  # noqa: BLE001 — 无 vllm / 版本差异 → 回退静态
        pass
    return out


def param_surface_size() -> int:
    """introspect 全量参数面规模(§4.2 的 258)。可导入 vllm 时实数,否则约数。"""
    try:  # pragma: no cover
        import dataclasses

        from vllm.engine.arg_utils import EngineArgs  # noqa: PLC0415
        return len(dataclasses.fields(EngineArgs))
    except Exception:  # noqa: BLE001
        return 258


# === 取值步进:朝"利于当前 regime"的方向走一档 ===

def _step(k: Knob, cur, regime: str):
    """返回该旋钮在 regime 下的下一档值;到边界/无意义 → None。"""
    if k.kind == "choice":
        seq = list(k.choices)
        # D regime 下 performance_mode 反向(throughput 伤 D → 往 interactivity 退)
        order = seq[::-1] if (regime == "D" and k.key == "performance_mode") else seq
        try:
            i = order.index(cur)
        except ValueError:
            i = 0
        return order[i + 1] if i + 1 < len(order) else None
    cur = float(cur if cur not in (None, 0) else k.default or k.lo)
    if k.kind == "int":
        # D:max_num_seqs / max_model_len 往下(腾容量);其余往上(提并发/加预算)
        down = regime == "D" and k.key in ("max_num_seqs", "max_model_len")
        nxt = max(k.lo, cur // 2) if down else min(k.hi, cur * 2)
        nxt = int(round(nxt))
    else:  # float
        down = False
        nxt = round(min(k.hi, cur + 0.10), 3)
    return None if nxt == int(cur) or nxt == round(cur, 3) else nxt


def _lever_for(k: Knob, regime: str, cur, nxt) -> str:
    if k.key == "max_num_seqs" and regime == "D" and float(nxt) < float(cur):
        return "降并发准入/缓解KV容量压力"
    if k.key == "max_model_len" and regime == "D" and float(nxt) < float(cur):
        return "降上下文上限/释放KV容量"
    return k.lever


# === 约束图可行性(§4.5):剪掉硬冲突/缺前置的候选 ===

def _feasible(k: Knob, config: dict) -> bool:
    for need in k.needs:                       # 前置 flag 必须为真
        nk = _BY_KEY.get(need)
        if not _enabled(config.get(need, nk.default if nk else None)):
            return False
    return all(not _enabled(config.get(conf)) for conf in k.conflicts)  # 与冲突项同开 → 不可行


def _enabled(value) -> bool:
    if isinstance(value, str):
        return value.lower() not in ("", "0", "false", "none", "auto")
    return bool(value)


def propose_candidates(bottleneck: str | None, config: dict, *,
                       kv_headroom: float = 1.0, quality_gate: bool = False,
                       load_binding: bool | None = None) -> list[dict]:
    """诊断→动作交集 + D 余量守卫 + 约束图 + 跳过已到位(§4.4)。

    交集:`helps ∋ regime ∧ hurts ∌ regime ∧ 值未到位 ∧ 非默认已开 ∧ 约束可行`。
    D 余量守卫:推大-batch 类(`big_batch`)只在 KV 余量足时给(`kv_headroom > 0.15`),
    否则先治 D。质量门关时(默认)只给 T1(output_impact=none)。
    准入闸绑定守卫(load_binding):bench 窗口实测 running 峰值远低于 max_num_seqs 且
    waiting=0 时(False),准入闸没绑定——瓶颈在提供的负载,提 max_num_seqs 是空转
    (真实教训:并发 8 的压测下 32→64 恒 tie)。None=无实测证据,保持旧行为。
    排序:按「主影响 SLO」让对症旋钮靠前(仅排序,不硬筛)。
    """
    bn = bottleneck or "A"             # 无诊断 → 按"喂不饱(双低)"探索
    cands: list[dict] = []
    for k in KNOBS:
        if k.unsupported:              # 当前 vLLM 版本硬拒非默认值 → 提了必 LaunchError
            continue
        if k.default_on:               # 分类(一)默认已开,跳过(除非诊断显式点名,M0 不点)
            continue
        if k.output_impact != "none" and not quality_gate:
            continue                   # 质量门关:只 T1
        helps = bn in k.helps
        d_relief = bn == "D" and k.key in ("max_num_seqs", "max_model_len")  # D:降并发/降len 缓解
        if not (helps or d_relief):
            continue
        if bn in k.hurts and not d_relief:
            continue                   # 会恶化当前瓶颈的不选
        if k.big_batch and kv_headroom <= 0.15:
            continue                   # D 余量守卫:KV 快满,先治 D 再推大 batch
        if k.key == "max_num_seqs" and load_binding is False and bn in ("A", "B"):
            continue                   # 准入闸没绑定:提并发上限治不了"负载喂不进来"
        if not _feasible(k, config):
            continue                   # 约束图:缺前置/硬冲突
        cur = config.get(k.key, k.default if k.default is not None else k.lo)
        nxt = _step(k, cur, bn)
        if nxt is None:                # 已到位 / 到边界
            continue
        new_cfg = dict(config)
        new_cfg[k.key] = nxt
        lever = _lever_for(k, bn, cur, nxt)
        cands.append({"knob": k.key, "flag": k.flag, "from": cur, "to": nxt,
                      "config": new_cfg, "output_impact": k.output_impact,
                      "primary_slo": k.primary_slo, "lever": lever,
                      # kind/range:LLM 可在值域内沿候选方向自选 value(不必逐档爬梯,
                      # 证据支持时可一步到位);"to" 只是建议档
                      "kind": k.kind,
                      "range": list(k.choices) if k.kind == "choice" else [k.lo, k.hi]})
    # 排序:对症 SLO 在前(throughput 类对 A/B,ttft 类对 prefill,tpot 类对 decode)
    pri = {"A": "throughput", "B": "tpot", "C": "ttft", "D": "throughput"}.get(bn, "throughput")
    cands.sort(key=lambda c: 0 if c["primary_slo"] == pri else 1)
    return cands


def _render_special(key: str, value) -> list[str] | None:
    """个别旋钮不是「--flag 值」的朴素形状,按 vLLM 0.21 真实 CLI 形态渲染(§4.5):
    - speculative:`--speculative-config` 收 JSON dict,裸字符串起不来;
    - cudagraph_mode:是 CompilationConfig 字段,顶层没有 --cudagraph-mode flag,
      走 `--compilation-config` JSON(`config/compilation.py:53-103`)。
    返回 None = 走通用渲染。"""
    import json as _json
    if key == "speculative":
        if value in (None, "", "none"):
            return []                            # 关 = 不传 flag
        spec = {"method": str(value), "num_speculative_tokens": 3}
        return ["--speculative-config", _json.dumps(spec, separators=(",", ":"))]
    if key == "cudagraph_mode":
        return ["--compilation-config",
                _json.dumps({"cudagraph_mode": str(value)}, separators=(",", ":"))]
    return None


def render_flags(config: dict) -> list[str]:
    """config → vllm serve flag tokens(给 docker run 拼参数)。choice/None 跳过。"""
    out: list[str] = []
    for k in KNOBS:
        if k.key in config and config[k.key] is not None:
            special = _render_special(k.key, config[k.key])
            if special is not None:
                out += special
            else:
                out += [k.flag, str(config[k.key])]
    return out


def render_command(model: str, config: dict) -> str:
    """可复制的 `vllm serve ...`;含 JSON/空白的 token 加 shell 单引号。"""
    def q(tok: str) -> str:
        return f"'{tok}'" if any(c in tok for c in ' {}"') else tok
    return " ".join([f"vllm serve {model}", *(q(t) for t in render_flags(config))])


def is_known_knob(key: str) -> bool:
    return key in _BY_KEY


def knob(key: str) -> Knob | None:
    return _BY_KEY.get(key)
