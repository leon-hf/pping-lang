<div align="center">

# pping-lang

### 给 vLLM 装一个会说人话的性能诊断仪表盘

不只是把指标画出来 —— 它告诉你「为什么慢、是 memory-bound 还是 compute-bound、改哪个参数能再榨多少」。

[![PyPI](https://img.shields.io/pypi/v/pping-lang?color=4c8bf5&label=PyPI)](https://pypi.org/project/pping-lang/)
[![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-4c8bf5)](https://pypi.org/project/pping-lang/)
[![License](https://img.shields.io/badge/license-Apache%202.0-43a047)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--alpha-fb8c00)](#项目状态)
[![Tests](https://img.shields.io/badge/tests-309%20passing-43a047)](tests/)

**[快速上手](#快速上手)** · **[仪表盘](#仪表盘)** · **[兼容性](#兼容性)** · **[架构](#架构)** · **[路线图](#路线图)**

</div>

---

## 📰 Latest News

- **2026-06** · **Dual-path 实时架构**：实时面板从「DuckDB → fresh conn → SQL」改成「in-memory ring → 直接读」，bench 启动后 KPI 出现延迟从 20 s 降到 2 s
- **2026-06** · **Roofline analytical fallback**：vllm 0.13 没 `perf_stats` 也能画 — 用 `FLOPS ≈ 2·params·tokens` 从 token 计数和模型参数量反推；附自动解读卡（memory-bound / compute-bound + 4 条提速建议）
- **2026-06** · **延迟卡升级**：TTFT/TPOT 不再只报 p99，同时显示 p50/p95/p99/avg/请求数，单一数字骗不了人
- **2026-05** · **内置 bench 模块**：dashboard 直接发起静态压测，附三个标准 prompt 数据集（短问答 / 长文档 / 代码）
- **2026-05** · `v0.1.0a1` 上 PyPI，单机本地全功能可跑

---

## 它解决什么问题

vLLM 把指标都暴露给你了 —— TTFT、TPOT、KV cache 用量、GPU 利用率、运行队列…… 但**没人帮你解读**。

「GPU 利用率 94%」看了等于没看。业界已知的坑是 **GPU util 是 duty cycle，不是吞吐** —— 97% 利用率照样能掉 3 倍吞吐，因为 SM 在等数据。Grafana / Prometheus 仪表盘只是把这些数字画出来，要人盯、还得自己会看。

pping-lang 把 vLLM 自带指标加上 NVML 物理层数据，喂给一个轻量规则引擎，**输出的不是数字而是结论**：

```text
[pping-lang] [!] WARNING: GPU 利用率偏低
  GPU 平均利用率 3% 持续低于 50% 已 30s
  → 检查 batch 是否退化为 1，或开启连续 batching

[pping-lang] [!] WARNING: batch 退化
  并发请求数 1.0 ≤ 1.0 已 30s（无法发挥 batching 优势）
  → 增加客户端并发，或检查路由是否串行化
```

Roofline 散点也不让你自己看 —— 解读卡直接告诉你：

```
当前结论: Memory-bound（LLM decode 阶段的常态）
  算力利用  ▰▱▱▱▱▱▱▱▱▱   1%   （0.40 / 33 TFLOPS）
  带宽利用  ▰▰▰▰▰▱▱▱▱▱  52%   （132 / 256 GB/s）

提速方向
  · 增大 batch 直到 KV cache 接近 80%
  · 启用 speculative decoding
  · 权重量化 (AWQ / GPTQ)
  · 换带宽更高的卡 (H100 3.4 TB/s)
```

---

## 快速上手

### 不用 GPU、不用 vLLM 先试试

```bash
pip install pping-lang
python -m examples.embedded.demo
```

约 7 秒后终端打印两条诊断、浏览器同时能访问 `http://localhost:8765` —— 确认装好了。

### 接到真实的 vLLM

```bash
vllm serve <your-model>
```

vLLM 启动日志里会多出一行：

```
[pping-lang] dashboard → http://localhost:8765
```

打开网址即用 —— **不改任何 vLLM 启动参数**。

---

## 仪表盘

单页 SPA、单文件 HTML，无需 npm / 打包。三个 tab：

| Tab | 内容 |
|:--|:--|
| **实时** | 12 张 KPI 卡（TTFT / TPOT / 吞吐 / KV cache / 运行+等待请求 / MFU / GPU 利用率 / 显存占用 / Prefix cache / CUDA padding / 抢占率）；Roofline 散点 + 解读卡；TTFT / TPOT / E2E 三张趋势小图。每张卡悬停看公式 + 怎么解读 |
| **规则** | 卡片式 CRUD，改阈值即热生效；「测试当前数据」按钮一键预览本规则在当前指标下会不会触发 |
| **压测** | dashboard 直接发起静态 bench：选 endpoint / 调用名 / 并发 / 时长 / 内置 prompt 数据集，结果带 client-side TTFT/TPOT/E2E 全分布 + SLO 校验 |

KPI 数据是从 sink 内存 ring 直接读的（实时路径），DuckDB 只用于长窗口归档查询 —— 见 [架构](#架构) 一节。

---

## 兼容性

### vLLM 版本

| vLLM | 状态 | `SchedulerStats` | `IterationStats` | `cudagraph_stats` | `perf_stats` |
|:--|:--|:--:|:--:|:--:|:--:|
| **0.20+** | 推荐 | ✓ | ✓ | ✓ | ✓ |
| **0.13.x** | 实测可用 | ✓ | ✓ | ✓ (字段名异) | ✗ |
| < 0.13 | 不支持 | — | — | — | — |

**`perf_stats` 是 MFU / 显存带宽 / Roofline 的实测数据源**，只 0.20+ 才有。0.13 上：
- **MFU、padding_ratio** 那两张 KPI 卡会显示 —（vllm 不发不强造）
- **Roofline 自动切到 analytical 模式** —— 从 `gen_tokens + prompt_tokens` × 模型参数量推算，点形状对、绝对值有 ±20% 误差。卡片顶部 banner 会明示「估算」
- 其余全部正常：TTFT/TPOT/E2E 全分布、KV cache、prefix hit、preemption、NVML 全套

### 操作系统

- **Linux 原生** —— `pip install` 即可
- **Windows** —— vLLM 不原生支持，走 **WSL2 + Ubuntu**。从 Windows 浏览器访问 dashboard 时设 `PPING_LANG_API_HOST=0.0.0.0`，端口跨 WSL 自动转发

### 识别的 GPU 列表

NVML 设备名 → BF16 peak (TFLOPs/s) / 显存带宽 (GB/s)。未识别的 GPU 不影响监控，只跳过 MFU / Roofline 派生。

```
Blackwell      B200 (2250 / 8000)  ·  B100 (1800 / 8000)
Hopper         H200 (989 / 4800)   ·  H100 SXM/PCIe/NVL  ·  A100 SXM/PCIe
Ada 数据中心    L40S / L40 / L4
Ada 桌面/移动  RTX 4090 / 4080 / 4070 Ti / 4070 / 4060 Ti / 4060（含 Laptop GPU）
旧代           A30 · A10G · A10 · V100 · T4 · RTX 3090
```

补卡只需在 [`src/pping_lang/hardware.py`](src/pping_lang/hardware.py) 的 `_GPU_PEAK_TABLE` 加一行。

---

## 性能

### Hot-path 开销

vLLM 每个 scheduler step 都会调一次插件的 `record()`。我们的目标是 **<2% 推理吞吐影响**。

| 项目 | 实测 |
|:--|:--|
| `push_metric()` per call | <5 μs（O(1) deque + dict assign） |
| `record()` per call | ~100 μs（含 collector 解析所有 vllm stats 字段） |
| Sink bg flush 线程 CPU | <1%（默认 5s flush，NVML 100ms 采） |
| 内存常驻 | ~6 MB（默认 65k 队列 + ring buffer） |

### 实测：4070 Laptop + WSL2 + Qwen2.5-0.5B-Instruct

在 WSL2 + RTX 4070 Laptop (33.3 TFLOPS / 256 GB/s) 上跑 Qwen2.5-0.5B + vllm 0.13.0，bench concurrency=3：

| 指标 | 值 | 来源 |
|:--|:--|:--|
| TTFT p99 | 305 ms | 实测（client-side） |
| TPOT p99 | 22 ms（≈ 45 tok/s/req）| 实测 |
| Output 吞吐（系统聚合） | 28 tok/s | server-side `vllm.iter.gen_tokens` |
| 单请求 decode 速度 | 138 tok/s | `1000 / TPOT_p50` |
| 算力利用 | 1.2% | analytical Roofline |
| 带宽利用 | **51.5%**（132 / 256 GB/s）| analytical Roofline |
| Bound 判断 | memory-bound | 中位 AI = 3.0 < knee 130 |

带宽利用 52% 而不是 90%+，说明这台机器上 concurrency 还能再翻倍。这就是 Roofline 解读卡的价值 —— 不只看图，告诉你「你被什么卡住、还有多少头空」。

### Dashboard 实时延迟

实时 tab 的数据从 sink 内存 ring 直接读出，**不经 DuckDB**。从指标在 `record()` 里产生 → dashboard 看见，理论延迟 = HTTP 轮询间隔（默认 2 s）。

旧设计走 DuckDB → fresh conn → SQL 的链路下，启动 bench 后首屏数据出现要 15–20 s，被新架构干掉了。详见 commit `9236c60` 的 message。

---

## 架构

```
                ┌─── live 内存层（O(1) 写、O(1) 读、零 IO）
                │       ↑               ↑
record() ──push─┤   /api/kpis       /api/metrics/recent  ≤60s
NVML 100ms ─────┤   /api/snapshot   /api/roofline        ≤60s
                │   /api/latency_trends                  ≤900s
                │                       ↑
                │                  Sink._latest:  name → (value, ts_ns)
                │                  Sink._recent:  name → 2000-deep ring
                │
                └─── bg flush → DuckDB ─── archival 查询
                                            (>900s 时间窗、报告导出)
```

热路径**只做 O(1) 入队**，不阻塞 vLLM。规则引擎在自己的线程里跑、查 DuckDB 做窗口聚合 —— 任何插件 bug 都不会拖垮 vLLM 主流程（设计文档 §3.1）。

### 关键设计文件

- [`src/pping_lang/sink/base.py`](src/pping_lang/sink/base.py) — 双路径 Sink 抽象
- [`src/pping_lang/sink/local.py`](src/pping_lang/sink/local.py) — DuckDB 持久化
- [`src/pping_lang/collector/vllm_stats.py`](src/pping_lang/collector/vllm_stats.py) — vLLM IterationStats → MetricPoint 流
- [`src/pping_lang/rules/engine.py`](src/pping_lang/rules/engine.py) — 规则评估循环（默认 1 s）
- [`src/pping_lang/api/routes.py`](src/pping_lang/api/routes.py) — FastAPI + 所有 endpoint
- [`src/pping_lang/ui/index.html`](src/pping_lang/ui/index.html) — 单文件 dashboard（Alpine.js + Chart.js）

---

## 配置

大多数情况零配置即用。要调就用环境变量：

| 变量 | 默认 | 作用 |
|:--|:--|:--|
| `PPING_LANG_API_PORT` | `8765` | 仪表盘端口 |
| `PPING_LANG_API_HOST` | `127.0.0.1` | 容器 / WSL 里要设成 `0.0.0.0` |
| `PPING_LANG_DB_PATH` | `~/.pping-lang/local.duckdb` | 持久化数据库路径 |
| `PPING_LANG_INSTANCE_ID` | 主机名 | 多实例聚合时区分用 |
| `PPING_LANG_FLUSH_INTERVAL_S` | `5.0` | sink → DuckDB 的 flush 频率 |
| `PPING_LANG_SINK_QUEUE_SIZE` | `65536` | sink 内存队列容量；高并发可上调 |
| `PPING_LANG_RULE_EVAL_INTERVAL_S` | `1.0` | 规则评估周期 |
| `PPING_LANG_RULES_PATH` | — | 自定义规则 JSON 路径，覆盖默认 10 条 |
| `PPING_LANG_DISABLE_NVML` | — | `1` 关闭 NVML 采样（无 GPU 环境） |
| `PPING_LANG_DISABLE_RULES` | — | `1` 关闭规则引擎 |
| `PPING_LANG_DISABLE_API` | — | `1` 关闭 dashboard / HTTP API |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | 设了就把数据同时发去 OTel 后端（Langfuse / Jaeger / Datadog） |

---

## 路线图

| 版本 | 模式 | 重点 |
|:--|:--|:--|
| **v0.1**（当前）| Embedded | 单机本地、`pip install` 即用、dashboard + 规则 + bench |
| v0.2 | + Sidecar / Centralized | 独立 server 进程、Docker 镜像、Helm chart、K8s 多副本聚合 |
| v0.3 | + Stateless | OTel-native、从已有 Prometheus / Tempo 反查诊断 |

---

## 项目状态

**Pre-alpha** (`v0.1.0a1`)。当前为 **Embedded 模式**，面向单机本地开发、单卡 / 单 Pod 场景。Sidecar、K8s 多副本聚合等生产部署模式在 v0.2 规划。

API 还可能 break。规则 JSON schema 和 dashboard URL 路径保持稳定。

---

## 开发

```bash
git clone https://github.com/leon-hf/pping-lang.git
cd pping-lang
pip install -e ".[dev,bench]"
bash scripts/setup-hooks.sh    # 一次性激活 git hooks
pytest                          # 309 个测试
ruff check src/ tests/
```

贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，变更见 [CHANGELOG.md](CHANGELOG.md)。

---

## Acknowledgments

- **[vLLM](https://github.com/vllm-project/vllm)** —— 没有它的 `stat_logger_plugins` 入口这个项目根本起不来
- **[DuckDB](https://duckdb.org/)** —— 嵌入式分析数据库，零运维
- **[NVIDIA NVML](https://docs.nvidia.com/deploy/nvml-api/)** —— GPU 物理层数据
- 性能分析理论：Williams et al., *Roofline: An insightful visual performance model* (2009)；Kaplan et al., *Scaling Laws for Neural Language Models* (2020)

---

<div align="center">

## License

**[Apache 2.0](LICENSE)** © Leon

<sub>用 vLLM 不该靠猜。</sub>

</div>
