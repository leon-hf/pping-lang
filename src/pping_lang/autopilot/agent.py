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
    h = config_hash(cand["config"])
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
    "只输出 JSON:{\"done\":bool, \"reason\":str, \"action\":{\"knob\":str,\"value\":number|str}, "
    "\"rationale\":str, \"expected_effect\":str, \"evidence_refs\":[str], \"guardrail_notes\":str}"
)

# regime playbook(§4.4 两张表按 regime 切片的先验,让 agent 不冷启动)
REGIME_PLAYBOOK = {
    "A": "喂不饱:硬件有余、活没喂进去。先提并发(max_num_seqs↑,KV 有余量时)/ 加 step 预算"
         "(max_num_batched_tokens↑)/ 短 prompt 插队(max_num_partial_prefills↑)。注:chunked-prefill"
         "/async-sched/cudagraph 0.21 默认已开,别重复打开。",
    "B": "带宽墙(decode 本性):提 batch 摊薄权重搬运(先查 D 余量)/ kv-cache fp8 / 投机解码(查接受率防 C 反噬)。",
    "C": "算力墙(长 prompt prefill):attention-backend / fp8 / prefix-caching(仅当有公共前缀)。",
    "D": "容量墙(KV 耗尽→抢占):扩 KV 池(gpu-util↑)/ kv-cache fp8 / 降 max-model-len / cpu-offload。"
         "绝不提并发(max_num_seqs↑ 恶化 D)。",
}


def build_messages(ctx: AgentContext, guidance: str = "") -> tuple[str, str]:
    """§8 prompt 骨架:返回 (system, user)。"""
    system = LOCKED_PROMPT + (("\n———\n额外指引:" + guidance) if guidance else "")
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
                               "lever": c.get("lever"), "output_impact": c.get("output_impact"),
                               "p0": c.get("p0"), "p2": c.get("p2")}
                              for c in ctx.candidates],
    }, ensure_ascii=False)
    return system, user


def _decision_from_json(out: dict, ctx: AgentContext) -> AgentDecision:
    """LLM JSON → AgentDecision。只能从候选按 knob 选,沿用候选预定的安全步长(to/config)。"""
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
    return AgentDecision(
        done=False, knob=knob, config=cand["config"], from_val=cand["from"], to_val=cand["to"],
        flag=cand["flag"], rationale=out.get("rationale", ""),
        expected_effect=out.get("expected_effect", ""),
        evidence_refs=list(out.get("evidence_refs", [])),
        guardrail_notes=out.get("guardrail_notes", ""),
        candidate_meta={"p0": cand.get("p0"), "p2": cand.get("p2")})


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
    """OpenAI/Anthropic 共用:build_messages → _call → JSON → AgentDecision。子类只实现 _call。"""

    model = "http-agent"

    def __init__(self, guidance: str = "", temperature: float = 0.4, timeout_s: float = 30.0) -> None:
        self.guidance = guidance
        self.temperature = temperature
        self.timeout_s = timeout_s

    def _call(self, system: str, user: str) -> str:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def propose(self, ctx: AgentContext) -> AgentDecision:
        system, user = build_messages(ctx, self.guidance)
        text = self._call(system, user)
        out = json.loads(text[text.find("{"):text.rfind("}") + 1])
        return _decision_from_json(out, ctx)


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
            return json.loads(resp.read())["choices"][0]["message"]["content"]


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
            return json.loads(resp.read())["content"][0]["text"]


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
            "model": self.model, "max_tokens": 1024,
            "system": system, "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/messages", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}",
                     "User-Agent": "KimiCLI/0.77"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            content = json.loads(resp.read()).get("content") or []
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

    def propose(self, ctx: AgentContext) -> AgentDecision:
        last = None
        for _ in range(self._retries + 1):
            try:
                return self._primary.propose(ctx)
            except Exception as e:  # noqa: BLE001 — 网络/解析/超时
                last = e
        dec = self._fallback.propose(ctx)
        dec.rationale = (dec.rationale + f"  [LLM 调用失败({type(last).__name__}),启发式兜底]").strip()
        return dec
