"""Autopilot API —— /start /status /stop(§9.5,G6)。

Local/dev can still use SimSandbox, but production runw should point these routes
at the host-side bridge so "start" means a true DockerSandbox/bench session.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

from pping_lang.autopilot.agent import (
    ClaudeAgent,
    KimiCodingAgent,
    OpenAIAgent,
    ResilientAgent,
    StubAgent,
)
from pping_lang.autopilot.objective import SLA, Floor, ObjectiveSpec
from pping_lang.autopilot.runner import Runner
from pping_lang.autopilot.sandbox import SimSandbox
from pping_lang.autopilot.session_store import SessionStore


def build_agent(cfg: dict | None):
    """可插拔(§8,G3 默认 Claude):
    - provider=anthropic 或 base_url 含 anthropic → ClaudeAgent;
    - provider=kimi_coding 或 base_url 含 api.kimi.com/coding → KimiCodingAgent;
    - 有 base_url+key+model(OpenAI 兼容:DeepSeek/OpenRouter/本地)→ OpenAIAgent;
    - 否则纯 StubAgent。真 LLM 一律用 ResilientAgent 兜底 StubAgent(网络/解析失败不死)。"""
    cfg = cfg or {}
    key, base, model = cfg.get("api_key"), cfg.get("base_url"), cfg.get("model")
    common = dict(guidance=cfg.get("guidance", ""), temperature=float(cfg.get("temperature", 0.4)),
                  timeout_s=float(cfg.get("timeout_s", 90)),    # thinking 模型常 >30s(见 _HTTPAgent)
                  lang=cfg.get("lang", "zh"))                   # 自由文本应答语言,默认中文
    primary = None
    if key and (cfg.get("provider") == "anthropic" or (base and "anthropic" in base)):
        primary = ClaudeAgent(api_key=key, model=model or "claude-opus-4", **common)
    elif key and (cfg.get("provider") == "kimi_coding" or (base and "api.kimi.com/coding" in base)):
        primary = KimiCodingAgent(base_url=base or "https://api.kimi.com/coding/v1",
                                  api_key=key, model=model or "kimi-for-coding", **common)
    elif key and base and model:
        primary = OpenAIAgent(base_url=base, api_key=key, model=model, **common)
    if primary is None:
        return StubAgent()
    return ResilientAgent(primary, StubAgent(), retries=int(cfg.get("retries", 1)))


def build_objective(d: dict) -> ObjectiveSpec:
    sla = d.get("sla", {})
    floor = d.get("floor")
    return ObjectiveSpec(
        target=d.get("target", "throughput"),
        sla=SLA(ttft_p99_ms=sla.get("ttft_p99_ms"), tpot_p99_ms=sla.get("tpot_p99_ms")),
        floor=Floor(output_tps=floor["output_tps"]) if floor else None,
        latency_metric=d.get("latency_metric", "tpot"),
        gpu_count=int(d.get("gpu_count", 1)),
        noise_margin=float(d.get("noise_margin", 0.03)),
    )


def test_agent_config(cfg: dict | None) -> dict:
    """Try a minimal provider call for the configured agent.

    This is intentionally server-side: browser-side provider calls usually hit
    CORS, and the API key should not be sprayed through page logs. The key is
    used only for this request and never returned.
    """
    cfg = cfg or {}
    base = str(cfg.get("base_url") or "").rstrip("/")
    key = str(cfg.get("api_key") or "")
    model = str(cfg.get("model") or "")
    is_anthropic = cfg.get("provider") == "anthropic" or (base and "anthropic" in base)
    if is_anthropic and not base:              # ClaudeAgent 同款默认,base 可省
        base = "https://api.anthropic.com"
    if not (base and key and model):
        return {"ok": False, "error": "missing base_url/api_key/model"}

    timeout = max(3.0, min(float(cfg.get("timeout_s") or 15), 30.0))
    if is_anthropic:                           # 与 build_agent 的 ClaudeAgent 路由保持一致
        url = base if base.endswith("/v1/messages") else base + "/v1/messages"
        payload: dict[str, Any] = {
            "model": model, "max_tokens": 16,
            "messages": [{"role": "user", "content": "reply exactly ok"}],
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8") or "{}")
            blocks = body.get("content") or []
            texts = [str(b.get("text") or "").strip() for b in blocks
                     if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
            sample = " ".join(t for t in texts if t).strip()
            return {"ok": True, "provider": "anthropic", "model": model, "sample": sample[:80]}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", errors="replace")[:400]
            return {"ok": False, "error": f"HTTP {e.code}: {msg}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if cfg.get("provider") == "kimi_coding" or "api.kimi.com/coding" in base:
        url = base if base.endswith("/messages") else base + "/messages"
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": "reply exactly ok"}],
            "max_tokens": 32,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": "KimiCLI/0.77",
            })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8") or "{}")
            blocks = body.get("content") or []
            texts = [str(b.get("text") or "").strip() for b in blocks
                     if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
            sample = " ".join(t for t in texts if t).strip()
            return {"ok": True, "provider": "kimi_coding", "model": model, "sample": sample[:80]}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", errors="replace")[:400]
            return {"ok": False, "error": f"HTTP {e.code}: {msg}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Reply with exactly: ok"},
            {"role": "user", "content": "ok"},
        ],
        "max_tokens": 2,
    }
    if cfg.get("temperature") is not None:
        payload["temperature"] = float(cfg["temperature"])
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8") or "{}")
        choice = ((body.get("choices") or [{}])[0] or {})
        content = ((choice.get("message") or {}).get("content") or "").strip()
        return {"ok": True, "provider": base, "model": model, "sample": content[:80]}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:400]
        return {"ok": False, "error": f"HTTP {e.code}: {msg}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class AutopilotController:
    """持有当前 session 的 store + runner;线程安全。单卡 → 单活动 session。"""

    def __init__(self, *, model: str, sim: bool = True,
                 session_dir: str | Path | None = None, step_delay_s: float = 1.2,
                 bridge_url: str | None = None) -> None:
        self._model = model
        self._sim = sim
        self._bridge_url = (bridge_url or "").rstrip("/")
        self._session_dir = Path(session_dir) if session_dir else None
        self._delay = step_delay_s
        self._lock = threading.Lock()
        self._store: SessionStore | None = None
        self._runner: Runner | None = None

    def _bridge(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._bridge_url + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(body or f"bridge HTTP {e.code}") from e

    def start(self, objective: dict, budget: dict | None, agent_cfg: dict | None) -> str:
        if self._bridge_url:
            r = self._bridge("POST", "/api/autopilot/start",
                             {"objective": objective, "budget": budget, "agent": agent_cfg})
            return str(r.get("session_id") or "")
        with self._lock:
            if self._store is not None and self._store.active():
                raise RuntimeError("a session is already running")  # → 409
            budget = budget or {"rounds": 12, "minutes": 30}
            sid = "ap-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            agent = build_agent(agent_cfg)  # 有 key → 真 LLM(ResilientAgent 兜底);否则 StubAgent
            path = self._session_dir / f"{sid}.jsonl" if self._session_dir else None
            store = SessionStore(path)
            store.new_session(sid, objective, budget, getattr(agent, "model", ""))
            sandbox = SimSandbox(self._model) if self._sim else None
            if sandbox is None:             # 真 GPU 沙盒是下一增量
                store.set_state("failed", "DockerSandbox 未启用(下一增量)")
                self._store = store
                return sid
            runner = Runner(store=store, sandbox=sandbox, agent=agent,
                            obj=build_objective(objective), budget=budget,
                            model=self._model, step_delay_s=self._delay)
            self._store, self._runner = store, runner
            runner.start()
            return sid

    def status(self) -> dict | None:
        if self._bridge_url:
            try:
                return self._bridge("GET", "/api/autopilot/status")
            except Exception:  # noqa: BLE001
                pass
        s = self._store
        if s is not None:
            return s.status_dict()
        # 没内存 session → 读 session_dir 最新 JSONL(host CLI 跑的真 session,跑完即见)
        if self._session_dir and self._session_dir.exists():
            files = sorted(self._session_dir.glob("*.jsonl"))   # sid=ap-时间戳 → 字典序即时序
            if files:
                return SessionStore.load_status(files[-1])
        return None

    def stop(self) -> bool:
        if self._bridge_url:
            try:
                return bool(self._bridge("POST", "/api/autopilot/stop").get("stopped"))
            except Exception:  # noqa: BLE001
                return False
        with self._lock:
            if self._runner is not None and self._store is not None and self._store.active():
                self._runner.stop()
                return True
            return False

    def test_agent(self, agent_cfg: dict | None) -> dict:
        if self._bridge_url:
            try:
                return self._bridge("POST", "/api/autopilot/agent-test", {"agent": agent_cfg})
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"bridge: {e}"}
        return test_agent_config(agent_cfg)


def register_autopilot_routes(app: Any, *, model: str, sim: bool = True,
                              session_dir: str | Path | None = None,
                              step_delay_s: float = 1.2,
                              bridge_url: str | None = None) -> AutopilotController:
    """把 /api/autopilot/* 挂到 FastAPI app;返回 controller(供测试/关停)。"""
    from fastapi import Body, HTTPException

    ctrl = AutopilotController(model=model, sim=sim, session_dir=session_dir,
                               step_delay_s=step_delay_s, bridge_url=bridge_url)

    @app.post("/api/autopilot/start")
    def ap_start(body: dict = Body(...)) -> dict:
        obj = body.get("objective") or {}
        try:
            sid = ctrl.start(obj, body.get("budget"), body.get("agent"))
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"session_id": sid}

    @app.get("/api/autopilot/status")
    def ap_status() -> dict:
        st = ctrl.status()
        return st or {"state": "idle", "rounds": []}

    @app.post("/api/autopilot/stop")
    def ap_stop() -> dict:
        return {"stopped": ctrl.stop()}

    @app.post("/api/autopilot/agent-test")
    def ap_agent_test(body: dict = Body(...)) -> dict:
        return ctrl.test_agent(body.get("agent"))

    return ctrl
