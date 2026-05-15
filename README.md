# pping-lang

> vLLM 性能诊断插件——把 vLLM 指标 + GPU 物理层数据喂给规则引擎，自动告诉你为什么慢。

**状态**：Pre-alpha (`v0.0.1.dev0`)，目标 v0.1 在 3 周内发布（当前 Week 3）。
**License**：Apache 2.0

---

## 它能告诉你什么

```
[pping-lang] [!] WARNING: CUDA graph padding 过高
  CUDA graph padding 比例 70%, 约 70% 的 GPU 算力浪费在补 0
  -> 调小 max_num_seqs 或开启更细粒度的 cudagraph capture（PIECEWISE 模式）

[pping-lang] [!] WARNING: MFU 偏低（计算资源浪费）
  MFU = 5% < 20%（理论峰值的小部分都没跑到）
  -> 检查 padding ratio / batch 大小 / dtype（应为 bf16/fp16）
```

不仅说"GPU 利用率低"——直接告诉你 **GPU 在补 0**、**MFU 只有 5%**、改 **`max_num_seqs`** 应该到 384。

## v0.1 能力清单

| 维度 | v0.1 |
|---|---|
| **采集** | NVML（util/mem/power/clock/temp） + vLLM SchedulerStats / IterationStats / FinishedRequestStats / cudagraph_stats / perf_stats |
| **派生指标** | CUDA padding_ratio、MFU、内存带宽利用率、prefix cache hit ratio |
| **诊断** | 11 条内置规则覆盖 throughput / latency / stability / efficiency / bottleneck，触发即给可执行建议 |
| **规则编辑** | Web UI + REST CRUD + 实时预览（不重启 vLLM 即时生效） |
| **报告** | 单 HTML 文件含 Executive Summary / 关键问题 / 趋势图 / 配置审计 / Roofline 散点 / 规则汇总，邮件可分享 |
| **OTel 输出** | OTLP gRPC，metric 名 `pping_lang.<domain>.<name>_<unit>`，含 engine_idx + gpu_idx attrs |
| **部署** | Embedded（v0.1 唯一），Sidecar/Centralized v0.2，Stateless v0.3 |

## 装

```bash
pip install pping-lang
vllm serve <your-model>
# 启动日志会打印：
#   [pping-lang] dashboard at http://localhost:8765
```

打开 dashboard 即可看实时指标 + 触发的诊断卡片。

## 试跑（无需 GPU/vLLM）

合成 stats 喂 marquee 诊断流程，验证安装：

```bash
python examples/embedded/demo.py
```

约 7 秒后看到 `high-cudagraph-padding` + `low-mfu` 两条诊断打印到 stderr，dashboard 同时可访问 (http://localhost:8765)。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PPING_LANG_DB_PATH` | `~/.pping-lang/local.duckdb` | DuckDB 数据库路径 |
| `PPING_LANG_INSTANCE_ID` | `local-{engine_index}` | 出站 metric 的 instance_id |
| `PPING_LANG_API_HOST` | `127.0.0.1` | API bind host（容器场景设 `0.0.0.0`） |
| `PPING_LANG_API_PORT` | `8765` | dashboard 端口 |
| `PPING_LANG_FLUSH_INTERVAL_S` | `5.0` | Sink 批量写 DuckDB 间隔 |
| `PPING_LANG_NVML_INTERVAL_S` | `0.1` | NVML 采样间隔 |
| `PPING_LANG_RULE_EVAL_INTERVAL_S` | `1.0` | 规则评估间隔 |
| `PPING_LANG_RULES_PATH` | — | 用户规则 JSON 文件（覆盖默认 by id） |
| `PPING_LANG_DISABLE_NVML` | `0` | 设 `1` 关闭 NVML 采样 |
| `PPING_LANG_DISABLE_RULES` | `0` | 设 `1` 关闭规则引擎 |
| `PPING_LANG_DISABLE_API` | `0` | 设 `1` 关闭 HTTP API |
| `PPING_LANG_DISABLE_OTEL` | `0` | 设 `1` 即使有 OTLP endpoint 也强制关 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTel 标准 env，设了即开 OTel 出口 |

## 架构

```
┌─────────────────────────────────────────────────────┐
│ vLLM EngineCore 进程 (Embedded 模式)                 │
│                                                     │
│  PpingLangStatLogger (vllm.stat_logger_plugins)    │
│    ├── VllmStatsCollector ── 派生 padding_ratio /   │
│    │                        mfu / bw_util          │
│    ├── NvmlSampler ── 100ms GPU 采样                │
│    │                                                │
│    ├── Sink → LocalSink (DuckDB)                    │
│    │       └─ TeeSink → OTelSink (可选)             │
│    │                                                │
│    ├── RuleEngine ── 1s SQL 评估，hot-reload 规则    │
│    │                                                │
│    └── ApiServer ── FastAPI + 单文件 HTML dashboard │
└─────────────────────────────────────────────────────┘
```

完整架构 + 部署模式见 [docs/pping-lang-design-v0.2.md](docs/pping-lang-design-v0.2.md)。

## 文档

- [设计文档 v0.2.1](docs/pping-lang-design-v0.2.md) — 4 种部署模式、Sink 抽象、用户故事覆盖
- [Pre-implementation RFC](docs/pping-lang-pre-impl-rfc.md) — 5 项编码前决策
- [Case study #1: GPU 在补 0](docs/case-study-1-padding.md) — 一个完整诊断流程

## 开发

```bash
git clone https://github.com/leon/pping-lang.git
cd pping-lang
pip install -e ".[dev]"
pytest                              # 209 tests, ~28s
python -m pping_lang.bench.microbench   # 热路径耗时基准
```

## License

Apache 2.0
