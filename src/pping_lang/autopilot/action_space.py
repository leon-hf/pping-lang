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

import os
import re
from dataclasses import dataclass
from pathlib import Path

# 瓶颈字母与诊断引擎一致:A 喂不饱 / B 带宽瓶颈 / C 算力瓶颈 / D 容量瓶颈(§4.3)
REGIMES = ("A", "B", "C", "D")

# 裸字母(如事件消息/evidence_refs 里的 "B"、"B:live")没有上下文,用户看不出是什么——
# 统一换成人话(如"带宽瓶颈"),字母本身不再出现在任何用户可见文本里
# (2026-07-21 用户反馈:连"(B)"这种带字母的括注都别留,谁都看不出字母指代什么)。
BOTTLENECK_LABEL: dict[str, str] = {"A": "双低", "B": "带宽瓶颈", "C": "算力瓶颈", "D": "容量瓶颈"}


def bottleneck_label(bn: str | None) -> str:
    return BOTTLENECK_LABEL.get(bn, "症状/其它")


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


# === 半自动 introspect + 白名单标签表(§4.4 / §4.6)===
#
# 设计:人工维护「哪些 flag 是性能杠杆 + 方向标签(helps/hurts/lever)」这张白名单;
# 默认值从本地 vLLM 源码的 EngineArgs 自动读取。这样既避免把 258 个背景参数全丢给 agent,
# 又能随 vLLM 版本更新自动校准默认值,不用每次手工改 static default。
#
# 读取路径优先级:
#   1. 环境变量 PPING_VLLM_SOURCE 指向的 vLLM 源码根目录
#   2. 固定候选 D:\GitCode\vllm
#   3. 读不到/解析失败 → 使用白名单里的 static_default

_VLLM_SOURCE_ROOT = os.environ.get("PPING_VLLM_SOURCE", r"D:\GitCode\vllm")
_UNPARSED = object()


def _parse_default_literal(val: str) -> object:
    """解析 dataclass 字段的默认字面量。复杂表达式返回 _UNPARSED,让调用方 fallback。"""
    # 去掉行尾注释(如 `# type: ignore`)
    if "#" in val:
        val = val.split("#", 1)[0]
    val = val.strip()
    if val == "None":
        return None
    if val == "True":
        return True
    if val == "False":
        return False
    if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    try:
        if "." in val and not val.startswith("0x"):
            # 避免把 "ModelConfig.x" 当 float
            float(val)
            return float(val)
        return int(val)
    except ValueError:
        return _UNPARSED


def _extract_class_body(text: str, class_name: str) -> str | None:
    """从源码文本中提取 class_name 的类体(到第一个方法/def/类装饰器之前)。"""
    pattern = rf"class {class_name}\b.*?:(?=\s*\n)(.*?)(?=\n    def |\n\n@|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1) if match else None


def _parse_class_defaults(text: str, class_name: str) -> dict[str, object]:
    """解析某个 Config 类的字段默认值。"""
    body = _extract_class_body(text, class_name)
    if body is None:
        return {}
    out: dict[str, object] = {}
    for m in re.finditer(
        r"^\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*[^=\n]+?=\s*(.+?)$",
        body, re.MULTILINE | re.DOTALL):
        key = m.group(1)
        val = " ".join(line.strip().lstrip("\\").strip()
                         for line in m.group(2).splitlines())
        parsed = _parse_default_literal(val)
        if parsed is not _UNPARSED:
            out[key] = parsed
    return out


def _introspect_engine_args(vllm_root: str | None = None) -> dict:
    """从 vLLM 源码 EngineArgs 解析字段默认值。不导入 vllm(避免依赖 torch)。

    EngineArgs 大量字段默认值写成 SomeConfig.field(如 ModelConfig.max_seq_len_to_capture),
    因此需要先到 vllm/config.py 把各 Config 类字段默认值解析出来,再做一次查找。
    """
    root = Path(vllm_root or _VLLM_SOURCE_ROOT)
    arg_utils = root / "vllm" / "engine" / "arg_utils.py"
    config_py = root / "vllm" / "config.py"
    if not arg_utils.exists():
        return {}

    config_defaults: dict[str, dict[str, object]] = {}
    if config_py.exists():
        config_text = config_py.read_text(encoding="utf-8")
        for cls in ("ModelConfig", "CacheConfig", "SchedulerConfig",
                    "ParallelConfig", "LoadConfig", "MultiModalConfig",
                    "LoRAConfig", "DecodingConfig"):
            config_defaults[cls] = _parse_class_defaults(config_text, cls)

    text = arg_utils.read_text(encoding="utf-8")
    body = _extract_class_body(text, "EngineArgs")
    if body is None:
        return {}

    defaults: dict[str, object] = {}
    for m in re.finditer(
        r"^\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*[^=\n]+?=\s*(.+?)$",
        body, re.MULTILINE | re.DOTALL):
        key = m.group(1)
        val = " ".join(line.strip().lstrip("\\").strip()
                         for line in m.group(2).splitlines())

        # SomeConfig.field 形式 → 查 config.py 解析结果
        cm = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$", val)
        if cm:
            cls_name, fld_name = cm.group(1), cm.group(2)
            if cls_name in config_defaults and fld_name in config_defaults[cls_name]:
                defaults[key] = config_defaults[cls_name][fld_name]
            continue

        parsed = _parse_default_literal(val)
        if parsed is not _UNPARSED:
            defaults[key] = parsed
    return defaults


# 白名单标签表:性能杠杆参数的元数据。
# static_default 是 vLLM 0.21 典型值,作为源码 introspect 失败时的 fallback。
_KNOB_REGISTRY: tuple[dict, ...] = (
    # 分类(一)——默认已开,通常别动(§4.4)
    {"key": "enable_chunked_prefill", "flag": "--enable-chunked-prefill",
     "kind": "choice", "lever": "减气泡", "helps": ("A",), "primary_slo": "ttft",
     "static_default": True, "default_on": True, "choices": (False, True)},
    {"key": "enable_prefix_caching", "flag": "--enable-prefix-caching",
     "kind": "choice", "lever": "省重算", "helps": ("C",), "primary_slo": "ttft",
     "static_default": True, "default_on": True, "choices": (False, True)},
    {"key": "async_scheduling", "flag": "--async-scheduling",
     "kind": "choice", "lever": "消调度气泡", "helps": ("A",), "primary_slo": "tpot",
     "static_default": True, "default_on": True, "choices": (False, True),
     "conflicts": ("speculative",)},

    # —— T1 ——
    {"key": "max_num_seqs", "flag": "--max-num-seqs",
     "kind": "int", "lever": "提并发/利用率", "helps": ("A", "B"), "hurts": ("D",),
     "primary_slo": "throughput", "static_default": 256, "lo": 1, "hi": 2048,
     "big_batch": True},
    {"key": "max_num_batched_tokens", "flag": "--max-num-batched-tokens",
     "kind": "int", "lever": "加大每step预算", "helps": ("A", "C"), "hurts": ("D",),
     "primary_slo": "ttft", "static_default": 2048, "lo": 256, "hi": 65536,
     "big_batch": True},
    # vLLM 0.21 V1 硬拒任何非默认值,永不提议
    {"key": "max_num_partial_prefills", "flag": "--max-num-partial-prefills",
     "kind": "int", "lever": "短prompt插队", "helps": ("A",), "primary_slo": "ttft",
     "static_default": 1, "lo": 1, "hi": 8, "needs": ("enable_chunked_prefill",),
     "unsupported": True},
    {"key": "long_prefill_token_threshold", "flag": "--long-prefill-token-threshold",
     "kind": "int", "lever": "长prompt降级", "helps": ("A",), "primary_slo": "ttft",
     "static_default": 0, "lo": 0, "hi": 8192},
    {"key": "gpu_memory_utilization", "flag": "--gpu-memory-utilization",
     "kind": "float", "lever": "腾容量(扩KV池)", "helps": ("B", "D"),
     "primary_slo": "throughput", "static_default": 0.92, "lo": 0.5, "hi": 0.97},
    {"key": "cudagraph_mode", "flag": "--cudagraph-mode",
     "kind": "choice", "lever": "减per-step开销", "helps": ("A", "B"),
     "primary_slo": "tpot", "static_default": "FULL_AND_PIECEWISE",
     "choices": ("PIECEWISE", "FULL_AND_PIECEWISE")},
    {"key": "max_model_len", "flag": "--max-model-len",
     "kind": "int", "lever": "腾容量(降上下文)", "helps": ("D",),
     "primary_slo": "throughput", "static_default": 0, "lo": 512, "hi": 131072},
    {"key": "cpu_offload_gb", "flag": "--cpu-offload-gb",
     "kind": "int", "lever": "腾容量(权重→CPU)", "helps": ("D",),
     "primary_slo": "tpot", "static_default": 0, "lo": 0, "hi": 128},
    {"key": "performance_mode", "flag": "--performance-mode",
     "kind": "choice", "lever": "元旋钮(batch+cudagraph策略)", "helps": ("A", "B"),
     "hurts": ("D",), "primary_slo": "throughput", "static_default": "balanced",
     "choices": ("interactivity", "balanced", "throughput")},
    # 调度与开销类(不依赖 load_binding)
    # 2026-07-16 dogfood 真机复现:`vllm serve --help` 里根本没有这两个 flag,提了必
    # `unrecognized arguments`(V1 引擎砍掉了 multi-step 调度,§这两个是 V0 遗留)——
    # 每次被选中都是白烧一轮(候选启动崩溃→回滚),标 unsupported 让它们永不被提议。
    {"key": "num_scheduler_steps", "flag": "--num-scheduler-steps",
     "kind": "int", "lever": "减调度开销", "helps": ("A", "B"), "hurts": ("C",),
     "primary_slo": "tpot", "static_default": 1, "lo": 1, "hi": 64,
     "unsupported": True},
    # prefill_chunk_size 同样是当前 vLLM build 没有的 flag:2026-07-19 dogfood session
    # ap-20260719-004104 R4 真机复现——候选容器直接打印 usage 退出(LaunchError),
    # 白烧一轮(启动崩溃→回滚),标 unsupported 让它永不被提议。
    {"key": "prefill_chunk_size", "flag": "--prefill-chunk-size",
     "kind": "int", "lever": "prefill粒度", "helps": ("A", "C"),
     "primary_slo": "ttft", "static_default": 512, "lo": 256, "hi": 8192,
     "unsupported": True},
    {"key": "scheduler_delay_factor", "flag": "--scheduler-delay-factor",
     "kind": "float", "lever": "调度等待系数", "helps": ("A",),
     "primary_slo": "throughput", "static_default": 0.0, "lo": 0.0, "hi": 2.0,
     "unsupported": True},
    # max_seq_len_to_capture 在当前 vLLM build 同样不存在:2026-07-19 7B session
    # ap-20260719-153115 R4 候选打印 usage 退出(LaunchError),`vllm serve --help`
    # 实测无此 flag——白烧一轮,标 unsupported。
    {"key": "max_seq_len_to_capture", "flag": "--max-seq-len-to-capture",
     "kind": "int", "lever": "cudagraph捕获长度", "helps": ("A", "B"),
     "primary_slo": "tpot", "static_default": 8192, "lo": 512, "hi": 32768,
     "unsupported": True},
    # D 守卫类
    {"key": "num_gpu_blocks_override", "flag": "--num-gpu-blocks-override",
     "kind": "int", "lever": "手动KV块数", "helps": ("D",),
     "primary_slo": "throughput", "static_default": None, "lo": 256, "hi": 65536},
    {"key": "block_size", "flag": "--block-size",
     "kind": "choice", "lever": "KV块大小", "helps": ("D",),
     "primary_slo": "throughput", "static_default": None,
     "choices": (8, 16, 32)},

    # —— T2 ——
    {"key": "kv_cache_dtype", "flag": "--kv-cache-dtype",
     "kind": "choice", "lever": "减字节+腾容量", "helps": ("B", "D"),
     "primary_slo": "tpot", "output_impact": "equivalence",
     "static_default": "auto", "choices": ("auto", "fp8"),
     "conflicts": ("attention_backend",)},
    {"key": "quantization", "flag": "--quantization",
     "kind": "choice", "lever": "减字节(权重)", "helps": ("B", "D"),
     "primary_slo": "tpot", "output_impact": "equivalence",
     "static_default": None, "choices": (None, "fp8", "awq")},
    {"key": "speculative", "flag": "--speculative-config",
     "kind": "choice", "lever": "减步数(摊权重读)", "helps": ("B",), "hurts": ("C",),
     "primary_slo": "tpot", "output_impact": "equivalence",
     "static_default": None, "choices": (None, "ngram"),
     "conflicts": ("async_scheduling",)},
    # MTP / speculative 细粒度控制
    {"key": "num_lookahead_slots", "flag": "--num-lookahead-slots",
     "kind": "int", "lever": "MTP前瞻槽数", "helps": ("B",), "hurts": ("C",),
     "primary_slo": "tpot", "output_impact": "equivalence",
     "static_default": None, "lo": 1, "hi": 8},
    {"key": "ngram_prompt_lookup_max", "flag": "--ngram-prompt-lookup-max",
     "kind": "int", "lever": "ngram回溯最大长度", "helps": ("B",), "hurts": ("C",),
     "primary_slo": "tpot", "output_impact": "equivalence",
     "static_default": None, "lo": 2, "hi": 16},
    {"key": "attention_backend", "flag": "--attention-backend",
     "kind": "choice", "lever": "抬有效屋顶", "helps": ("B", "C"),
     "primary_slo": "ttft", "output_impact": "equivalence",
     "static_default": "auto", "choices": ("auto", "FLASHINFER"),
     "conflicts": ("kv_cache_dtype",)},
)


def _build_knob(meta: dict, defaults: dict) -> Knob:
    """把白名单元数据和 EngineArgs 解析出的默认值合并成 Knob。"""
    return Knob(
        key=meta["key"], flag=meta["flag"], kind=meta["kind"], lever=meta["lever"],
        helps=meta.get("helps", ()), hurts=meta.get("hurts", ()),
        primary_slo=meta["primary_slo"],
        output_impact=meta.get("output_impact", "none"),
        default=defaults.get(meta["key"], meta.get("static_default")),
        default_on=meta.get("default_on", False),
        lo=meta.get("lo", 0.0), hi=meta.get("hi", 0.0),
        choices=meta.get("choices", ()),
        big_batch=meta.get("big_batch", False),
        needs=meta.get("needs", ()),
        conflicts=meta.get("conflicts", ()),
        unsupported=meta.get("unsupported", False),
    )


_EA_DEFAULTS = _introspect_engine_args(_VLLM_SOURCE_ROOT)
KNOBS: tuple[Knob, ...] = tuple(
    _build_knob(m, _EA_DEFAULTS) for m in _KNOB_REGISTRY)
_BY_KEY = {k.key: k for k in KNOBS}


def action_space_stats() -> dict:
    """全量旋钮面按分类计数(§4.2)——给 session 收尾"为什么只调这些参数"的总结用。
    用户反馈(2026-07-22):每次 session 就调 2-3 个参数,看不出剩下的 ~250 个 vLLM 参数
    是"不该调"还是"没顾上调"。三类跳过原因:default_on=vLLM 启动已自调最优,不用碰;
    unsupported=当前 vLLM build 没这个 flag,提了必 LaunchError;precision=会降精度,
    按产品策略不提供。剩下的才是真正可能被提议的候选池。"""
    default_on = sum(1 for k in KNOBS if k.default_on)
    unsupported = sum(1 for k in KNOBS if k.unsupported)
    precision = sum(1 for k in KNOBS if k.output_impact != "none")
    considerable = [k for k in KNOBS if not k.default_on and not k.unsupported
                    and k.output_impact == "none"]
    return {
        "total": len(KNOBS),
        "default_on_count": default_on,
        "unsupported_count": unsupported,
        "precision_excluded_count": precision,
        "considerable_count": len(considerable),
        "considerable_knobs": sorted(k.key for k in considerable),
    }


def knobs_helping(bn: str) -> list[str]:
    """候选池(非 default_on/unsupported/precision)里对症给定瓶颈字母的旋钮 key——
    含 B↔D 共生并集(§4.3,同 propose_candidates 的耦合逻辑),否则耦合旋钮(如
    cpu_offload_gb/num_gpu_blocks_override)会被试过却不在"相关旋钮"里,总结自相矛盾。"""
    bns = {bn} | set(_COUPLED_REGIMES.get(bn, ()))
    return sorted(k.key for k in KNOBS
                  if not k.default_on and not k.unsupported
                  and k.output_impact == "none" and bns & set(k.helps))


# === introspect:读 vLLM 源码 EngineArgs 默认值,填 default_0_21(§4.6.1)===

def introspect_defaults(vllm_root: str | None = None) -> dict:
    """读 vLLM 源码 EngineArgs 默认值(不导入 vllm,避免 torch 依赖)。

    M0 价值:让 propose 跳过"已到位/默认已开"的旋钮,不烧空轮。
    如果提供了 vllm_root,优先从该路径解析;否则用模块初始化时的结果。
    """
    defaults = _introspect_engine_args(vllm_root) if vllm_root else _EA_DEFAULTS
    out: dict = {}
    for k in KNOBS:
        if k.key in defaults:
            out[k.key] = defaults[k.key]
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
        # D:max_num_seqs / max_model_len 往下(腾容量);其余往上(提并发/加预算)。
        # cur=0 时翻倍还是 0(cpu_offload_gb 这类 0 起点旋钮永远提不出来)——首档给最小非零步。
        down = regime == "D" and k.key in ("max_num_seqs", "max_model_len")
        nxt = max(k.lo, cur // 2) if down else min(k.hi, max(cur * 2, k.lo, 1))
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
                       load_binding: bool | None = None,
                       couple_regimes: bool = True) -> list[dict]:
    """诊断→动作交集 + D 余量守卫 + 约束图 + 跳过已到位(§4.4)。

    交集:`helps ∋ regime ∧ hurts ∌ regime ∧ 值未到位 ∧ 非默认已开 ∧ 约束可行`。
    D 余量守卫:推大-batch 类(`big_batch`)只在 KV 余量足时给(`kv_headroom > 0.15`),
    否则先治 D。质量门关时(默认)只给 T1(output_impact=none)。
    准入闸绑定守卫(load_binding):bench 窗口实测 running 峰值远低于 max_num_seqs 且
    waiting=0 时(False),准入闸没绑定——瓶颈在提供的负载,提 max_num_seqs 是空转
    (真实教训:并发 8 的压测下 32→64 恒 tie)。None=无实测证据,保持旧行为。
    B↔D 共生并集(§4.3,couple_regimes 开):诊断 B 时把 D 缓解类旋钮并进来——
    治 B 要推大 batch,推大 batch 要 KV 空间,真实 decode 负载常同时压在两档上;
    方向按 D 语义(如 max_model_len↓),D 余量守卫对并集候选同样硬约束。
    排序:按「主影响 SLO」让对症旋钮靠前(仅排序,不硬筛);并集候选排在主 regime 之后。
    """
    bn = bottleneck or "A"             # 无诊断 → 按"喂不饱(双低)"探索
    cands = _regime_candidates(bn, config, kv_headroom=kv_headroom,
                               quality_gate=quality_gate, load_binding=load_binding)
    secondary: list[dict] = []
    if couple_regimes:
        for extra in _COUPLED_REGIMES.get(bn, ()):
            for c in _regime_candidates(extra, config, kv_headroom=kv_headroom,
                                        quality_gate=quality_gate,
                                        load_binding=load_binding):
                if all(c["knob"] != p["knob"] for p in cands):   # 主 regime 版优先
                    c["secondary_regime"] = extra
                    secondary.append(c)
    # 排序:对症 SLO 在前(throughput 类对 A/B,ttft 类对 prefill,tpot 类对 decode)
    pri = {"A": "throughput", "B": "tpot", "C": "ttft", "D": "throughput"}.get(bn, "throughput")
    cands.sort(key=lambda c: 0 if c["primary_slo"] == pri else 1)
    return cands + secondary


# B↔D 共生(§4.3):诊断命中主 regime 时,并入耦合 regime 的缓解类旋钮。
_COUPLED_REGIMES: dict[str, tuple[str, ...]] = {"B": ("D",)}


def _regime_candidates(bn: str, config: dict, *, kv_headroom: float,
                       quality_gate: bool, load_binding: bool | None) -> list[dict]:
    """单 regime 的候选生成(propose_candidates 的工作马,供主/并集两路复用)。"""
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


# 这些旋钮的 0 表示"禁用/不覆盖",传 0 和不传等效,某些 flag 传 0 还会让 vLLM 报错
_ZERO_DISABLED_KEYS = {
    "num_gpu_blocks_override", "num_lookahead_slots", "ngram_prompt_lookup_max",
    "long_prefill_token_threshold", "cpu_offload_gb",
}


def render_flags(config: dict) -> list[str]:
    """config → vllm serve flag tokens(给 docker run 拼参数)。choice/None/0禁用 跳过。"""
    out: list[str] = []
    for k in KNOBS:
        if k.key not in config:
            continue
        value = config[k.key]
        if value is None:
            continue
        # 0 默认值通常表示"不启用/自动",传 0 和不传等效,且某些 flag 传 0 会报错
        if value == 0 and (k.default == 0 or k.key in _ZERO_DISABLED_KEYS):
            continue
        special = _render_special(k.key, value)
        if special is not None:
            out += special
        else:
            out += [k.flag, str(value)]
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
