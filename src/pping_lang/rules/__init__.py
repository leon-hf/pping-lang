"""Rules — 微型 DSL + 事实规则诊断引擎 + CRUD store。

子模块：
- schema:           Rule, Condition, validate_rule
- defaults:         DEFAULT_RULES (10 条 v0.1 内置)
- loader:           load_rules_from_file, get_active_rules
- diagnosis_*:      DiagnosisEngine — 纯内存环评估的事实规则引擎(现役)
- store:            RuleStore — defaults + user JSON overrides 持久化
"""
from pping_lang.rules.defaults import DEFAULT_RULES
from pping_lang.rules.loader import get_active_rules, load_rules_from_file
from pping_lang.rules.schema import Condition, Rule, validate_rule
from pping_lang.rules.store import RuleStore

__all__ = [
    "Rule",
    "Condition",
    "validate_rule",
    "DEFAULT_RULES",
    "RuleStore",
    "get_active_rules",
    "load_rules_from_file",
]
