"""Rules — 微型 DSL + 引擎。

子模块：
- schema:   Rule, Condition, validate_rule
- defaults: DEFAULT_RULES (10 条 v0.1 内置)
- loader:   load_rules_from_file, get_active_rules
- engine:   RuleEngine — 周期 SQL 评估 + 终端打印
"""
from pping_lang.rules.defaults import DEFAULT_RULES
from pping_lang.rules.engine import RuleEngine
from pping_lang.rules.loader import get_active_rules, load_rules_from_file
from pping_lang.rules.schema import Condition, Rule, validate_rule

__all__ = [
    "Rule",
    "Condition",
    "validate_rule",
    "DEFAULT_RULES",
    "RuleEngine",
    "get_active_rules",
    "load_rules_from_file",
]
