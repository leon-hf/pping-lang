"""Agent(LLM)—— §5 漏斗的 P3 判断层(§8)。

**不是数值搜索器**(那是 P2);是"读蒸馏诊断 → 从候选交集挑 1 个对症动作 → 挂证据 → 接受 bench 判决"。
三重约束(§8):① 只能从动作空间(诊断剪枝后的候选集)选;② 必须挂诊断证据(evidence_refs);
③ 必须接受压测判决(不得声称压测没证实的收益)。

可插拔(G3,默认 Claude/Anthropic):`StubAgent`(确定性,无 key,默认兜底)/ `ClaudeAgent`
(Anthropic /v1/messages)/ `KimiCodingAgent`(Kimi Coding /messages)/
`OpenAIAgent`(OpenAI 兼容:DeepSeek/OpenRouter/本地)。
`ResilientAgent` 把真 LLM 调用失败兜回 StubAgent,session 不因网络抖动而死。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

# === 输入(每轮)= AgentContext(§8)===

@dataclass
class AgentContext:
    objective: dict                              # {target, sla, ...}
    round: int
    budget: dict                                 # {rounds_left, seconds_left}
    current_config: dict                         # = 当前 best,已加载
    diagnosis: dict                              # 蒸馏事实:{bottleneck, fired_rules, mfu, mbu,
                                                 #   running, waiting, kv_util, ttft_p99_ms, ...}
    candidates: list[dict]                       # §4 诊断→交集 + D 守卫后的旋钮子集
    history: list[dict] = field(default_factory=list)        # [{round, action, decision, ...}]
    tried_configs: list[dict] = field(default_factory=list)  # [{hash, decision}] 防重
    best_so_far: dict = field(default_factory=dict)          # {config, scorecard}


# === 输出(StructuredOutput 强约束)= AgentDecision(§8)===

@dataclass
class AgentDecision:
    done: bool = False
    reason: str = ""                             # done=true 时填(为何判已近最优)
    knob: str | None = None
    config: dict | None = None
    from_val: object = None
    to_val: object = None
    flag: str | None = None
    rationale: str = ""
    expected_effect: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    guardrail_notes: str = ""
    candidate_meta: dict = field(default_factory=dict)
    thinking: str = ""                           # LLM 思考过程(provider 支持时),直播+落轨迹


def config_hash(config: dict) -> str:
    return ";".join(f"{k}={config[k]}" for k in sorted(config))


def validate(dec: AgentDecision, ctx: AgentContext) -> str | None:
    """校验 ∈ 候选集 + 防重(§9.3 proposing)。返回 None=合法,否则返回拒绝原因。"""
    if dec.done:
        return None
    cand = next((c for c in ctx.candidates if c["knob"] == dec.knob), None)
    if cand is None:
        return f"knob {dec.knob!r} 不在候选集(只能从 {[c['knob'] for c in ctx.candidates]} 选)"
    tried = {t["hash"]: t.get("decision") for t in ctx.tried_configs}
    # 防重按"实际要 apply 的配置"查:LLM 自选 value 时,同旋钮不同值是不同配置,不误挡
    h = config_hash(dec.config if dec.config else cand["config"])
    if tried.get(h) in ("reverted", "tie"):      # 已试且失败/持平 → 防重硬挡
        return f"配置 {h} 已试过且 {tried[h]},别再提"
    return None


# === 合约 prompt(锁定,不可改)+ regime playbook 先验(§8 / §4.4)===

LOCKED_PROMPT = (
    "你是一个 LLM-serving 性能工程师。每轮只能改 1 个旋钮(或一个解析上耦合的组),"
    "且只能从给定候选集里选。你必须:\n"
    "① 把改动挂在本轮诊断证据上(evidence_refs,指向 fired_rules / regime / metric);\n"
    "② 接受压测判决——不得声称压测没证实的收益;\n"
    "③ 目标:不破 SLA 前提下最大化主指标。若判断已近最优(再动会破 SLA 或屋顶已贴),done:true。\n"
    "数值旋钮(kind=int/float)的 value 可在 range 内沿候选方向自选——证据支持时直接一步到位"
    "(如 waiting 队列明示需求量),不必逐档试;不给 value 或给出非法值则用建议档(to)。"
    "choice 旋钮用建议值。\n"
    "只输出 JSON:{\"done\":bool, \"reason\":str, \"action\":{\"knob\":str,\"value\":number|str}, "
    "\"rationale\":str, \"expected_effect\":str, \"evidence_refs\":[str], \"guardrail_notes\":str}"
)

# regime playbook(§4.4 两张表按 regime 切片的先验,让 agent 不冷启动)
REGIME_PLAYBOOK = {
    "A": "喂不饱:硬件有余、活没喂进去。**先看 running vs max_num_seqs**:running 峰值远低于"
         "max_num_seqs 且 waiting=0 → 准入闸没绑定,瓶颈在提供的负载(压测并发/客户端),提"
         "max_num_seqs 是空转,应 done 并如实说明。闸真绑定(running≈max_num_seqs 或 waiting>0)"
         "才提并发(max_num_seqs↑,KV 有余量时)/ 加 step 预算(max_num_batched_tokens↑)/ 短 prompt"
         "插队(max_num_partial_prefills↑)。注:chunked-prefill/async-sched/cudagraph 0.21 默认已开,别重复打开。",
    "B": "带宽墙(decode 本性):提 batch 摊薄权重搬运(先查 D 余量)/ kv-cache fp8 / 投机解码(查接受率防 C 反噬)。",
    "C": "算力墙(长 prompt prefill):attention-backend / fp8 / prefix-caching(仅当有公共前缀)。",
    "D": "容量墙(KV 耗尽→抢占):扩 KV 池(gpu-util↑)/ kv-cache fp8 / 降 max-model-len / cpu-offload。"
         "绝不提并发(max_num_seqs↑ 恶化 D)。",
}


# 自由文本字段(rationale/reason/expected_effect/guardrail_notes)的应答语言:
# 不指定时 LLM 会跟着上下文里的英文技术标识符(max_num_seqs/evidence_refs…)默认说英文,
# 即使 system prompt 本身是中文——thinking 通道尤其容易滑向模型默认语言。
# 只影响这几个自由文本字段,不影响 JSON key / knob 名 / evidence_refs 里的规则 id。
_LANG_DIRECTIVE = {
    "zh": "所有自由文本字段(rationale/reason/expected_effect/guardrail_notes,以及思考过程)"
          "必须用中文回答,不要用英文。knob 名、JSON key、evidence_refs 里的规则 id 保持原样不译。",
    "en": "Answer all free-text fields (rationale/reason/expected_effect/guardrail_notes, and your "
          "thinking) in English. Keep knob names, JSON keys, and evidence_refs rule ids as-is.",
}


def build_messages(ctx: AgentContext, guidance: str = "", lang: str = "zh") -> tuple[str, str]:
    """§8 prompt 骨架:返回 (system, user)。lang 控制自由文本字段的应答语言(zh/en,默认 zh)。"""
    directive = _LANG_DIRECTIVE.get(lang, _LANG_DIRECTIVE["zh"])
    system = LOCKED_PROMPT + "\n" + directive + (("\n———\n额外指引:" + guidance) if guidance else "")
    bn = ctx.diagnosis.get("bottleneck")
    user = json.dumps({
        "objective": ctx.objective,
        "round": ctx.round, "budget": ctx.budget,
        "current_config": ctx.current_config,
        "diagnosis": ctx.diagnosis,
        "regime_playbook": REGIME_PLAYBOOK.get(bn, ""),
        "history": ctx.history,
        "tried_and_failed": [t["hash"] for t in ctx.tried_configs
                             if t.get("decision") in ("reverted", "tie")],
        "candidate_actions": [{"knob": c["knob"], "from": c["from"], "to": c["to"],
                               "kind": c.get("kind"), "range": c.get("range"),
                               "lever": c.get("lever"), "output_impact": c.get("output_impact"),
                               "p0": c.get("p0"), "p2": c.get("p2")}
                              for c in ctx.candidates],
    }, ensure_ascii=False)
    return system, user


def _agent_value(cand: dict, value):
    """LLM 自选值:int/float 在 range 内 clamp,且须沿候选方向(诊断定方向,agent 定跨度);
    不合法/未给/choice 类 → None(回落候选建议档)。"""
    if value is None or cand.get("kind") not in ("int", "float"):
        return None
    rng = cand.get("range")
    if not rng or len(rng) != 2:
        return None
    try:
        v = float(value)
        lo, hi = float(rng[0]), float(rng[1])
        frm = float(cand["from"])
        to = float(cand["to"])
    except (TypeError, ValueError):
        return None
    v = min(max(v, lo), hi)
    if to > frm and v <= frm:                    # 方向须与候选一致(候选方向来自诊断)
        return None
    if to < frm and v >= frm:
        return None
    return int(round(v)) if cand["kind"] == "int" else round(v, 3)


def _decision_from_json(out: dict, ctx: AgentContext) -> AgentDecision:
    """LLM JSON → AgentDecision。knob 只能从候选选;数值旋钮的 value 可在 range 内
    沿候选方向自选(一步到位省轮次),非法则回落候选建议档。launch-catch + bench 兜底。"""
    if out.get("done"):
        return AgentDecision(done=True, reason=out.get("reason", "已近最优"),
                             rationale=out.get("rationale", out.get("reason", "")),
                             evidence_refs=list(out.get("evidence_refs", [])))
    act = out.get("action") or {}
    knob = act.get("knob")
    cand = next((c for c in ctx.candidates if c["knob"] == knob), None)
    if cand is None:                             # 非法 knob → 让 runner 走防重重试/failed
        return AgentDecision(done=False, knob=knob, config=None,
                             rationale=out.get("rationale", ""),
                             evidence_refs=list(out.get("evidence_refs", [])))
    free_val = _agent_value(cand, act.get("value"))
    if free_val is not None:
        to_val, source = free_val, "agent"
        config = dict(cand["config"])
        config[knob] = free_val
    else:
        to_val, source = cand["to"], "ladder"
        config = cand["config"]
    return AgentDecision(
        done=False, knob=knob, config=config, from_val=cand["from"], to_val=to_val,
        flag=cand["flag"], rationale=out.get("rationale", ""),
        expected_effect=out.get("expected_effect", ""),
        evidence_refs=list(out.get("evidence_refs", [])),
        guardrail_notes=out.get("guardrail_notes", ""),
        candidate_meta={"p0": cand.get("p0"), "p2": cand.get("p2"), "value_source": source})


# === 实现 ===

class StubAgent:
    """确定性启发式(默认、无 key):挑候选交集里第一个没试过的;无则 done。"""

    model = "stub-agent"

    def propose(self, ctx: AgentContext) -> AgentDecision:
        tried = {t["hash"] for t in ctx.tried_configs
                 if t.get("decision") in ("reverted", "tie")}
        bn = ctx.diagnosis.get("bottleneck")
        ev = list(ctx.diagnosis.get("evidence_refs", [])) or ([f"regime:{bn}"] if bn else [])
        for c in ctx.candidates:
            if config_hash(c["config"]) in tried:
                continue
            return AgentDecision(
                done=False, knob=c["knob"], config=c["config"], from_val=c["from"],
                to_val=c["to"], flag=c["flag"],
                rationale=(f"命中 {bn or '双低'}:{c['knob']} {c['from']}→{c['to']} "
                           f"({c.get('lever', '对症提升')},不伤当前瓶颈)。"),
                expected_effect="主指标↑,不破 SLA", evidence_refs=ev,
                guardrail_notes="范围内;launch-catch 兜底",
                candidate_meta={"p0": c.get("p0"), "p2": c.get("p2")})
        return AgentDecision(done=True, reason="已无对症候选 / 收益耗尽 → 判定近最优,停。",
                             rationale="已无对症候选", evidence_refs=ev)


class _HTTPAgent:
    """OpenAI/Anthropic 共用:build_messages → _call → JSON → AgentDecision。子类只实现 _call。

    网络层带退避重试:真实链路(如 runw → api.kimi.com)实测 TLS 间歇性 RST,单发成功率
    可低至 ~1/3;不在这里重试,ResilientAgent 会把整个 session 永久打回启发式兜底。"""

    model = "http-agent"
    NET_RETRIES = 4          # 成功率 1/3 时 4 发 ≈ 80%,5 发 ≈ 87%

    def __init__(self, guidance: str = "", temperature: float = 0.4, timeout_s: float = 90.0,
                 lang: str = "zh") -> None:
        # timeout_s 默认 90:thinking 类模型(K2.7 等)单次响应实测常 >30s,
        # 30s 超时会让每轮都退化成启发式兜底(真机 ap-20260706-153658 全程 TimeoutError)。
        self.guidance = guidance
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.lang = lang if lang in _LANG_DIRECTIVE else "zh"
        self._progress = None            # set_progress(cb):重试过程亮给直播,解释等待时长
        self._last_thinking = ""         # 最近一次调用的思考文本(provider 支持时)

    def set_progress(self, cb) -> None:
        self._progress = cb

    def _report(self, msg: str) -> None:
        if self._progress:
            try:
                self._progress(msg)
            except Exception:  # noqa: BLE001
                pass

    def _call(self, system: str, user: str) -> str:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def propose(self, ctx: AgentContext) -> AgentDecision:
        """调用 + JSON 解析整体纳入重试:响应被截断/非 JSON(JSONDecodeError)与网络失败
        同等对待——一次坏响应不该把整轮打回启发式兜底。"""
        import time as _time
        system, user = build_messages(ctx, self.guidance, self.lang)
        last: Exception | None = None
        for attempt in range(self.NET_RETRIES + 1):
            try:
                self._last_thinking = ""
                text = self._call(system, user)
                out = json.loads(text[text.find("{"):text.rfind("}") + 1])
                dec = _decision_from_json(out, ctx)
                dec.thinking = self._last_thinking       # provider 支持时:思考过程随决策带出
                return dec
            except Exception as e:  # noqa: BLE001 — SSL RST/超时/截断/非 JSON
                last = e
                if attempt < self.NET_RETRIES:
                    self._report(f"agent 调用失败({type(e).__name__}),"
                                 f"第 {attempt + 2}/{self.NET_RETRIES + 1} 次尝试…")
                    _time.sleep(min(4.0, 0.5 * (2 ** attempt)))
        raise last  # type: ignore[misc]


class OpenAIAgent(_HTTPAgent):
    """OpenAI 兼容端点(DeepSeek/OpenRouter/本地)。需 base_url+key+model。"""

    def __init__(self, base_url: str, api_key: str, model: str, **kw) -> None:
        super().__init__(**kw)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def _call(self, system: str, user: str) -> str:
        import urllib.request
        body = json.dumps({
            "model": self.model, "temperature": self.temperature,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            msg = json.loads(resp.read())["choices"][0]["message"]
        self._last_thinking = str(msg.get("reasoning_content") or "")
        return msg["content"]


class ClaudeAgent(_HTTPAgent):
    """Anthropic Messages API(G3 钉的默认强模型)。需 api_key + model。"""

    def __init__(self, api_key: str, model: str = "claude-opus-4",
                 base_url: str = "https://api.anthropic.com", **kw) -> None:
        super().__init__(**kw)
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def _call(self, system: str, user: str) -> str:
        import urllib.request
        body = json.dumps({
            "model": self.model, "max_tokens": 1024, "temperature": self.temperature,
            "system": system, "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/messages", data=body,
            headers={"Content-Type": "application/json", "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            blocks = json.loads(resp.read())["content"]
        self._last_thinking = "\n".join(
            str(b.get("thinking") or "") for b in blocks
            if isinstance(b, dict) and b.get("type") == "thinking")
        texts = [b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        return texts[0] if texts else ""


class KimiCodingAgent(_HTTPAgent):
    """Kimi Coding Messages API. This is not Moonshot's OpenAI-compatible API."""

    def __init__(self, api_key: str, model: str = "kimi-for-coding",
                 base_url: str = "https://api.kimi.com/coding/v1", **kw) -> None:
        super().__init__(**kw)
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def _call(self, system: str, user: str) -> str:
        import urllib.request
        body = json.dumps({
            # max_tokens 覆盖 thinking+text:K2.7 思考常吃掉 1000+ tokens,给 1024 会让
            # text block 缺失(确定性 RuntimeError,重试无用)——真机 ap-20260706-234559 的教训
            "model": self.model, "max_tokens": 8192,
            "system": system, "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/messages", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}",
                     "User-Agent": "KimiCLI/0.77"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            content = json.loads(resp.read()).get("content") or []
        self._last_thinking = "\n".join(
            str(b.get("thinking") or "") for b in content
            if isinstance(b, dict) and b.get("type") == "thinking" and b.get("thinking"))
        texts = [str(b.get("text") or "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
        if texts:
            return "\n".join(texts)
        raise RuntimeError("Kimi Coding response did not contain a text block")


class ResilientAgent:
    """主 agent(真 LLM)失败/超时 → 退回 fallback(StubAgent),该轮 rationale 标注,session 不死。"""

    def __init__(self, primary, fallback, retries: int = 1) -> None:
        self._primary = primary
        self._fallback = fallback
        self._retries = max(0, int(retries))
        self.model = getattr(primary, "model", getattr(fallback, "model", ""))

    def set_progress(self, cb) -> None:
        if hasattr(self._primary, "set_progress"):
            self._primary.set_progress(cb)

    def propose(self, ctx: AgentContext) -> AgentDecision:
        last = None
        for _ in range(self._retries + 1):
            try:
                return self._primary.propose(ctx)
            except Exception as e:  # noqa: BLE001 — 网络/解析/超时
                last = e
        dec = self._fallback.propose(ctx)
        dec.rationale = (dec.rationale + f"  [LLM 调用失败({type(last).__name__}),启发式兜底]").strip()
        # 结构化标记:runner 据此发 warn 事件、round 记录落盘 —— 兜底不能只藏在 rationale 文案里
        dec.candidate_meta = {**(dec.candidate_meta or {}), "llm_fallback": type(last).__name__}
        return dec
