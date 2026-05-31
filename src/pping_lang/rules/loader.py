"""规则加载 — 内置 defaults + 可选用户 overrides 文件 (JSON)。

环境变量：
- PPING_LANG_RULES_PATH: 指向 JSON 文件，加载后追加到 defaults 之后
  （同 id 时用户的覆盖默认）
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pping_lang.rules.defaults import DEFAULT_RULES
from pping_lang.rules.schema import Condition, Rule, validate_rule

logger = logging.getLogger(__name__)


def load_rules_from_file(path: Path) -> list[Rule]:
    """Load rules from a JSON file. Format: list of rule dicts matching schema."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"rules file {path} must contain a JSON list")
    rules: list[Rule] = []
    for i, item in enumerate(raw):
        try:
            cond_dict = item["condition"]
            cond = Condition(
                metric=cond_dict["metric"],
                op=cond_dict["op"],
                threshold=float(cond_dict["threshold"]),
                window_seconds=int(cond_dict["window_seconds"]),
                aggregation=cond_dict.get("aggregation", "avg"),
            )
            rule = Rule(
                id=item["id"],
                name=item["name"],
                severity=item["severity"],
                category=item["category"],
                condition=cond,
                message=item["message"],
                suggestion=item["suggestion"],
                enabled=item.get("enabled", True),
            )
            validate_rule(rule)
            rules.append(rule)
        except (KeyError, ValueError, TypeError) as e:
            raise ValueError(f"rules file {path}: rule[{i}] invalid: {e}") from e
    return rules


def get_active_rules() -> list[Rule]:
    """Defaults merged with PPING_LANG_RULES_PATH overrides (by rule id)."""
    rules: dict[str, Rule] = {r.id: r for r in DEFAULT_RULES}
    override_path = os.environ.get("PPING_LANG_RULES_PATH")
    if override_path:
        try:
            for r in load_rules_from_file(Path(override_path)):
                rules[r.id] = r
            logger.info(
                "[pping-lang] loaded %d rule overrides from %s",
                len(rules), override_path,
            )
        except Exception as e:
            logger.warning(
                "[pping-lang] failed to load %s, using defaults only: %s",
                override_path, e,
            )
    return list(rules.values())
