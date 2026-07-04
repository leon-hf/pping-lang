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

from pping_lang.autopilot.agent import ClaudeAgent, OpenAIAgent, ResilientAgent, StubAgent
from pping_lang.autopilot.objective import SLA, Floor, ObjectiveSpec
from pping_lang.autopilot.runner import Runner
from pping_lang.autopilot.sandbox import SimSandbox
from pping_lang.autopilot.session_store import SessionStore


def build_agent(cfg: dict | None):
    """可插拔(§8,G3 默认 Claude):
    - provider=anthropic 或 base_url 含 anthropic → ClaudeAgent;
    - 有 base_url+key+model(OpenAI 兼容:DeepSeek/OpenRouter/本地)→ OpenAIAgent;
    - 否则纯 StubAgent。真 LLM 一律用 ResilientAgent 兜底 StubAgent(网络/解析失败不死)。"""
    cfg = cfg or {}
    key, base, model = cfg.get("api_key"), cfg.get("base_url"), cfg.get("model")
    common = dict(guidance=cfg.get("guidance", ""), temperature=float(cfg.get("temperature", 0.4)),
                  timeout_s=float(cfg.get("timeout_s", 30)))
    primary = None
    if key and (cfg.get("provider") == "anthropic" or (base and "anthropic" in base)):
        primary = ClaudeAgent(api_key=key, model=model or "claude-opus-4", **common)
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
            budget = budget or {"rounds": 6, "minutes": 15}
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

    return ctrl
