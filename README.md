<div align="center">

# pping-lang

**vLLM 性能诊断插件 —— 实时指标采集、规则化分析、结构化建议**

[![PyPI](https://img.shields.io/pypi/v/pping-lang?color=4c8bf5&label=PyPI)](https://pypi.org/project/pping-lang/)
[![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-4c8bf5)](https://pypi.org/project/pping-lang/)
[![License](https://img.shields.io/badge/license-Apache%202.0-43a047)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--alpha-fb8c00)](#项目状态)
[![Tests](https://img.shields.io/badge/tests-309%20passing-43a047)](tests/)

[快速上手](#快速上手) · [仪表盘](#仪表盘) · [兼容性](#兼容性) · [架构](#架构) · [路线图](#路线图)

</div>

---

## Latest News

- **2026-06** —— Dual-path 实时读取架构：实时面板从 DuckDB SQL 路径迁移至内存 ring buffer，bench 启动后 KPI 可见性延迟由 20s 降至 2s
- **2026-06** —— Roofline analytical fallback：在缺失 `perf_stats` 的 vLLM 版本上，依据 `FLOPS ≈ 2·params·tokens` 从 iter 级 token 计数和模型参数量推算 arithmetic intensity 与 throughput，并附区域着色 + 自动结论卡（memory-bound / compute-bound 判定及对应优化路径）
- **2026-06** —— 延迟指标改为多统计量并报：TTFT / TPOT 同时披露 p50 / p95 / p99 / avg 及请求数，避免单一百分位对偏态分布的失真
- **2026-05** —— 内置 bench 模块：dashboard 直接发起静态压测，含三个标准 prompt 数据集（短问答 / 长文档 / 代码）
- **2026-05** —— `v0.1.0a1` 发布至 PyPI

---

## 概述

vLLM 通过 `stat_logger_plugins` 入口暴露完整的运行时指标（SchedulerStats、IterationStats、cudagraph / perf 派生量），其消费方式通常为 Prometheus 抓取 + Grafana 可视化。该方案能展示指标但不输出决策结论，存在两个具体问题：

1. **指标语义模糊**。`GPU utilization` 反映的是 SM duty cycle 而非吞吐量。在 LLM decode 阶段，该值常稳定于 70–90% 而 MFU 不足 5%，原因为 memory-bound。仅看 utilization 数字无法识别此类瓶颈
2. **缺乏可操作性**。规则触发、阈值告警、根因关联需要使用方自行实现

pping-lang 直接消费 `stat_logger_plugins` 回调，结合 NVML 物理层采样，通过规则引擎输出结构化诊断与优化路径。示例输出：

```text
[pping-lang] WARNING  GPU 利用率偏低
  GPU 平均利用率 3% 持续低于 50% 已 30s
  建议：检查 batch 是否退化为 1，或开启连续 batching

[pping-lang] WARNING  batch 退化
  并发请求数 1.0 ≤ 1.0 已 30s
  建议：增加客户端并发，或检查上游路由是否串行化
```

Roofline 视图附带自动结论：

```text
当前结论  Memory-bound（decode 阶段的典型状态）
  算力利用    1%   （0.40 / 33 TFLOPS）
  带宽利用   52%   （132 / 256 GB/s）

优化路径
  · 增大 batch 直至 KV cache 占用接近 80%
  · 启用 speculative decoding
  · 权重量化 (AWQ / GPTQ)
  · 升级带宽更高的 GPU
```

---

## 快速上手

### 离线 demo（无需 GPU / vLLM）

```bash
pip install pping-lang
python -m examples.embedded.demo
```

脚本注入合成指标，约 7 秒后终端打印诊断，dashboard 可访问 `http://localhost:8765`。

### 接入 vLLM

```bash
pip install pping-lang
vllm serve <model>
```

vLLM 启动日志将输出 dashboard 地址：

```
[pping-lang] dashboard → http://localhost:8765
```

无需修改任何 vLLM 启动参数。

---

## 仪表盘

单页应用，单文件 HTML，无需前端构建工具。三个标签页：

| 标签页 | 内容 |
|:--|:--|
| 实时 | 12 项 KPI（TTFT / TPOT / 吞吐 / KV cache / 队列状态 / MFU / GPU 利用 / 显存 / Prefix cache / Padding / 抢占率）；Roofline 散点 + 自动结论；TTFT / TPOT / E2E 时序图。每项 KPI 支持 hover 查看公式与解读 |
| 规则 | 阈值与 condition 可视化编辑；「测试当前数据」按钮预览触发结果；保存后热加载，不需要重启 vLLM |
| 压测 | 内置 OpenAI 协议静态压测器，配置 endpoint / 调用名 / 并发 / 时长 / prompt 来源，输出 client-side TTFT / TPOT / E2E 分布及 SLO 校验 |

实时数据从 Sink 的内存 ring buffer 直读，延迟约等于轮询间隔。

---

## 兼容性

### vLLM 版本

| vLLM | 状态 | SchedulerStats | IterationStats | cudagraph_stats | perf_stats |
|:--|:--|:--:|:--:|:--:|:--:|
| 0.20+ | 推荐 | ✓ | ✓ | ✓ | ✓ |
| 0.13.x | 支持 | ✓ | ✓ | ✓（字段不同） | ✗ |
| < 0.13 | 不支持 | — | — | — | — |

`perf_stats` 是 MFU、显存带宽利用率与实测 Roofline 的数据源，仅 0.20+ 提供。在 0.13.x 上：

- MFU、padding ratio KPI 显示为空（不进行不可靠的构造）
- Roofline 自动切换至 analytical 模式，绝对值存在约 ±20% 误差；卡片标识数据来源
- 其余功能不受影响：TTFT / TPOT / E2E 完整分布、KV cache、prefix cache 命中、preemption、NVML 全套采样、规则诊断

### 运行环境

- Linux：原生支持
- Windows：需通过 WSL2 + Ubuntu。跨子系统访问 dashboard 时需设置 `PPING_LANG_API_HOST=0.0.0.0`

### 已识别 GPU 列表

NVML 设备名 → BF16 peak (TFLOPs/s) 与显存带宽 (GB/s)。未识别的 GPU 不影响指标采集，仅跳过依赖峰值的派生量。

```
Blackwell        B200 (2250 / 8000) · B100 (1800 / 8000)
Hopper           H200 (989 / 4800) · H100 SXM/PCIe/NVL · A100 SXM/PCIe
Ada 数据中心      L40S · L40 · L4
Ada 桌面 / 移动   RTX 4090 / 4080 / 4070 Ti / 4070 / 4060 Ti / 4060 (含 Laptop GPU)
旧代             A30 · A10G · A10 · V100 · T4 · RTX 3090
```

补充设备：在 [`src/pping_lang/hardware.py`](src/pping_lang/hardware.py) 的 `_GPU_PEAK_TABLE` 添加条目。

---

## 性能

### 热路径开销

| 项目 | 实测 |
|:--|:--|
| `push_metric()` 单次 | <5 μs |
| `record()` 单次（含 collector 解析） | ≈100 μs |
| Sink bg flush 线程 CPU | <1% |
| 常驻内存 | ≈6 MB |

### 基准：Qwen2.5-0.5B-Instruct / RTX 4070 Laptop / WSL2 / vLLM 0.13.0

bench concurrency=3，时长 20s：

| 指标 | 值 | 数据来源 |
|:--|:--|:--|
| TTFT p99 | 305 ms | client-side |
| TPOT p99 | 22 ms | client-side |
| Output throughput | 28 tok/s | `vllm.iter.gen_tokens` |
| 单请求 decode 速度 | 138 tok/s | 1000 / TPOT p50 |
| 算力利用 | 1.2% | analytical Roofline |
| 带宽利用 | 51.5% (132 / 256 GB/s) | analytical Roofline |
| Bound 判定 | memory-bound | 中位 AI = 3.0 < knee 130 |

### 实时延迟

实时面板数据自 Sink 内存层直读，不经 DuckDB。指标自 `record()` 产生至 dashboard 渲染的端到端延迟约等于 HTTP 轮询周期（默认 2s）。

---

## 架构

```
                ┌─── live 内存层（O(1) 写 / O(1) 读）
                │       ↑                ↑
record() ──push─┤   /api/kpis        /api/metrics/recent  ≤60s
NVML 100ms ─────┤   /api/snapshot    /api/roofline        ≤60s
                │   /api/latency_trends                   ≤900s
                │
                │   Sink._latest:  name → (value, ts_ns)
                │   Sink._recent:  name → 2000-deep ring
                │
                └─── bg flush ─── DuckDB ─── archival 查询
                                              (>900s 时间窗)
```

热路径仅执行 O(1) 入队，不进行 I/O、序列化或锁等待。规则引擎与 Sink flush 在独立 daemon 线程运行。设计前提：插件任何异常不得影响 vLLM 推理路径。

### 关键源文件

- [`src/pping_lang/sink/base.py`](src/pping_lang/sink/base.py) —— 双路径 Sink 抽象与 ring buffer 定义
- [`src/pping_lang/sink/local.py`](src/pping_lang/sink/local.py) —— DuckDB 持久化实现
- [`src/pping_lang/collector/vllm_stats.py`](src/pping_lang/collector/vllm_stats.py) —— vLLM IterationStats → MetricPoint 适配
- [`src/pping_lang/rules/engine.py`](src/pping_lang/rules/engine.py) —— 规则评估循环（默认 1s 周期）
- [`src/pping_lang/api/routes.py`](src/pping_lang/api/routes.py) —— FastAPI endpoints
- [`src/pping_lang/ui/index.html`](src/pping_lang/ui/index.html) —— Alpine.js + Chart.js dashboard

---

## 配置

| 环境变量 | 默认值 | 说明 |
|:--|:--|:--|
| `PPING_LANG_API_PORT` | `8765` | dashboard 监听端口 |
| `PPING_LANG_API_HOST` | `127.0.0.1` | 监听地址（容器 / WSL 场景设为 `0.0.0.0`） |
| `PPING_LANG_DB_PATH` | `~/.pping-lang/local.duckdb` | DuckDB 文件路径 |
| `PPING_LANG_INSTANCE_ID` | 主机名 | 多实例聚合时的标识 |
| `PPING_LANG_FLUSH_INTERVAL_S` | `5.0` | Sink → DuckDB 刷盘周期 |
| `PPING_LANG_SINK_QUEUE_SIZE` | `65536` | Sink 内存队列容量 |
| `PPING_LANG_RULE_EVAL_INTERVAL_S` | `1.0` | 规则评估周期 |
| `PPING_LANG_RULES_PATH` | — | 自定义规则 JSON 路径 |
| `PPING_LANG_DISABLE_NVML` | — | 设为 `1` 关闭 NVML 采样 |
| `PPING_LANG_DISABLE_RULES` | — | 设为 `1` 关闭规则引擎 |
| `PPING_LANG_DISABLE_API` | — | 设为 `1` 关闭 HTTP API 与 dashboard |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | 配置后将指标同时导出至 OTel 后端 |

---

## 路线图

| 版本 | 部署模式 | 重点 |
|:--|:--|:--|
| v0.1（当前）| Embedded | 单机本地，pip 安装即用，dashboard + 规则引擎 + bench |
| v0.2 | Sidecar / Centralized | 独立 server 进程、Docker 镜像、Helm chart、K8s 多副本指标聚合 |
| v0.3 | Stateless | OTel-native，基于已有 Prometheus / Tempo 后端的诊断 |

---

## 项目状态

Pre-alpha (`v0.1.0a1`)。当前为 Embedded 模式，目标场景为单机本地开发与单卡 / 单 Pod 部署。生产侧的 Sidecar 模式、K8s 多副本聚合在 v0.2 规划。

API 在 0.x 阶段允许不兼容变更；规则 JSON schema 与 dashboard URL 路径承诺向后兼容。

---

## 开发

```bash
git clone https://github.com/leon-hf/pping-lang.git
cd pping-lang
pip install -e ".[dev,bench]"
bash scripts/setup-hooks.sh
pytest
ruff check src/ tests/
```

贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。

---

## Acknowledgments

- [vLLM](https://github.com/vllm-project/vllm) —— `stat_logger_plugins` 入口
- [DuckDB](https://duckdb.org/) —— 嵌入式分析数据库
- [NVIDIA NVML](https://docs.nvidia.com/deploy/nvml-api/) —— GPU 物理层采样
- 性能模型参考：Williams et al., *Roofline: An Insightful Visual Performance Model* (CACM 2009)；Kaplan et al., *Scaling Laws for Neural Language Models* (2020)

---

<div align="center">

## License

[Apache 2.0](LICENSE) © Leon

</div>
