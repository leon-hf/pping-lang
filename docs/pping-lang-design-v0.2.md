# pping-lang: vLLM 性能诊断插件 — 设计文档

**版本**：v0.2.1 (2026-05-14)
**状态**：设计阶段（已通过 pre-impl RFC，可进入编码）
**目标**：3 周内发布 v0.1（Embedded 模式），后续渐进支持 K8s 生产场景
**配套 RFC**：[pping-lang-pre-impl-rfc.md](pping-lang-pre-impl-rfc.md)（编码前 5 项决策收口）

---

## v0.2.1 修订

基于 pre-impl RFC 的修正：

- **§13.2 metric 命名**：统一为 `pping_lang.<domain>.<name>_<unit>` 规范（原 `power_watts` → `power_w` 等）
- **§15.1 多 worker 处理**：删除"rank 0 启 FastAPI、其他 worker IPC 上报"的错误前提；vLLM v1 架构下 TP worker 已由 EngineCore 内部 ZMQ 汇总，StatLogger 只在 EngineCore 进程跑
- **§15.2 DP 范围**：v0.1 限定 DP=1，DP>1 推到 v0.2 走 `AggregateStatLoggerBase`
- **v0.1 范围扩展**（来自 RFC §8）：实测 vLLM `cudagraph_stats` / `perf_stats` 后纳入 v0.1：
  - §8.1 数据源新增 cudagraph + perf 字段（之前误以为 v0.1 跳过）
  - §10.4 默认规则 9 条 → 11 条（新增 `high-cudagraph-padding`，激活原本无数据源的 `memory-bw-saturated`）
  - §12.1 Roofline 基础版本从 v0.2 提前到 v0.1
  - 日历影响：v0.1 +2 小时，不影响 Day 5 demo

## v0.2 主要变更

相对 v0.1 的关键修正：

- **新增"部署模式"概念**：识别出 v0.1 的 fat plugin 架构在 K8s 多副本场景下不成立，引入四种部署模式（Embedded / Sidecar / Centralized / Stateless）
- **引入 Sink 抽象**：让一份代码支持多种部署模式，v0.1 就要做好接口
- **明确演进路径**：v0.1 只发 Embedded 模式，v0.2 加 Sidecar，v0.3 加 OTel-native
- **存储路径不再硬编码**：DuckDB 位置依部署模式而定，K8s 场景必须挂卷
- **新增章节**：部署模式详解、Docker/K8s 差异分析、版本演进路线图

---

## 1. 一句话定义

**pping-lang 是 vLLM 的性能诊断插件。它把 vLLM 已有的指标、加上工具自己采集的 GPU 物理层数据，喂给一个轻量规则引擎，自动告诉用户"你的服务为什么慢、怎么修"，并能生成可分享的复盘报告。**

定位关键词：诊断层（不是 observability backend）、可嵌入可分离（按部署场景选）、聚焦 vLLM（不做通用）。

---

## 2. 背景与动机

### 2.1 vLLM 已经做得很好的事

调研结论（基于 vLLM 0.20.2）：

- **请求级 OTel trace**：`--otlp-traces-endpoint` 启用后，每个请求一个 span
- **细粒度 trace**：`--collect-detailed-traces=model_execute,model_forward` 能拿到模块级时间
- **Prometheus 指标全套**：TTFT、TPOT、KV cache 用量、queue depth、prefix cache 命中等
- **可插拔 StatLogger**：v1 架构提供了 `vllm.stat_logger_plugins` entry point group，类继承 `StatLoggerBase` 即可注入
- **CUDA graph 指标**：`--enable-cudagraph-metrics` 开启 dispatch mode、padded/unpadded token 数

### 2.2 vLLM 没有解决的事（即 pping-lang 的价值）

1. **GPU 物理层数据没接进来**：NVML/DCGM 数据不在 vLLM 指标里，用户要自己搭，且**没法和 vLLM 的请求关联**
2. **没有自动诊断**：所有指标都给你了，但没人帮你解读。Grafana 仪表盘要人盯着看
3. **GenAI semconv 没对齐**：vLLM 自己 pin 了 `SpanAttributes`，故意不跟进 OTel GenAI semantic conventions（v1.37+），主流后端字段对不上
4. **GPU util 是 duty cycle 不是吞吐**：行业已知坑（97% util 但吞吐掉 3 倍），vLLM 不会帮你揭穿
5. **没有报告导出**：Grafana 是仪表盘，不是可分享的复盘文档

---

## 3. 设计目标与非目标

### 3.1 必须做到（goals）

- **多部署形态友好**：本地一行 `pip install` 即用，生产 K8s 多副本场景架构也成立
- **架构上故障隔离**：pping-lang 任何 bug 不能拖垮 vLLM 推理
- 性能开销 < 2%（开启所有功能下）
- 默认开箱即用：内置 8-10 条经过验证的诊断规则
- 规则可视化编辑：Web UI 改阈值、加规则，不重启 vLLM
- 报告自包含：单个 HTML 文件，可邮件分享
- OTel 输出兼容主流后端（Langfuse、Jaeger、Datadog）

### 3.2 明确不做（non-goals）

| 不做 | 原因 |
|---|---|
| Observability 后端 | Langfuse/Jaeger/Tempo 在做 |
| 模型质量评估 | eval 框架的事 |
| 异常检测 ML | 规则引擎够 v1 用 |
| 长期数据存储 | DuckDB 默认 30 天滚动，要长期走 OTel 出去 |
| 规则市场 | 等 v0.5 用户基数起来再说 |
| 非 vLLM 后端（SGLang、TGI） | v1 聚焦 |
| 替代 Prometheus + Grafana | 互补而非替代 |

---

## 4. 用户画像与故事

### 4.1 三类用户画像

| 画像 | 场景 | 推荐部署模式 |
|---|---|---|
| **个人开发者小王**：本地调 vLLM，看为什么慢 | 笔记本 + 单 GPU + 直接跑 | Embedded |
| **平台工程师老李**：给业务团队提供 vLLM 服务，多 Pod 副本 | K8s + 多副本 + 已有 Prometheus | Centralized 或 OTel-native |
| **MLOps 张工**：要求 vLLM 进公司 observability 栈，统一管理 | K8s + 强治理 + 已有 OTel collector | OTel-native |

### 4.2 个人开发者完整故事

**Day 1, 10:00 - 装上**

```bash
pip install pping-lang
vllm serve Qwen/Qwen2.5-32B-Instruct  # 不改任何参数
```

vLLM 启动日志多一行：
```
[pping-lang] embedded mode, dashboard at http://localhost:8765
```

**Day 1, 10:05 - 看实时仪表盘**

打开 `http://localhost:8765`，看到当前 GPU util 34%、KV cache 12%、TTFT p99 1240ms。下方"当前触发的诊断"显示：

> ⚠ GPU 利用率偏低
> GPU 平均利用率 34% 持续低于 50% 已 45 秒
> 建议：检查 batch 是否退化为 1，或开启连续 batching

**Day 2 - 调规则**

阈值改成 30，点"测试当前数据"立即看是否还触发，确认后保存。规则立即生效。

**Day 7 - 出报告**

下载 `pping-lang-report.html`，单 HTML 文件，发邮件给老板。

### 4.3 平台工程师完整故事

**老李部署到 K8s**：

```yaml
# Helm values
pping-lang:
  mode: centralized
  server:
    replicas: 1
    persistence:
      enabled: true
      size: 50Gi
  agent:
    enabled: true   # 注入到每个 vLLM Pod 作为 sidecar
```

部署后：
- 6 个 vLLM Pod 各自带 pping-lang-agent sidecar
- 1 个 pping-lang-server Deployment 收所有 agent 上报数据
- Dashboard 通过 Ingress 暴露：`https://pping-lang.team.com`
- 进去能看到 6 个 Pod 的聚合视图，也能切到单 Pod

**核心区别**：dashboard 是平台级 service，不是 Pod 级 ephemeral 服务。

### 4.4 v0.1 用户故事覆盖度

v0.2.1 + pre-impl RFC 收口后，盘点对 §4 三类用户画像的覆盖。

#### ✅ 完整覆盖：个人开发者小王（§4.2 全流程）

| 故事节点 | 实现机制 | 落地 Day |
|---|---|---|
| Day 1, 10:00 — `pip install pping-lang` + `vllm serve`（不改参数） | entry point 自动注册（`vllm.stat_logger_plugins`） | Day 1 |
| Day 1, 10:00 — 启动日志多一行 dashboard 链接 | `log_engine_initialized` 内启 FastAPI + 打印 | Day 6 |
| Day 1, 10:05 — 打开 `localhost:8765` 看实时 GPU/KV/TTFT | SSE `/api/metrics/live` + 单文件 HTML | Day 7 |
| Day 1, 10:05 — 看到当前触发的诊断卡片（含建议） | 11 条默认规则 + `/api/diagnoses` | Day 4 + Day 7 |
| Day 2 — 改阈值 + "测试当前数据" + 立即生效 | 规则 CRUD + 热加载 | Day 8-9 |
| Day 7 — 下载单 HTML 报告，邮件分享 | `/api/report` + plotly inline | Day 10 |

#### ⚠️ 部分覆盖 / 降级覆盖

| 场景 | v0.1 状况 | 缺什么 |
|---|---|---|
| 多 GPU 单 Pod（TP=2/4/8） | NVML 按 GPU index 全采，能展示每张卡 | UI 多 GPU 切换/对比控件 Day 7 细化 |
| DP > 1（多 engine 并行） | 启动 warning + 降级到只采主 engine | `AggregateStatLoggerBase` 走 v0.2 |
| 单机 Docker 跑 vLLM | 技术上可跑 | 默认 `127.0.0.1` 在容器内不可达；文档需给 `PPING_LANG_BIND_HOST=0.0.0.0` 模板（Day 14） |
| OTel 输出（GenAI semconv 翻译） | 最常用属性已覆盖 | 完整对齐推 v0.2 |
| 历史趋势报告 | DuckDB 已存 | 首日数据少时报告 UX 兜底（Day 10） |

#### ❌ v0.1 完全不覆盖（已明确推迟）

| 故事 | 推到 |
|---|---|
| 平台工程师老李整套（§4.3 helm install / 多 Pod / Ingress / 聚合视图） | v0.2 |
| ↳ Sidecar 容器 / Pod 内进程隔离 | v0.2 |
| ↳ 多 Pod 聚合 dashboard / 实例选择器 | v0.2 |
| ↳ token 认证 / SSO | v0.2（token） / v0.3（SSO） |
| MLOps 张工整套（OTel-native 反向查询 + CronJob 出报告） | v0.3 |
| 复合规则（all/any） | v0.2 |
| 多实例报告对比 | v0.2 |
| Centralized 模式 HA | v0.3 |
| 规则市场 / 团队共享规则 | v0.5 |
| 异常检测 ML / 影响估计回归模型 | v0.4+ |

#### v0.2.1 范围扩展带来的新故事

实测 vLLM `cudagraph_stats` / `perf_stats` 后纳入 v0.1：

- **看穿 GPU duty cycle**：padding ratio 直接算出"GPU 看着 95% 但 47% 在补 0"——设计 §2.2 marquee 卖点首次有具体数字
- **MFU 实时显示**：`vllm.perf.mfu_ratio` 在仪表盘和报告 Executive Summary
- **基础 Roofline 图**：报告 §5 含 flops/bytes 散点 + 硬件 peak 上沿（按 NVML GPU 型号查表）
- **`memory-bw-saturated` 规则真实可用**：原本无数据源，现在 `perf_stats` 直接给

### 4.5 v0.1 marquee demo 一句话

> 开 vLLM 不改任何参数，3 秒后浏览器告诉你 GPU 在补 0、MFU 只有 18%、改 max_num_seqs 应该到 384。

这是 HN/Twitter 上能传的话——对应设计 §25 v0.1 关键里程碑"抢眼球"。

---

## 5. 部署模式与架构演进

**这一节是 v0.2 的核心新增内容。** 之前的设计假设"插件搞定一切"，在 K8s 场景下站不住。

### 5.1 四种部署模式

#### 模式 1：Embedded（默认，v0.1）

```
┌──────────────────────────────────┐
│ vLLM 进程                         │
│  ├── 推理                         │
│  ├── pping_lang StatLogger plugin    │
│  │   ├── 采集（NVML + vLLM stats）│
│  │   ├── DuckDB（进程内）          │
│  │   ├── 规则引擎                  │
│  │   └── FastAPI :8765            │
│  └── ...                          │
└──────────────────────────────────┘
```

**适用**：本地开发、单机 Docker、零配置 demo。
**优点**：装一次完事、零部署成本。
**缺点**：vLLM 进程职责膨胀、故障耦合、K8s 多副本不友好。

#### 模式 2：Sidecar（v0.2）

```
┌──────────────────────────────────────┐
│ Pod                                   │
│ ┌────────────────┐  ┌──────────────┐ │
│ │ vLLM 容器       │  │ pping-lang-  │ │
│ │ + pping-lang-  │─►│ server       │ │
│ │   agent (轻量)  │  │ sidecar 容器  │ │
│ │                │  │ DuckDB / API │ │
│ └────────────────┘  └──────────────┘ │
└──────────────────────────────────────┘
```

**适用**：K8s 单副本生产、Docker compose、想要进程隔离。
**优点**：故障隔离、生命周期解耦、vLLM 容器轻量。
**缺点**：每个 Pod 一份 dashboard，多副本要 LB hash 才能看到一致视图。

#### 模式 3：Centralized（v0.2）

```
┌───────────────┐                       ┌────────────────────┐
│ vLLM Pod 1    │                       │ pping-lang-server      │
│ + agent       │──┐                    │ Deployment          │
└───────────────┘  │                    │ ├── 接收 N 个 agent │
                   ├───── gRPC/OTLP ───►│ ├── DuckDB (PVC)    │
┌───────────────┐  │                    │ ├── 规则引擎         │
│ vLLM Pod 2    │  │                    │ └── FastAPI + UI    │
│ + agent       │──┤                    └────────────────────┘
└───────────────┘  │                              ▲
                   │                              │
┌───────────────┐  │                       Ingress / Service
│ vLLM Pod 3    │  │                              │
│ + agent       │──┘                       (浏览器统一访问)
└───────────────┘
```

**适用**：K8s 多副本生产、需要全局聚合视图。
**优点**：集中视图、单一仪表盘、独立扩缩。
**缺点**：server 是有状态服务，要 PVC，需要 HA 考虑。

#### 模式 4：Stateless / OTel-native（v0.3）

```
┌───────────────┐         ┌──────────────────┐
│ vLLM × N      │         │ OTel Collector    │
│ + agent       │────────►│ (用户已有的)      │
│ (纯导出)       │  OTLP   │                  │
└───────────────┘         └────────┬─────────┘
                                   │
                                   ▼
                          ┌────────────────────┐
                          │ Prometheus / Tempo │
                          │ (用户的后端)        │
                          └────────┬───────────┘
                                   │
                                   ▼
                          ┌────────────────────┐
                          │ pping-lang-analyzer    │
                          │ (CLI / CronJob)     │
                          │ 查 PromQL 跑诊断    │
                          │ 出报告              │
                          └────────────────────┘
```

**适用**：已有完整 observability 栈、要求云原生无状态、强治理团队。
**优点**：真正无状态、归属清晰、数据进用户已有栈。
**缺点**：依赖用户基础设施、实时性差（秒级查询）、规则评估在外部。

### 5.2 Docker vs K8s 关键差异

| 维度 | Docker / docker-compose | Kubernetes |
|---|---|---|
| 应用单元 | 容器 = 应用 | Pod = ephemeral 实例 |
| 状态管理 | 容器内本地状态 OK | 本地状态是反模式（需 PVC） |
| 多副本 | 通常 1 个 | 通常 N 个 |
| 服务发现 | 容器名直连 | Service + DNS |
| 端口暴露 | host:container 映射 | Service + Ingress |
| 推荐模式 | Embedded 或 Sidecar | Centralized 或 Stateless |

**关键认知**：Docker 场景"工具 = 应用"还成立；K8s 场景必须接受"Pod 是 cattle 不是 pets"，任何放在 Pod 内的状态都是反模式。

### 5.3 模式选择矩阵

| 你的场景 | 推荐 |
|---|---|
| 本地笔记本开发 | Embedded |
| 单机 Docker 跑 vLLM | Embedded |
| docker-compose 多服务 | Sidecar |
| K8s 单副本测试环境 | Sidecar |
| K8s 多副本生产、想要简单 | Centralized |
| K8s 多副本生产、已有 OTel 栈 | Stateless |

---

## 6. 整体架构（基于 Sink 抽象）

### 6.1 核心抽象

把"采集"和"消费"解耦，所有部署模式共用同一份采集代码，差别只在 Sink 的实现。

```
┌─────────────────────────────────────┐
│ vLLM 进程（永远存在）                 │
│                                      │
│  PpingLangStatLogger                   │
│   ├── Collector（采集统一）           │
│   │   ├── vLLM stats 接入            │
│   │   └── NVML 后台采样              │
│   │                                  │
│   └── Sink（策略模式）                │
│       ├── LocalSink   (Embedded)    │
│       ├── RemoteSink  (Sidecar/     │
│       │              Centralized)   │
│       └── OTelSink    (Stateless)   │
└─────────────────────────────────────┘
```

### 6.2 Sink 接口设计

```python
class Sink(Protocol):
    def push_metrics(self, batch: list[MetricPoint]) -> None: ...
    def push_diagnosis(self, diag: Diagnosis) -> None: ...
    def close(self) -> None: ...

class LocalSink(Sink):
    """Embedded 模式：DuckDB + FastAPI 都在本进程"""
    def __init__(self):
        self.duckdb = duckdb.connect(...)
        self.api_server = start_local_api(...)
    
class RemoteSink(Sink):
    """Sidecar / Centralized 模式：gRPC/OTLP 上报到外部 server"""
    def __init__(self, endpoint: str):
        self.client = OTLPClient(endpoint)
    
class OTelSink(Sink):
    """Stateless 模式：纯 OTel 出口，本地不存"""
    def __init__(self, otel_endpoint: str):
        self.meter = ...
        self.tracer = ...
```

模式选择通过环境变量或配置文件：

```bash
PPING_LANG_MODE=embedded   # 默认
PPING_LANG_MODE=remote     # 配合 PPING_LANG_SERVER_ENDPOINT
PPING_LANG_MODE=otel       # 配合 OTEL_EXPORTER_OTLP_ENDPOINT
```

### 6.3 pping-lang-server（独立进程）

v0.2 开始引入。是一个可执行 CLI：

```bash
pping-lang server --port 8765 --storage /var/lib/pping-lang
```

职责：
- 接收 agent 上报（gRPC 或 OTLP）
- DuckDB 存储
- 规则引擎运行
- 提供 FastAPI（API + UI）
- 生成报告

server 完全不依赖 vLLM，可以独立打包、独立部署、独立扩缩。

### 6.4 pping-lang-analyzer（v0.3）

纯无状态 CLI / Web 工具，从用户的 Prometheus/OTel 后端读数据跑诊断：

```bash
pping-lang analyze --source prometheus --endpoint http://prom:9090 \
                --from "7 days ago" --output report.html
```

---

## 7. 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.10+ | vLLM 同语言 |
| vLLM 接入 | `vllm.stat_logger_plugins` entry point | 官方机制 |
| GPU 采集 | `pynvml` | 轻、官方 |
| 时序存储 | `duckdb` | 嵌入式、列式、查询快 |
| 上报协议 | OTLP/gRPC | 标准、已经要做 OTel |
| OTel SDK | `opentelemetry-sdk` + `opentelemetry-exporter-otlp` | 标准 |
| HTTP 服务 | `fastapi` + `uvicorn` | 和 vLLM 一致 |
| 前端 | 单文件 HTML + Alpine.js + Chart.js（CDN） | 零构建 |
| 报告图表 | `plotly`（生成静态 HTML） | 交互保留 |
| 模板 | `jinja2` | 标准 |
| 容器/K8s | 提供官方 Docker image + Helm chart（v0.2） | 生产友好 |

依赖总数 ~10 个，全主流。

---

## 8. 数据采集层

### 8.1 采集源

| 层 | 数据 | 来源 | 频率 |
|---|---|---|---|
| 框架层 | batch_size, KV cache usage, queue depth, prefix cache hit rate | vLLM `SchedulerStats` | 每 step |
| 请求层 | TTFT, TPOT, e2e_latency, prompt/generation tokens | vLLM `IterationStats` / `FinishedRequestStats` | 每 step |
| GPU 物理层 | utilization, memory used, power, temperature, sm_clock | `pynvml` | 100ms |
| **CUDA graph 效率** | padded vs unpadded tokens, runtime mode | vLLM `cudagraph_stats` (需 `--enable-cudagraph-metrics`) | 每 step |
| **MFU / 内存带宽** | flops, read/write bytes per GPU | vLLM `perf_stats` | 每 step |
| GPU 进阶（v0.4+）| tensor core util、PCIe 带宽 | DCGM (如果可用) | 1s |

> **实测确认**（pre-impl RFC §4.3）：vLLM 0.20+ 的 `cudagraph_stats` 和 `perf_stats` 提供了 padding ratio 和 MFU 所需的 ground truth，v0.1 即可使用。完整字段映射见 RFC。

### 8.2 采集实现

```python
class PpingLangStatLogger(StatLoggerBase):
    def __init__(self, vllm_config, engine_index=0):
        self.engine_index = engine_index
        self.collector = Collector()
        self.sink = build_sink_from_config()  # 工厂方法
        self._start_nvml_thread()
        self._start_rule_thread()  # 仅在 sink 是 LocalSink 时启动
    
    def record(self, scheduler_stats, iteration_stats, mm_cache_stats=None, engine_idx=0):
        # 热路径：O(1) 采集 + 入队
        points = self.collector.collect(scheduler_stats, iteration_stats)
        self.sink.push_metrics(points)
```

**注意点**：
- `record()` 必须 O(1)，重活异步
- v1 架构里 `log()` 几乎不被调用（issue #20175），逻辑放 `record()`
- NVML 线程独立采，统一时钟（`time.monotonic_ns`）

### 8.3 采集开销

| 子系统 | 预算 |
|---|---|
| `record()` 热路径 | < 50μs/step |
| NVML 采样 | < 0.5% CPU |
| Sink 推送（异步） | < 1% CPU |
| 规则评估（仅 LocalSink） | < 5ms/秒 |
| **总开销** | **< 2% 吞吐** |

---

## 9. 数据存储层（按部署模式分）

存储位置完全依部署模式而定。**这是 v0.2 的关键修正——v0.1 假设统一存 `~/.pping-lang`，K8s 场景行不通。**

| 数据 | Embedded | Sidecar | Centralized | Stateless |
|---|---|---|---|---|
| 规则配置 | 本地 JSON 文件 | 本地 ConfigMap | server PVC / ConfigMap | server 端 |
| 实时指标流 | vLLM 进程内存 | agent 进程内存 | agent 进程内存 | agent 进程内存 |
| 历史指标时序 | 本地 DuckDB | sidecar DuckDB | server PVC DuckDB | 不存（在用户 Prometheus） |
| 诊断事件 | 本地 DuckDB | sidecar DuckDB | server DuckDB | OTel events |
| OTel | 可选 | 可选 | 可选 | 必选 |

### 9.1 DuckDB schema（不变）

```sql
CREATE TABLE metrics (
    ts_ns BIGINT,
    engine_idx INT,
    instance_id VARCHAR,  -- 新增：区分 Pod / agent 来源
    metric_name VARCHAR,
    value DOUBLE
);
CREATE INDEX idx_metrics_ts ON metrics(ts_ns);

CREATE TABLE diagnoses (
    ts_ns BIGINT,
    instance_id VARCHAR,
    rule_id VARCHAR,
    severity VARCHAR,
    triggered_value DOUBLE,
    threshold DOUBLE,
    message VARCHAR
);

CREATE TABLE config_snapshots (
    ts_ns BIGINT,
    instance_id VARCHAR,
    config JSON
);
```

**新增 `instance_id`**：Centralized 模式下区分上报来源。

### 9.2 存储滚动

- 默认保留 30 天
- 启动时和每天定时清理
- DuckDB 自动 vacuum

### 9.3 K8s 持久化

Centralized 模式必须挂 PVC：

```yaml
volumeMounts:
  - name: pping-lang-data
    mountPath: /var/lib/pping-lang
volumes:
  - name: pping-lang-data
    persistentVolumeClaim:
      claimName: pping-lang-pvc
```

PVC 大小建议：每个 vLLM Pod 30 天约 500MB（按 1Hz 采样估算），10 Pod 即 5GB。

---

## 10. 规则引擎

### 10.1 设计原则

不用现成库，自己写 100 行微型 DSL。理由：

- 通用引擎没有原生时间窗口聚合
- 规则形状极简（指标 + 操作符 + 阈值 + 窗口），固定 schema 反而友好
- 固定 schema → 表单式 UI 易做、白名单 metric 可校验

### 10.2 规则 schema

```json
{
  "id": "low-gpu-util",
  "name": "GPU 利用率偏低",
  "enabled": true,
  "severity": "warning",
  "category": "throughput",
  "condition": {
    "metric": "gpu.utilization_pct",
    "op": "<",
    "threshold": 50,
    "window_seconds": 30,
    "aggregation": "avg"
  },
  "message": "GPU 平均利用率 {value:.0f}% 持续低于 {threshold}% 已 {window}s",
  "suggestion": "检查 batch 是否退化为 1，或开启连续 batching"
}
```

支持的字段：
- `metric`：白名单 `gpu.*` / `vllm.*`
- `op`：`<`, `<=`, `>`, `>=`, `==`, `!=`
- `aggregation`：`avg`, `p50`, `p95`, `p99`, `max`, `min`, `count`
- `severity`：`info`, `warning`, `critical`

### 10.3 复合条件（v0.2 同步加）

```json
{
  "condition": {
    "all": [
      {"metric": "gpu.utilization_pct", "op": "<", "threshold": 50, ...},
      {"metric": "vllm.batch_size", "op": "<=", "threshold": 2, ...}
    ]
  }
}
```

### 10.4 内置规则（v0.1 默认开启，11 条）

| ID | 类别 | 描述 | 数据源 |
|---|---|---|---|
| low-gpu-util | throughput | GPU 利用率长期偏低 | NVML |
| kv-cache-pressure | latency | KV cache 用量 > 90% | SchedulerStats |
| queue-buildup | throughput | 队列长度持续 > 50 | SchedulerStats |
| high-ttft-p99 | latency | TTFT p99 > 2s | IterationStats |
| batch-degraded | throughput | batch size 长期 ≤ 1 | SchedulerStats |
| preemption-spike | stability | preemption 频率 > 1/秒 | IterationStats |
| low-prefix-cache-hit | efficiency | prefix cache 命中率 < 10% | SchedulerStats |
| memory-bw-saturated | bottleneck | 内存带宽利用率 > 90%（接近 roofline 上沿） | **perf_stats**（v0.2.1 起可用） |
| ttft-tpot-imbalance | tuning | TTFT 高但 TPOT 低（prefill 瓶颈） | IterationStats |
| **high-cudagraph-padding** | throughput | CUDA graph padding 比例 > 30%（GPU 在补 0） | **cudagraph_stats** |
| **low-mfu** | efficiency | MFU < 20%（计算资源浪费） | **perf_stats** + GPU peak 表 |

后两条规则是 v0.2.1 范围扩展加入的——直接对应设计 §2.2 的"GPU util duty cycle 不是吞吐"洞察，把卖点从故事变成具体数字。

### 10.5 引擎运行位置

- **Embedded**：vLLM 进程内
- **Sidecar / Centralized**：pping-lang-server 进程内
- **Stateless**：analyzer 进程内（查询时跑）

代码完全相同，运行位置不同。

---

## 11. Web UI 与 API

### 11.1 API 端点

| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/rules` | GET / POST | 规则 CRUD |
| `/api/rules/{id}` | PUT / DELETE | 编辑/删除规则 |
| `/api/rules/{id}/test` | POST | 用当前数据试触发 |
| `/api/metrics/live` | GET (SSE) | 实时指标流 |
| `/api/metrics/available` | GET | 指标白名单 |
| `/api/diagnoses` | GET | 当前触发诊断 |
| `/api/diagnoses/history` | GET | 历史诊断 |
| `/api/report` | GET | 生成 HTML 报告 |
| `/api/instances` | GET | **新增**：列出所有上报实例（Centralized 模式） |
| `/api/config` | GET | 当前 vLLM 配置快照 |
| `/api/health` | GET | 工具自身健康 |

### 11.2 UI 形态

单文件 HTML，三个 tab：

1. **实时**：仪表盘 + 当前诊断卡片，Centralized 模式下顶部加实例选择器
2. **规则**：卡片式 CRUD
3. **报告**：时间范围 + 生成按钮

### 11.3 API 运行位置

| 模式 | API 在哪 |
|---|---|
| Embedded | vLLM 进程内（FastAPI thread） |
| Sidecar | sidecar 容器内（独立进程） |
| Centralized | server Deployment（独立 Pod） |
| Stateless | analyzer 临时进程（仅查询时） |

---

## 12. 性能诊断报告

### 12.1 报告结构

1. **Executive Summary**：时间范围、总请求、p50/p99 TTFT/TPOT、GPU 平均利用率、**MFU 平均值**
2. **关键问题**（按严重程度）：现象图、规则触发、关联指标、影响估计、可执行建议
3. **趋势分析**：日维度变化
4. **配置审计**：vLLM 启动参数 vs 建议
5. **Roofline 基础图**（v0.1，使用 `perf_stats` 的 flops + bytes）；**完整版本 v0.2**（按 model layer 分解）
6. **触发规则汇总**
7. **多实例对比**（Centralized 模式特有，v0.2 加）

### 12.2 实现

`/api/report` 端点：
1. 从 DuckDB 拉时间范围数据（一条 SQL）
2. 跑预定义分析逻辑
3. `plotly.io.to_html(include_plotlyjs='inline')` 嵌入图表
4. Jinja2 拼出 HTML
5. 返回文件下载

整个模块约 300 行。

### 12.3 配置审计逻辑

```
当前配置 vs 建议
─────────────────────────────────
max_num_seqs:           256  → 建议 512   (理由: batch p50=1.2)
enable_prefix_caching:  false → 建议 true (理由: 67% prompt 前缀重复)
gpu_memory_utilization: 0.9  → 保持
chunked_prefill:        false → 建议 true (理由: 长 prompt 阻塞 decode)
```

每条建议关联具体证据。

---

## 13. OTel 集成

### 13.1 整体策略

vLLM 已有 OTel trace（请求级），pping-lang **不重复**。补充：

1. GPU 数据作为 OTel metrics 输出
2. GenAI semconv 翻译层（vLLM SpanAttributes → OTel GenAI semconv v1.37+）
3. 诊断事件作为 OTel log signal

### 13.2 输出 metrics

命名遵循统一规范 `pping_lang.<domain>.<subsystem>?.<name>_<unit>`（详见 pre-impl RFC §5）。OTel 导出时机械加 `pping_lang.` 前缀，不做单位转换。

```
pping_lang.gpu.utilization_pct                 gauge   (0..100)
pping_lang.gpu.mem_used_bytes                  gauge
pping_lang.gpu.mem_util_pct                    gauge   (0..100)
pping_lang.gpu.power_w                         gauge
pping_lang.gpu.temp_c                          gauge
pping_lang.gpu.sm_clock_mhz                    gauge
pping_lang.vllm.scheduler.kv_cache_usage_ratio gauge   (0..1)
pping_lang.vllm.iter.ttft_ms                   histogram
pping_lang.vllm.req.e2e_latency_ms             histogram
pping_lang.diagnosis.triggered_total           counter (label: rule_id, severity)
```

resource attributes：`service.name=pping_lang`、`service.instance.id=<engine_idx>@<host>`。

完整 metric 字段映射（vLLM SchedulerStats / IterationStats / FinishedRequestStats → 我们的命名）见 pre-impl RFC §4.3。

### 13.3 与 Stateless 模式的关系

Stateless 模式下 OTel 是**唯一**数据出口。所有指标、诊断事件都走 OTel，pping-lang-analyzer 反过来从 Prometheus/Tempo 查询。

这意味着 Stateless 模式的实时性受用户 OTel 后端的查询延迟限制（通常秒级），不如 Embedded/Centralized 的内存评估实时。

---

## 14. 性能开销约束

**承诺**：开启所有功能下，吞吐下降 < 2%。

### 14.1 控制手段

- `record()` 热路径只做 O(1)（push + 队列）
- 重活异步（DuckDB 写入、OTel 导出、Sink 推送）
- NVML 采样独立线程
- 规则评估节流到每秒一次
- Sink 推送批量（默认 5s / 512 events）
- CI benchmark：吞吐回归 > 3% 阻塞合并

### 14.2 自我观测

pping-lang 自己输出：
- `pping_lang.overhead.record_us`：`record()` 平均耗时
- `pping_lang.overhead.sink_lag`：Sink 队列堆积
- `pping_lang.overhead.eval_ms`：规则评估耗时

---

## 15. 多进程 / 多 GPU 处理

### 15.1 vLLM 多 worker（tensor parallel）

**关键事实**（v0.2.1 修订）：vLLM v1 架构下，TP 多 worker 是 EngineCore 的子进程，stats 已经通过 vLLM 内部 ZMQ 汇总到 EngineCore 主进程。`StatLogger` **只在 EngineCore 进程实例化一次**，看到的就是聚合后的 `SchedulerStats`。我们不需要做任何跨 worker 的 IPC。

处理方式：

- **NVML 采样**：在 EngineCore 进程内单线程跑。NVML 是外部 GPU 接口，任何进程都能查任何 GPU——通过 `CUDA_VISIBLE_DEVICES` 解析出本 engine 占用的 GPU index 列表，逐个 `nvmlDeviceGetHandleByIndex(i)` 即可。
- **数据合并**：天然单点，无需合并
- **FastAPI / DuckDB**：启在 EngineCore 进程，与 StatLogger 同进程
- **OTel exporter**：单实例，`service.instance.id = <engine_idx>@<host>`

> 历史注：v0.2 设计文档原文写"rank 0 启 FastAPI、其他 worker IPC 上报"——这是基于错误前提（误以为 worker 各有 StatLogger）。v0.2.1 修正。

### 15.2 vLLM 多 engine（data parallel）

- **v0.1 限定 DP=1**。如检测到 `vllm_config.parallel_config.data_parallel_size > 1`，启动时打 warning 并降级（仍能跑，但只采主 engine）
- **v0.2 完整支持**：实现 `AggregateStatLoggerBase` 工厂，vLLM 主进程会一次性把所有 engine 的 stats 喂进来；按 `engine_idx` 路由到不同分桶；UI 加实例选择器
- **Sidecar/Centralized**：每个 vLLM Pod 各起一个 agent，server 端按 `instance_id` 聚合，天然多实例视图

**结论**：**Sidecar 及更高级模式对多进程多 GPU 是 free 的**，这是另一个推动 v0.2 的理由。

---

## 16. 安全模型

### 16.1 按模式区分

| 模式 | API 默认 bind | 认证 |
|---|---|---|
| Embedded | 127.0.0.1 | 无 |
| Sidecar | Pod 内网 | 可选 token |
| Centralized | Service ClusterIP | 强烈建议 OIDC/token |
| Stateless | 不暴露 API（CLI 触发） | 不适用 |

### 16.2 关键配置

```bash
PPING_LANG_BIND_HOST=0.0.0.0       # 容器场景
PPING_LANG_API_TOKEN=<random>      # 启用 token 认证
PPING_LANG_DISABLE_API=1           # 仅 OTel 出口
PPING_LANG_READONLY=1              # 只读 API（不能改规则）
```

`/api/rules` 写操作默认要 token；读操作可不要（方便监控集成）。

### 16.3 Centralized 模式 SSO（v0.3）

集成 OIDC，对接公司 IAM。

---

## 17. 配置与部署

### 17.1 配置层级

优先级从高到低：
1. 环境变量（`PPING_LANG_*`）
2. 配置文件（`./pping-lang.yaml` 或 `/etc/pping-lang/config.yaml`）
3. ConfigMap（K8s）
4. 内置默认值

### 17.2 关键配置项

```yaml
mode: embedded   # embedded | remote | otel

api:
  enabled: true
  host: 127.0.0.1
  port: 8765
  token: null

storage:
  data_dir: /var/lib/pping-lang   # K8s 友好默认值
  retention_days: 30

sampling:
  nvml_interval_ms: 100
  rule_eval_interval_ms: 1000
  sink_flush_interval_s: 5

remote:
  endpoint: null
  protocol: grpc   # grpc | http

otel:
  enabled: false
  endpoint: null

rules:
  defaults_enabled: true
  custom_rules_path: null
```

### 17.3 部署形态

| 形态 | 命令 |
|---|---|
| 本地 (Embedded) | `pip install pping-lang; vllm serve ...` |
| Docker compose (Sidecar) | 提供 `docker-compose.yaml` 模板 |
| K8s (Centralized) | 提供 Helm chart：`helm install pping-lang ./pping-lang-chart` |
| CLI 离线分析 | `pping-lang analyze --from ... --to ...` |

---

## 18. 测试策略

### 18.1 单元测试

- 规则引擎：mock 指标流、窗口聚合、模板渲染
- Sink 接口：mock 三种 Sink 实现
- 存储层：临时 DuckDB
- API：FastAPI TestClient

### 18.2 集成测试

- mock `StatLoggerBase`，喂合成 stats
- 真实 vLLM + 轻量模型，CI 跑 5 分钟标准 workload
- K8s 集成测试：kind 集群起 Centralized 模式，验证多 agent 上报

### 18.3 性能基准

CI 跑标准 workload，吞吐下降 > 3% 阻塞合并。每个 release tag 在 README 更新 benchmark 数据。

### 18.4 兼容性矩阵

**Python**：3.10 / 3.11 / 3.12
**Kubernetes**：1.25+（v0.2）

#### vLLM 字段可用性 — 实测列

WSL2 + Ubuntu 22.04 + RTX 4070 Laptop GPU 上跑 Qwen2.5-0.5B-Instruct，每个版本至少 100 个 chat completion 请求 + 持续 NVML 采样。

| vLLM | `SchedulerStats` | `IterationStats` | `cudagraph_stats` | `perf_stats` | `FinishedRequestStats` | plugin entry-point | 备注 |
|:--|:--|:--|:--|:--|:--|:--|:--|
| **0.20.2** | ✓ | ✓ | ✓ 字段对齐 | ✓ | ✓ | ✓ | 推荐版本；所有 marquee 指标可用 |
| **0.13.0** | ✓ | ✓ | ✓ 但字段重命名（`num_padded_tokens` 等不匹配） | ✗ 整字段不存在 | ✓ | ✓ | 降级可用；MFU/Roofline/padding_ratio 不会派生但不崩 |
| < 0.13 | n/a | n/a | n/a | n/a | n/a | n/a | v1 架构 + `vllm.stat_logger_plugins` 入口不存在，不支持 |

**关键 metric 的版本可用性**：

| pping-lang metric | 数据源 | 0.20.x | 0.13.0 |
|:--|:--|:--|:--|
| `gpu.utilization_pct` | NVML | ✓ | ✓ |
| `gpu.mem_used_bytes` / power / temp / clocks | NVML | ✓ | ✓ |
| `vllm.scheduler.running_reqs` / waiting / kv_cache_usage | SchedulerStats | ✓ | ✓ |
| `vllm.scheduler.prefix_cache_hit_ratio` | SchedulerStats.prefix_cache_stats | ✓ | ✓ |
| `vllm.iter.gen_tokens` / preempted_reqs | IterationStats | ✓ | ✓ |
| `vllm.req.ttft_ms` / tpot_ms / itl_ms / e2e_latency_ms | IterationStats lists + FinishedRequestStats | ✓ | ✓ |
| `vllm.cudagraph.padding_ratio`（派生） | cudagraph_stats | ✓ | ⚠ 0 ratio（字段名漂移） |
| `vllm.perf.mfu_ratio`（派生） | perf_stats × GPU peak 表 | ✓ | ✗ |
| `vllm.perf.mem_bw_util_ratio`（派生） | perf_stats × GPU peak 表 | ✓ | ✗ |
| Roofline 散点 | perf_stats | ✓ | ✗ |

**实测的 Sink 容量瓶颈**（v0.2 修复目标）：

| 配置 | 单进程负载 | dropped/min | 状态 |
|:--|:--|:--|:--|
| 默认（NVML 100ms、queue 16384、flush 5s） | 32 concurrent chat × 60s sustained | ~10k+ | 队列爆 |
| 推荐（NVML 1s、queue 65536、flush 0.5s） | 同上 | < 100 | 稳态 |

**规则评估实现迭代实测**（v0.1 Day 17-18 真负载验证）：

| 实现 | 真 vLLM + 32 并发 sustained 时 median eval | 备注 |
|:--|:--|:--|
| 原始：10 条规则各 1 次 DuckDB QUANTILE_CONT | ~90 ms | baseline，每条独立 round-trip |
| 当前：1 次 SQL 拉数据 + Python 内存聚合 | ~25 ms | 采用 |
| 备选 A：fetchnumpy + np.percentile | ~44 ms | string 列 mask 拉慢 |
| 备选 B：UNION ALL 让 DuckDB 在 C 层算 | ~76 ms | DuckDB 不并行各分支，跟 #A 同速但多一层语法成本 |

**真要继续压到 < 5 ms 必须改架构**——用内存环形缓冲（per-metric 滑动窗口 deque）替代每 tick 查 DuckDB。这是 v0.2 工作，因为：
1. 环形缓冲需要规则引擎直接订阅 sink 流（而非读 DuckDB），跨进程的 Centralized 模式下要重新设计
2. 25 ms 占 1s 评估间隔的 2.5%，**v0.1 实际可接受**——没人会因此关掉规则引擎

---

## 19. 风险与对冲

| 风险 | 影响 | 对冲 |
|---|---|---|
| vLLM `StatLoggerBase` 接口变化 | 工具失效 | pin 版本、try/except 兼容多版本、CI 矩阵 |
| vLLM 自己加诊断 | 价值削弱 | 保持差异化（GPU 数据、报告、规则可视化、多模式） |
| Langfuse/Phoenix 进入 | 竞争 | 速度优先，聚焦 vLLM 做深 |
| NVML 在共享 GPU 受限 | 部分用户差体验 | 降级到仅 vLLM 数据，明确告知 |
| Centralized 模式 server 单点 | 生产可用性 | v0.3 加 HA（多副本 + leader election） |
| Sink 抽象设计错 | 模式扩展困难 | v0.1 就写抽象，即使只实现 LocalSink |

---

## 20. 项目命名

**已定**：`pping-lang`（PyPI 包名 / repo 名 / CLI 命令均用连字符；Python 模块名为 `pping_lang`）。

历史候选（保留备查）：`llmprof` / `vprofile` / `vllm-prof` / `infersight` / `whyslow`。

---

## 21. 开发计划（修正版）

v0.1 仍 3 周完成（Embedded 模式），但**代码就要写好 Sink 抽象**。

### v0.1（Embedded 模式，3 周）

**第 1 周：核心闭环 + Sink 抽象**

| 天 | 任务 |
|---|---|
| 1 | 项目骨架 + pyproject.toml + entry point + plugin 加载验证 + GPU peak 性能常量表 |
| 2 | Sink 抽象设计（按 RFC §3 fire-and-forget 契约）+ LocalSink + StatLogger 接入 |
| 3 | NVML 采样线程 + DuckDB schema + cudagraph_stats / perf_stats 采集 + 派生指标（padding_ratio / mfu_ratio / mem_bw_util_ratio） |
| 4 | 规则引擎 + 11 条默认规则（含 high-cudagraph-padding、low-mfu）+ 终端打印 |
| 5 | 端到端 demo（重点演示 padding-ratio 和 MFU 诊断） |

**第 2 周：UI + 报告**

| 天 | 任务 |
|---|---|
| 6 | FastAPI server + 核心 API 端点 |
| 7 | 单文件 HTML UI + 实时仪表盘 |
| 8 | 规则编辑 UI（CRUD + 测试） |
| 9 | 规则热加载 |
| 10 | 报告生成 + 配置审计 |

**第 3 周：打磨发布**

| 天 | 任务 |
|---|---|
| 11 | OTel 输出 + GenAI semconv 翻译 |
| 12 | 性能 benchmark + 自我观测 |
| 13 | 文档：README、example、case study 博客 1 |
| 14 | PyPI 发布 + Twitter/HN |
| 15 | 缓冲 |

### v0.2（Sidecar + Centralized 模式，~4 周）

- `pping-lang-server` 独立 CLI 实现
- RemoteSink（gRPC 上报）
- 官方 Docker image
- Helm chart
- K8s 集成测试
- 多实例 UI（实例选择器、聚合视图）
- 复合规则（all/any）
- 多实例报告对比

### v0.3（Stateless / OTel-native，~3 周）

- OTelSink 完善
- `pping-lang analyze` CLI（从 Prometheus/Tempo 读数据）
- Stateless 模式文档和 K8s CronJob 模板
- Centralized 模式 HA（leader election）

### v0.4 ~ v0.5（不限时）

- v0.4：Roofline 图、异常检测（基于历史基线）、SSO
- v0.5：规则市场、SGLang 实验性支持、对比模式

---

## 22. 仓库结构

```
pping_lang/
├── pyproject.toml
├── README.md
├── LICENSE (Apache 2.0)
├── CHANGELOG.md
├── CONTRIBUTING.md
├── pping_lang/
│   ├── __init__.py
│   ├── plugin.py              # entry point: PpingLangStatLogger
│   ├── collector/
│   │   ├── nvml.py
│   │   ├── buffer.py
│   │   └── vllm_stats.py
│   ├── sink/
│   │   ├── base.py            # Sink Protocol
│   │   ├── local.py           # Embedded 模式
│   │   ├── remote.py          # Sidecar/Centralized 模式
│   │   └── otel.py            # Stateless 模式
│   ├── server/                # pping-lang-server 独立进程（v0.2）
│   │   ├── main.py            # CLI entry
│   │   ├── api/
│   │   ├── storage/
│   │   └── ingest/            # 接收 agent 上报
│   ├── analyzer/              # pping-lang-analyzer (v0.3)
│   │   ├── main.py
│   │   └── prometheus.py
│   ├── rules/
│   │   ├── engine.py
│   │   ├── schema.py
│   │   └── defaults.json
│   ├── report/
│   │   ├── generator.py
│   │   ├── analysis.py
│   │   └── templates/
│   ├── otel/
│   │   ├── exporter.py
│   │   └── semconv.py
│   ├── ui/
│   │   └── index.html
│   └── config.py
├── deploy/
│   ├── docker/
│   │   ├── Dockerfile.server
│   │   └── docker-compose.yaml
│   └── k8s/
│       └── chart/             # Helm chart (v0.2)
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/                   # K8s e2e (v0.2)
│   └── benchmark/
├── examples/
│   ├── embedded/
│   ├── sidecar/
│   ├── centralized/
│   └── stateless/
└── docs/
    ├── design.md (本文档)
    ├── deployment-modes.md
    ├── rules-reference.md
    └── faq.md
```

---

## 23. 开放问题

1. **报告里"影响估计"**：v0.1 启发式规则；v0.5 考虑轻量回归模型
2. **是否给 vLLM upstream**：核心采集能力可贡献，差异化部分保留
3. **Centralized 模式的 HA**：v0.3 通过 etcd/leader election 实现
4. **数据保留策略可配置**：按指标重要程度分级保留？
5. **规则配置导入导出**：v0.2 加，方便团队共享
6. **是否做多语言（i18n）**：v0.1 英文，国际优先

---

## 24. 面试讲解要点

把这个项目讲成完整故事的关键叙述线：

### 24.1 问题识别

"vLLM 已经有 80% 的指标，但用户不知道怎么解读。GPU util 是 duty cycle 不是吞吐——业界已知坑没工具解决。"

### 24.2 调研深度

- vLLM 0.20 的 `vllm.stat_logger_plugins` 是新机制，社区文章还在讲老做法
- v1 里 `LoggingStatLogger.log()` 几乎不被调用（issue #20175）
- vLLM 故意 pin `SpanAttributes` 不跟进 OTel GenAI semconv

### 24.3 架构演进（v0.2 新增的故事亮点）

"v0.1 设计时我做了 fat plugin —— 所有功能都塞在 vLLM 进程里。后来意识到这在 K8s 多副本场景下架构不对：Pod 是 cattle，本地状态是反模式。所以重新设计成 Sink 抽象，让一份代码支持四种部署模式：Embedded（本地）、Sidecar（K8s 单 Pod）、Centralized（K8s 多副本）、Stateless（OTel-native）。"

这段话浓缩了多个面试要点：架构能力、cloud native 思维、抽象设计、产品判断。

### 24.4 工程判断

- 不用 json-rules-engine 这类通用引擎——没有时间窗口、过度设计
- 用 DuckDB 不用 SQLite——大数据量查询差一个数量级
- 单文件 HTML + Alpine.js 不用 React——MVP 不需要构建工具链
- v0.1 就抽出 Sink 接口——多写一天，v0.2 加 Sidecar 时省一周

### 24.5 性能意识

- record() 热路径 < 50μs，所有重活异步
- 规则评估节流到每秒一次
- 自己输出 overhead 指标

### 24.6 部署经验

- Docker 场景"工具=应用"还成立
- K8s 场景必须接受"Pod 是 cattle，本地状态是反模式"
- 多副本 dashboard 不能在 Pod 内部，必须集中
- PVC vs ConfigMap vs OTel 后端的存储选择

每条都能展开 30 分钟。

---

## 25. 版本演进路线图

直观看到项目长期方向：

```
v0.1 (3 weeks)
└── Embedded 模式
    ├── 单进程内全功能
    ├── 本地 DuckDB
    ├── 个人开发者友好
    └── 抢占早期用户、建立认知

v0.2 (~4 weeks after v0.1)
└── 生产化
    ├── pping-lang-server 独立进程
    ├── Sidecar 模式（Docker compose / K8s 单副本）
    ├── Centralized 模式（K8s 多副本聚合）
    ├── 官方 Docker image + Helm chart
    ├── 复合规则
    └── 跨越个人到团队的使用门槛

v0.3 (~3 weeks after v0.2)
└── 云原生
    ├── Stateless / OTel-native 模式
    ├── pping-lang-analyzer CLI
    ├── HA support
    ├── SSO
    └── 进入大公司生产环境

v0.4+ (不限时)
└── 深度
    ├── Roofline 图
    ├── 异常检测 ML
    ├── 规则市场
    ├── 多 backend 支持（SGLang 等）
    └── 报告对比/diff
```

**关键里程碑**：
- v0.1：抢眼球（demo 容易传播）
- v0.2：可生产（团队/平台开始采用）
- v0.3：进大厂（observability 栈天然契合）
- v0.4+：建生态（社区贡献）

---

## 附录 A：关键依赖

```toml
[project]
dependencies = [
    "vllm>=0.20.0,<0.22.0",
    "duckdb>=1.0.0",
    "pynvml>=11.5.0",
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "opentelemetry-api>=1.27.0",
    "opentelemetry-sdk>=1.27.0",
    "opentelemetry-exporter-otlp>=1.27.0",
    "plotly>=5.18.0",
    "jinja2>=3.1.0",
    "pydantic>=2.0.0",
]

[project.optional-dependencies]
server = ["grpcio>=1.60.0", "protobuf>=4.0.0"]    # v0.2
analyzer = ["prometheus-api-client>=0.5.0"]        # v0.3
```

## 附录 B：相关项目对比

| 项目 | 定位 | 与 pping_lang 关系 |
|---|---|---|
| vLLM 内置 metrics/tracing | 数据生产者 | 我们消费它的数据 |
| Langfuse | LLM trace 后端 | 我们输出 OTel，可对接 |
| Phoenix (Arize) | LLM eval + observability | 偏 eval，不重叠 |
| Grafana + Prometheus | 通用监控 | 通用，我们做 LLM 特化诊断 |
| Helicone | LLM 网关 + 监控 | 偏请求级，不做内部指标 |
| GuideLLM | vLLM benchmarking | 离线 benchmark，不是诊断 |

## 附录 C：v0.1 → v0.2 关键变更摘要

| 维度 | v0.1 | v0.2 |
|---|---|---|
| 架构核心 | fat plugin（一切在 vLLM 进程） | Sink 抽象 + 多模式 |
| 部署模式 | 仅 Embedded | Embedded + Sidecar + Centralized |
| API server 位置 | 永远在 vLLM 进程内 | 按模式定（plugin / sidecar / 独立 Deployment） |
| 存储 | `~/.pping-lang` 硬编码 | 配置驱动，K8s 友好默认 |
| 多副本支持 | 不行（架构不对） | Centralized 模式天然支持 |
| K8s 友好 | 否 | 是（Helm chart + 多模式） |
| 复合规则 | 不支持 | 支持 |
| 多实例 UI | 单实例 | 实例选择器 + 聚合视图 |

---

**文档结束。审阅、讨论、实施。**
