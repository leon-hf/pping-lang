"""RuleStore — defaults + user overrides JSON 持久化。

defaults 始终来自 DEFAULT_RULES（代码内）。
user overrides 持久化到 JSON 文件。merge 时 user 按 id 覆盖 defaults。

删除语义：
- 删 user-only 规则：从 user dict 移除
- 删 default 规则：在 user dict 存一条 enabled=False 的同 id 规则
  （用户可以随时通过 PUT 重新启用）
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pping_lang.rules.defaults import DEFAULT_RULES
from pping_lang.rules.loader import load_rules_from_file
from pping_lang.rules.schema import Rule, validate_rule

logger = logging.getLogger(__name__)


class RuleStore:
    """In-memory rule registry backed by an optional JSON override file."""

    def __init__(self, override_path: Path | str | None = None) -> None:
        self._path: Path | None = Path(override_path) if override_path else None
        self._defaults: dict[str, Rule] = {r.id: r for r in DEFAULT_RULES}
        self._user: dict[str, Rule] = {}
        if self._path and self._path.exists():
            try:
                rules = load_rules_from_file(self._path)
                self._user = {r.id: r for r in rules}
                logger.info(
                    "[pping-lang] loaded %d user rule overrides from %s",
                    len(self._user), self._path,
                )
            except Exception as e:
                logger.warning(
                    "[pping-lang] failed to load %s, ignoring: %s", self._path, e,
                )

    @property
    def override_path(self) -> Path | None:
        return self._path

    def list(self) -> list[Rule]:
        merged = dict(self._defaults)
        merged.update(self._user)  # user wins by id
        return list(merged.values())

    def get(self, rule_id: str) -> Rule | None:
        if rule_id in self._user:
            return self._user[rule_id]
        return self._defaults.get(rule_id)

    def is_default(self, rule_id: str) -> bool:
        return rule_id in self._defaults

    def upsert(self, rule: Rule) -> None:
        """Create or update a rule. Persists to override file if configured."""
        validate_rule(rule)
        self._user[rule.id] = rule
        self._persist()

    def delete(self, rule_id: str) -> None:
        """Delete user-only rules; soft-disable defaults (kept as enabled=False override).

        Raises KeyError if rule_id is unknown.
        """
        if rule_id not in self._defaults and rule_id not in self._user:
            raise KeyError(rule_id)
        if self.is_default(rule_id):
            base = self._defaults[rule_id]
            self._user[rule_id] = Rule(
                id=base.id, name=base.name, severity=base.severity,
                category=base.category, condition=base.condition,
                message=base.message, suggestion=base.suggestion,
                enabled=False,
            )
        elif rule_id in self._user:
            del self._user[rule_id]
        self._persist()

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [_rule_to_dict(r) for r in self._user.values()]
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _rule_to_dict(r: Rule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "severity": r.severity,
        "category": r.category,
        "enabled": r.enabled,
        "condition": {
            "metric": r.condition.metric,
            "op": r.condition.op,
            "threshold": r.condition.threshold,
            "window_seconds": r.condition.window_seconds,
            "aggregation": r.condition.aggregation,
        },
        "message": r.message,
        "suggestion": r.suggestion,
    }
