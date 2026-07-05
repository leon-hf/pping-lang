"""UI 静态资产测试 — / 路由返回单文件 HTML。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.rules.store import RuleStore
from pping_lang.sink.local import LocalSink


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "ui.duckdb"
    sink = LocalSink(db_path=db, instance_id="ui-test", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db), instance_id="ui-test", engine_index=0,
        sink=sink, rule_store=RuleStore(),
    )
    yield TestClient(app)
    sink.close()


def test_root_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "<!DOCTYPE html>" in body
    assert "pping-lang" in body


def test_root_contains_required_libs(client):
    body = client.get("/").text
    # vendor 本地化:Alpine/Chart 走 /vendor/ 而非 CDN,离线/air-gapped 主机也能渲染
    assert "alpine.min.js" in body.lower()
    assert "chart.umd" in body.lower()
    assert "jsdelivr" not in body.lower() and "cdn." not in body.lower()


def test_vendor_assets_served(client):
    """vendor JS 本地服务可用(C:不再依赖 CDN)。"""
    alp = client.get("/vendor/alpine.min.js")
    assert alp.status_code == 200 and "javascript" in alp.headers["content-type"]
    assert len(alp.text) > 1000
    chart = client.get("/vendor/chart.umd.min.js")
    assert chart.status_code == 200 and "javascript" in chart.headers["content-type"]
    assert len(chart.text) > 1000


def test_root_references_known_api_endpoints(client):
    """UI's JS should fetch the endpoints we ship.

    KPI/Roofline/latency_trends 出来后聚合工作移到 server 端，前端不再直接
    call snapshot/recent；指标名也在 server 端组装好。
    """
    body = client.get("/").text + client.get("/dashboard.js").text  # UI 拆成 html+js
    for endpoint in [
        "/api/health",
        "/api/system",
        "/api/kpis",
        "/api/roofline",
        "/api/latency_trends",
        "/api/diagnoses",
    ]:
        assert endpoint in body, f"UI does not reference {endpoint}"


def test_autopilot_cold_open_ignores_terminal_history(client):
    """打开 Autopilot tab 时,历史 done JSONL 不应伪装成刚跑完的当前 session。"""
    js = client.get("/dashboard.js").text
    assert "term && !this.session && !this.running" in js
    assert "latest completed JSONL" in js


def test_autopilot_start_button_uses_execute_label(client):
    js = client.get("/dashboard.js").text
    assert "return '执行调优'" in js
    assert "重新调优" not in js
    assert "重新真实调优" not in js


def test_autopilot_budget_is_max_rounds_not_fixed_six(client):
    body = client.get("/").text + client.get("/dashboard.js").text
    assert "budget: { rounds: 12, minutes: 30 }" in body
    assert "Number(this.budget.rounds) || 12" in body
    assert "最多 <input" in body
    assert "已评估" in body and "最多 <span x-text=\"budget.rounds\"></span> 轮" in body


def test_autopilot_agent_presets_include_kimi(client):
    body = client.get("/").text + client.get("/dashboard.js").text
    assert 'value="kimi"' in body
    assert "Kimi / Moonshot" in body
    assert "https://api.moonshot.ai/v1" in body
    assert "kimi-k2.6" in body
    assert "temperature: 0.6" in body
    assert 'value="kimi_coding"' in body
    assert "Kimi Coding" in body
    assert "https://api.kimi.com/coding/v1" in body
    assert "kimi-for-coding" in body
    assert "provider: 'kimi_coding'" in body


def test_autopilot_agent_test_calls_backend_probe(client):
    body = client.get("/").text + client.get("/dashboard.js").text
    assert "/api/autopilot/agent-test" in body
    assert "连接中…" in body
    assert "✓ 连接可用" in body
    assert "配置已填" not in body


def test_autopilot_start_tracks_session_id_to_avoid_stale_completed_status(client):
    js = client.get("/dashboard.js").text
    assert "activeSessionId" in js
    assert "out.session_id" in js
    assert "s.session_id !== this.activeSessionId" in js
    assert "正在创建 session 并准备基线压测" in js


def test_autopilot_running_feedback_uses_structured_events(client):
    body = client.get("/").text + client.get("/dashboard.js").text + client.get("/dashboard.css").text
    assert "_pendingFromStatus" in body
    assert "s.events" in body
    assert "bench ${Math.min(elapsed, total)}s" in body
    assert "ap-events" in body
    assert "ap-event" in body
    assert "状态心跳中，未改线上 serve" in body
    assert "pendingPhase" not in body


def test_autopilot_treats_stopped_bridge_as_terminal(client):
    js = client.get("/dashboard.js").text
    assert "bridgeStopped" in js
    assert "bridge.running === false" in js
    assert "const state = bridgeStopped ? 'stopped' : s.state" in js


def test_root_references_marquee_kpi_labels(client):
    """Dashboard 必须把 marquee KPI 标签暴露给用户。

    i18n 后:标签文本搬进 dashboard.js 的 I18N 字典,HTML 用 t('key') 引用。
    所以查 html + js 合起来(同 test_rules_tab 的做法)。
    """
    body = client.get("/").text + client.get("/dashboard.js").text
    for label in ["TTFT", "TPOT", "MFU", "KV cache", "padding", "Roofline"]:
        assert label in body, f"UI missing marquee KPI label {label!r}"


def test_ui_assets_split_and_under_budget():
    """CSS/JS 已拆出单文件。index.html 只剩 markup,预算 100KB;
    CSS/JS 各自有独立预算。再大就该上构建/压缩。

    预算 90KB→100KB→110KB→120KB:Kernel tab 扩成完整诊断面板;规则 tab 重做成
    策展事实规则只读 + 中心配置弹框 + 自定义规则 CRUD(列表 + 新建/编辑弹框),
    markup 实打实变多。once-load、gzip 后才十几 KB,为这点体积上构建工具不划算。

    i18n(中/英):dashboard.js 多了双语字典 I18N(每串两份),index.html 的中文文本
    换成 `<span x-text="t('key')">`(比裸文本长)。全站 i18n 后字典 ~280 键×2 语言:
    js 95KB→135KB;index.html 反而变小(中文文本被 t('key') 取代)。
    gzip 后仍十几 KB(双语字典压缩比高),拆 i18n.js 多一个路由不划算。"""
    ui = Path(__file__).parent.parent / "src" / "pping_lang" / "ui"
    html = (ui / "index.html").stat().st_size
    css = (ui / "dashboard.css").stat().st_size
    js = (ui / "dashboard.js").stat().st_size
    assert html < 140_000, f"index.html is {html} bytes, exceeds 140KB"
    # Autopilot 预览 tab 接入(.ap-* 一整套样式)后过了 70KB → 抬到 90KB;gzip 后仍十几 KB。
    assert css < 90_000, f"dashboard.css is {css} bytes, exceeds 90KB"
    # 双语规则 i18n(中/英各一份)随诊断规则增删而长;0.21 处方更新后过了 135KB。
    # 根因是 rule.* 文案在 Python 与前端 i18n 各存一份(漂移源);彻底瘦身要把 ruleI18n
    # 改成"优先用 API 值、不走 en 兜底"再删中文那份(t() 现在 I18N[lang]||I18N.en||key,
    # 直接删中文会让中文显示英文)。在那之前抬到 140KB,gzip 后仍十几 KB。
    # Autopilot 预览 tab 的 autopilotTab()(mock 轨迹 + 脚本播放)过了 140KB → 抬到 155KB。
    assert js < 155_000, f"dashboard.js is {js} bytes, exceeds 155KB"


def test_css_and_js_served(client):
    """拆出的 CSS/JS 由路由提供,content-type 正确。"""
    css = client.get("/dashboard.css")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert ".kfinding" in css.text or ".tl-block" in css.text
    js = client.get("/dashboard.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "function dashboard()" in js.text


def test_index_references_split_assets(client):
    body = client.get("/").text
    assert "/dashboard.css" in body and "/dashboard.js" in body


def test_rules_tab_references_diagnosis_endpoints(client):
    """诊断 tab 只读 4 瓶颈命中;首页 SLO 面板 PUT 中心配置。自定义规则/旧 CRUD 已删除。"""
    body = client.get("/").text + client.get("/dashboard.js").text  # UI 拆成 html+js
    assert "/api/diagnosis_rules" in body            # 读 4 瓶颈规则定义(诊断详情/条件)
    assert "/api/diagnosis_config" in body           # PUT 改中心 SLA/阈值(首页 SLO 面板)
    assert "saveConfig" in body
    # 自定义规则概念彻底删除(无 CRUD 端点、无编辑函数调用)
    assert "/api/diagnosis_rules/custom" not in body
    assert "newRule(" not in body and "saveRule(" not in body and "deleteRule(" not in body
    assert "/api/rules/" not in body                 # 旧 RuleStore CRUD 也不在


def test_home_slo_panel_and_diagnosis_table(client):
    """首页 SLO 面板(业务形态 + SLA + 达标)+ 诊断 tab 4 瓶颈密集表。"""
    body = client.get("/").text
    # 首页 SLO 面板 —— 配置已从诊断 tab 搬到首页
    assert "sloPanel()" in body
    assert "cfg-bar" in body                          # 配置条容器
    assert "cfgDraft.workload_form" in body           # 业务形态选择
    assert "cfgDraft.sla_ttft_p99_ms" in body         # SLA
    assert "advancedOpen" in body                     # 高级阈值折叠开关
    assert "advKeys()" in body and "advLabels" in body  # 高级阈值网格(只放 4 瓶颈阈值)
    assert "slaPass(" in body                         # 达标判定(当前 p99 vs SLA)
    # 诊断 tab = 内置 4 瓶颈目录(detector-group):每瓶颈多条跨层检测手段,命中即高亮(几条互证)
    assert "rules.builtinTitle" in body and "rules.colMethods" in body
    for token in ["ruleName(r.id)", "ruleHyp(r)", "ruleSug(r)", "firedFor(r.id, diagnoses)",
                  "r.detectors", "detFired(det", "detCond(det)", "hitCount(r, diagnoses)",
                  "t('layer.'+det.layer)", "toggleDiag"]:
        assert token in body, f"diagnosis catalog missing {token}"


def test_i18n_framework(client):
    """i18n:dashboard.js 带中英字典 + 全局 t();index.html 有语言切换且用 t() 渲染。"""
    html = client.get("/").text
    js = client.get("/dashboard.js").text
    # 语言切换控件 + 写 store
    assert "lang-toggle" in html
    assert "$store.i18n.lang" in html
    # 关键串走 t()
    assert "t('nav.live')" in html
    assert "t('slo.form')" in html
    # 字典 + 全局 t() + 中英两套
    assert "const I18N" in js and "window.t" in js
    assert "'nav.live': 'Live'" in js and "'nav.live': '实时'" in js


def test_autopilot_promote_package_ui(client):
    body = client.get("/").text + client.get("/dashboard.js").text
    assert "promote_package" in body
    assert "查看上线包" in body
    assert "production_command" in body
    assert "rollback_command" in body
    assert "人工确认清单" in body


def test_autopilot_start_button_targets_real_bridge(client):
    body = client.get("/").text + client.get("/dashboard.js").text
    assert "startLabel()" in body
    assert "执行调优" in body
    assert "host bridge 未连接" in body
    assert "真实调优需要先配置 LLM agent" in body
    assert "host bridge 状态不可达" in body
    assert "StubAgent 只用于本地 SimSandbox/dev tests" in body
    assert ":8776" in body
