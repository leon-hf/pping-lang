"""Session / Round 记录 —— UI 直播 + resume 底座(§9.4,G6)。

单 session(单卡现实):内存里保活当前 session 供 `/status` 2s 轮询;同时 append JSONL
做审计 + resume。round 记录字段齐全(诊断/证据/动作/scorecard before|after/score/decision)。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Round:
    round: int
    state_at_record: str = "deciding"
    ts_wall: str = ""
    kind: str = "candidate"                      # baseline | candidate | stop
    config_before: dict = field(default_factory=dict)
    config_after: dict = field(default_factory=dict)
    command: str = ""                            # 该轮 vllm serve 命令(UI 展示用)
    action: dict | None = None                   # {knob, from, to, flag}
    rationale: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    diagnosis: dict | None = None
    scorecard_before: dict | None = None
    scorecard_after: dict | None = None
    objective_score_before: float | None = None
    objective_score_after: float | None = None
    delta_pct: float | None = None
    decision: str = ""                           # baseline | kept | reverted | tie | done
    bench_spec: dict = field(default_factory=dict)
    agent_model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Session:
    session_id: str
    objective: dict
    budget: dict
    started_ts: str
    state: str = "idle"
    rounds: list[Round] = field(default_factory=list)
    best_round: int | None = None
    best_config: dict = field(default_factory=dict)
    best_score: float | None = None
    baseline_score: float | None = None
    recommended_command: str | None = None
    applies_to: dict = field(default_factory=dict)
    config_review: dict = field(default_factory=dict)
    promote_package: dict = field(default_factory=dict)
    agent_model: str = ""
    error: str | None = None
    resume_from_state: str | None = None

    def status_dict(self) -> dict:
        return {
            "session_id": self.session_id, "state": self.state,
            "objective": self.objective, "budget": self.budget,
            "rounds": [r.to_dict() for r in self.rounds],
            "best_round": self.best_round, "best": {
                "config": self.best_config, "score": self.best_score,
            },
            "baseline_score": self.baseline_score,
            "recommended_command": self.recommended_command,
            "applies_to": self.applies_to,
            "config_review": self.config_review,
            "promote_package": self.promote_package,
            "agent_model": self.agent_model, "error": self.error,
        }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class SessionStore:
    """单活动 session 的内存 + JSONL 持久化。线程安全(runner 在后台线程跑)。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._current: Session | None = None
        self._path = Path(path) if path else None
        self._fh = None
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self._path.open("a", encoding="utf-8")

    # --- session 生命周期 ---
    def new_session(self, session_id: str, objective: dict, budget: dict, agent_model: str = "") -> Session:
        with self._lock:
            self._current = Session(
                session_id=session_id, objective=objective, budget=budget,
                started_ts=_now_iso(), state="baselining", agent_model=agent_model,
            )
            self._write({"rec": "session_start", **self._current.status_dict()})
            return self._current

    def resume_session(self) -> Session | None:
        """从当前 JSONL path 恢复内存 session,供进程重启后继续跑。

        只恢复可审计字段;正在 applying/benchmarking 的候选由 Runner 解释为失败半轮。
        """
        if not self._path:
            return None
        st = self.load_status(self._path)
        if not st:
            return None
        round_fields = set(Round.__dataclass_fields__)
        rounds = [Round(**{k: v for k, v in r.items() if k in round_fields})
                  for r in st.get("rounds", [])]
        best = st.get("best") or {}
        with self._lock:
            old_state = st.get("state") or "idle"
            self._current = Session(
                session_id=st["session_id"],
                objective=st.get("objective") or {},
                budget=st.get("budget") or {},
                started_ts=st.get("started_ts") or _now_iso(),
                state=old_state,
                rounds=rounds,
                best_round=st.get("best_round"),
                best_config=best.get("config") or {},
                best_score=best.get("score"),
                baseline_score=st.get("baseline_score"),
                recommended_command=st.get("recommended_command"),
                applies_to=st.get("applies_to") or {},
                config_review=st.get("config_review") or {},
                promote_package=st.get("promote_package") or {},
                agent_model=st.get("agent_model", ""),
                error=st.get("error"),
                resume_from_state=old_state,
            )
            self._write({"rec": "session_state", "session_id": self._current.session_id,
                         "state": "resuming", "error": None})
            self._current.state = "resuming"
            return self._current

    @property
    def current(self) -> Session | None:
        return self._current

    def active(self) -> bool:
        s = self._current
        return s is not None and s.state not in ("done", "stopped", "failed")

    def set_state(self, state: str, error: str | None = None) -> None:
        with self._lock:
            if self._current:
                self._current.state = state
                if error:
                    self._current.error = error
                self._write({"rec": "session_state", "session_id": self._current.session_id,
                             "state": state, "error": error})

    def append_round(self, r: Round) -> None:
        with self._lock:
            if not self._current:
                return
            r.ts_wall = _now_iso()
            self._current.rounds.append(r)
            # 'rec' 作记录类型判别(不能用 'kind':会被 r.to_dict() 的 Round.kind 覆盖)
            self._write({"rec": "round", "session_id": self._current.session_id, **r.to_dict()})

    def update_best(self, round_idx: int, config: dict, score: float, command: str) -> None:
        with self._lock:
            if self._current:
                self._current.best_round = round_idx
                self._current.best_config = dict(config)
                self._current.best_score = score
                self._current.recommended_command = command

    def set_applies_to(self, applies_to: dict) -> None:
        with self._lock:
            if self._current:
                self._current.applies_to = dict(applies_to)

    def set_config_review(self, review: dict) -> None:
        with self._lock:
            if self._current:
                self._current.config_review = dict(review)

    def set_promote_package(self, package: dict) -> None:
        with self._lock:
            if self._current:
                self._current.promote_package = dict(package)

    def status_dict(self) -> dict | None:
        with self._lock:
            return self._current.status_dict() if self._current else None

    def _write(self, obj: dict) -> None:
        if self._fh:
            self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            if self._current:                            # 收尾写完整快照(含 best/recommended)
                self._write({"rec": "final", **self._current.status_dict()})
            self._fh.close()
            self._fh = None

    @staticmethod
    def load_status(path: str | Path) -> dict | None:
        """从 JSONL 重建 status_dict —— 给 dashboard 展示 CLI 跑完的真 session。

        优先用收尾的 final 完整快照;没 final(还在跑/崩了)则用 session_start + 回放 round。
        """
        path = Path(path)
        if not path.exists():
            return None
        final = start = None
        rounds: list[dict] = []
        state = error = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            rec = o.get("rec") or o.get("kind")          # 新格式 rec;旧格式回退 kind(round 行除外)
            if rec == "final":
                final = o
            elif rec == "session_start":
                start = o
            elif rec == "session_state":
                state, error = o.get("state"), o.get("error")
            elif rec == "round":                          # 只新格式 round 行有 rec='round';保留其自带 kind
                rounds.append({kk: vv for kk, vv in o.items() if kk not in ("rec", "session_id")})
        if final:                                        # final/start = status_dict 快照,session_id 是真数据
            return {kk: vv for kk, vv in final.items() if kk != "rec"}
        if not start:
            return None
        st = {kk: vv for kk, vv in start.items() if kk != "rec"}
        st["rounds"] = rounds
        if state:
            st["state"], st["error"] = state, error
        return st
