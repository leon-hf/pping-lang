"""bench scenarios — schema + execution strategies (static / dynamic).

v0.1 scope: StaticScenario only. Dynamic (ramp/spike/soak/trace) lands in Week 2.
See docs/bench-design-v0.1.md §7-8.
"""
from pping_lang.bench.scenarios.schema import SLO, ApiKind, StaticScenario

__all__ = ["StaticScenario", "SLO", "ApiKind"]
