# Changelog

按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，遵循 SemVer。

## [Unreleased] — v0.1 collecting (2026-05)

完成第 1-3 周开发，准备发首个 alpha tag。

### Added

**采集与派生**（Day 1-3）
- vLLM `stat_logger_plugins` entry point + `PpingLangStatLogger`
- NVML 周期采样（util / mem / power / temp / sm/mem clock，按 GPU index 区分）
- vLLM SchedulerStats / IterationStats / FinishedRequestStats 完整字段映射
- cudagraph_stats / perf_stats 派生指标：**`padding_ratio`** / **`mfu_ratio`** / **`mem_bw_util_ratio`**
- 16 款常见 GPU 的 BF16 peak FLOPs + 内存带宽常量表（hardware.py）

**诊断**（Day 4 / Day 9）
- 微型 DSL（10 字段 Rule + Condition），加载时校验 metric ∈ catalog
- 10 条内置规则覆盖 6 个 category，含 marquee `high-cudagraph-padding` + `low-mfu`
- 周期 SQL 评估（DuckDB QUANTILE_CONT 支持 p50/p95/p99）
- 触发即终端打印 + 推 Diagnosis 到 sink
- 抑制窗口避免重复告警
- **规则热加载**：CRUD 后下次评估即生效，无需重启 vLLM

**Sink 与存储**（Day 2 / Day 11）
- Fire-and-forget Sink ABC，push <5μs（实测 156ns）
- LocalSink → DuckDB（schema 含 instance_id + gpu_idx）
- TeeSink fan-out 多 sink 并联
- OTelSink → OTLP gRPC（metric 名 `pping_lang.<name>`，含 engine_idx + gpu_idx attrs）

**HTTP API + UI**（Day 6-8）
- 14 个端点：health / metrics (available/recent/snapshot) / diagnoses / rules CRUD / rule test / report / instances
- 单文件 HTML dashboard（Alpine.js + Chart.js via CDN，~12KB）
- 三 tab：实时（KPI + chart + 诊断卡片）、规则（CRUD 表单 + inline 测试）、报告

**报告**（Day 10）
- 单 HTML 文件含 Executive Summary / 关键问题 / 趋势 / 配置审计 / Roofline / 规则汇总
- Plotly via CDN 默认（~30KB），inline 选项（~3.5MB 邮件离线）
- Roofline 散点 + 理论上界折线（compute roof + memory bandwidth roof）
- 配置审计含 4 条启发式建议（batch / prefix cache / KV / padding）

**性能**（Day 12）
- 微基准：push_metric 156ns mean，collect 22μs，record 12μs
- CI regression test 守 hot path 预算
- 自我观测指标：`pping_lang.overhead.record_us` / `rule_eval_ms` / `sink.dropped_total`

### Changed
- 项目命名从 `llmprof` 改为 `pping-lang`（PyPI 名带连字符，Python 模块带下划线）

### Tests
- 209 测试，3 周内 15 commits 累积
- 命名规范守卫、字段映射全覆盖、API CRUD 边界、e2e marquee 流程、性能 regression

---

模板（未来 release）：

## [0.x.0] — YYYY-MM-DD

### Added
### Changed
### Deprecated
### Removed
### Fixed
### Security
