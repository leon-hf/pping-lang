# pping-lang Pre-Implementation RFC — 5 项编码前必须收口的决策

**版本**：v1 (2026-05-14)
**配套设计文档**：[pping-lang-design-v0.2.md](pping-lang-design-v0.2.md)
**目的**：消除 v0.1 编码第一周内会卡住的接口/数据/集成不确定性
**读者**：v0.1 实施者

---

## 决策摘要

| # | 议题 | 决定 |
|---|---|---|
| 1 | MetricPoint / Diagnosis schema | `dataclass(slots=True, frozen=True)`，字段固定（见 §1） |
| 2 | 多 worker IPC | **v0.1 不做 IPC**，复用 vLLM `AggregateStatLoggerBase`；NVML 在 EngineCore 进程内按 GPU index 采样 |
| 3 | Sink 接口契约 | `push_*` 必须 fire-and-forget（≤ 5μs），基类提供默认 ring buffer + bg flush 线程 |
| 4 | vLLM 接入 | entry point group = `vllm.stat_logger_plugins`；继承 `StatLoggerBase`；字段白名单见 §4 |
| 5 | 指标命名 | 单一规范 `<domain>.<subsystem>.<name>_<unit>`，OTel 导出加 `pping_lang.` 前缀，单一常量模块兜底 |

---

## 1. 数据模型 Schema

### 1.1 MetricPoint（热路径，高频）

```python
# pping_lang/types.py
from dataclasses import dataclass
from typing import Mapping

@dataclass(slots=True, frozen=True)
class MetricPoint:
    ts_ns: int                       # time.monotonic_ns() at sample
    name: str                        # canonical metric name (see §5)
    value: float                     # all metrics are float (counts cast up)
    engine_idx: int = 0              # vLLM engine index (DP 场景区分)
    gpu_idx: int = -1                # -1 = not GPU-scoped
    labels: Mapping[str, str] | None = None  # 极少用，避免 dict 构造
```

**关键约束**：
- `frozen=True` + `slots=True`：减少内存、避免误改、比 namedtuple 略慢但可读性更好
- `value` 一律 float：DuckDB schema 用 DOUBLE，避免每个 metric 都判类型
- `labels` 默认 `None`：只在确实有维度时才构造 dict（如 spec decode 的 `pos` 维度）
- **不带 instance_id**：由 Sink 在出站边界统一打上，进程内不重复

### 1.2 Diagnosis（低频，规则触发时）

```python
from typing import Literal

Severity = Literal["info", "warning", "critical"]

@dataclass(slots=True, frozen=True)
class Diagnosis:
    ts_ns: int
    rule_id: str
    severity: Severity
    triggered_value: float
    threshold: float
    window_seconds: int
    message: str                     # 已渲染（rule.message.format(**ctx)）
    suggestion: str
    engine_idx: int = 0
    gpu_idx: int = -1
    context: Mapping[str, float] | None = None  # 关联指标快照（最近一次值）
```

**为什么 message 在生产端渲染**：避免下游（HTML/UI/OTel log）各自重复模板逻辑；多语言以后再说。

### 1.3 序列化

进程内不序列化（直接传对象引用）。出 Sink 时：
- LocalSink → 直接写 DuckDB（按字段映射）
- RemoteSink → msgpack（比 JSON 快 3-5×，比 protobuf 省 schema 编译）
- OTelSink → 转 OTel SDK 的 `Measurement` / `LogRecord`

---

## 2. 多 Worker IPC：v0.1 不做

### 2.1 背景澄清

设计文档 §15.1 写"rank 0 启 FastAPI，其他 worker 通过 IPC 上报"——这是基于错误前提。重新核查 vLLM v1 架构：

- **TP（tensor parallel）多 worker**：worker 是 EngineCore 的子进程，stats 通过 vLLM 内部 ZMQ 已经汇总到 EngineCore 主进程。**StatLogger 跑在 EngineCore，看到的就是聚合后的 SchedulerStats**，无需我们再做 IPC。
- **DP（data parallel）多 engine**：vLLM 提供 `AggregateStatLoggerBase` 工厂，主进程拿到所有 engine 的 stats。**v0.1 仅声明 PerEngineStatLoggerFactory（DP=1），DP > 1 报错并在文档明示 v0.2 支持。**
- **NVML 采样位置**：NVML 是 GPU 外部接口，**任何进程都能查任何 GPU**（`pynvml.nvmlDeviceGetHandleByIndex(i)`），不需要跑在 worker 进程里。我们在 EngineCore 进程开一个 NVML 线程，按 `CUDA_VISIBLE_DEVICES` 解析出本 engine 占用的 GPU index 列表，逐个采样即可。

### 2.2 v0.1 决定

```
┌─────────────────────────────────────────┐
│ EngineCore 进程 (单一)                   │
│  ├── vLLM scheduler / model executor    │
│  ├── PpingLangStatLogger                  │
│  │   ├── Collector                      │
│  │   │   ├── 同步 hook (record())        │
│  │   │   └── NVML 后台线程               │
│  │   └── Sink (LocalSink)               │
│  └── FastAPI 线程（uvicorn in thread）   │
└─────────────────────────────────────────┘
   vLLM Worker 子进程 1..N（不感知 pping_lang）
```

**收益**：v0.1 完全没有跨进程通信问题，第一周日历不用动。

### 2.3 v0.2 才需要面对的情况

- DP > 1：实现 AggregateStatLoggerBase，按 engine_idx 路由
- Sidecar：进程边界变成 OTLP/gRPC（即 RemoteSink），不是裸 IPC
- 真出现"NVML 必须在 worker 跑"（如 GPU 隔离强制）：UDS + msgpack 帧，**届时再选**

**结论**：v0.1 删除"IPC 抽象"这个工作项，§15.1 的描述同步修正。

---

## 3. Sink 接口契约

### 3.1 核心约束（写在接口 docstring，不只是 RFC）

> `Sink.push_*` 方法**必须**在 5μs 内返回，不得阻塞 vLLM 热路径。
> 任何 I/O、序列化、批量、网络都必须在 Sink 内部异步线程完成。
> 队列溢出时丢最旧数据，并在 `pping_lang.sink.dropped_total` 自我观测指标计数。

### 3.2 接口定义

```python
# pping_lang/sink/base.py
from abc import ABC, abstractmethod
from collections import deque
from threading import Thread, Event
from typing import Iterable

class Sink(ABC):
    """All push_* methods MUST return in <5μs. See RFC §3."""

    def __init__(self, queue_size: int = 16384, flush_interval_s: float = 5.0):
        self._metric_q: deque[MetricPoint] = deque(maxlen=queue_size)
        self._diag_q: deque[Diagnosis] = deque(maxlen=1024)
        self._flush_interval = flush_interval_s
        self._stop = Event()
        self._dropped = 0
        self._thread = Thread(target=self._run, daemon=True, name=f"{type(self).__name__}-flush")
        self._thread.start()

    def push_metric(self, p: MetricPoint) -> None:
        # deque.append with maxlen is O(1) and atomic under GIL
        if len(self._metric_q) == self._metric_q.maxlen:
            self._dropped += 1
        self._metric_q.append(p)

    def push_diagnosis(self, d: Diagnosis) -> None:
        if len(self._diag_q) == self._diag_q.maxlen:
            self._dropped += 1
        self._diag_q.append(d)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self._flush_interval * 2)
        self._drain()  # final flush

    def _run(self) -> None:
        while not self._stop.wait(self._flush_interval):
            self._drain()

    def _drain(self) -> None:
        # Snapshot then process (deque.copy + clear is atomic under GIL)
        metrics = list(self._metric_q)
        self._metric_q.clear()
        diags = list(self._diag_q)
        self._diag_q.clear()
        if metrics or diags:
            try:
                self._flush(metrics, diags)
            except Exception:
                # Never propagate to vLLM. Log and continue.
                logger.exception("Sink flush failed; data dropped")

    @abstractmethod
    def _flush(self, metrics: list[MetricPoint], diags: list[Diagnosis]) -> None: ...
```

### 3.3 子类只实现 `_flush`

```python
class LocalSink(Sink):
    def __init__(self, db_path: str, **kw):
        super().__init__(**kw)
        self._conn = duckdb.connect(db_path)

    def _flush(self, metrics, diags):
        if metrics:
            self._conn.executemany(
                "INSERT INTO metrics VALUES (?, ?, ?, ?, ?, ?)",
                [(m.ts_ns, m.engine_idx, m.gpu_idx, m.name, m.value, json.dumps(m.labels) if m.labels else None) for m in metrics]
            )
        if diags:
            self._conn.executemany("INSERT INTO diagnoses VALUES (...)", [...])
```

### 3.4 关键设计抉择记录

| 抉择 | 选 | 弃 | 理由 |
|---|---|---|---|
| 队列结构 | `collections.deque(maxlen=N)` | `queue.Queue` / `asyncio.Queue` | deque + maxlen 在 GIL 下 O(1) 原子 append，无锁开销 |
| 溢出策略 | 丢最旧 | 阻塞 / 丢最新 | 监控数据"最近的最有价值"，但保留最近 N 比阻塞热路径更安全 |
| 异常 | 内部吞掉 + log | 上抛 | 设计目标 §3.1："任何 bug 不能拖垮 vLLM" |
| Diag 队列 | 独立 1024 | 与 metric 共用 | Diag 频率低、价值高，避免被 metric 涨潮挤掉 |
| 序列化时机 | `_flush` 内 | `push_*` 内 | 序列化代价不放热路径 |

---

## 4. vLLM 接入 & 字段白名单

### 4.1 Entry point 注册

`pyproject.toml`：

```toml
[project.entry-points."vllm.stat_logger_plugins"]
pping_lang = "pping_lang.plugin:PpingLangStatLogger"
```

> Entry point group 字符串：`vllm.stat_logger_plugins`（确认源：`vllm/plugins/__init__.py` 中 `STAT_LOGGER_PLUGINS_GROUP = "vllm.stat_logger_plugins"`）

### 4.2 StatLoggerBase 实际签名

来自 `vllm/v1/metrics/loggers.py`（main 分支，2026-05-14 抓取）：

```python
class StatLoggerBase(ABC):
    @abstractmethod
    def __init__(self, vllm_config: VllmConfig, engine_index: int = 0): ...

    @abstractmethod
    def record(
        self,
        scheduler_stats: SchedulerStats | None,
        iteration_stats: IterationStats | None,
        mm_cache_stats: MultiModalCacheStats | None = None,
        engine_idx: int = 0,
    ): ...

    @abstractmethod
    def log_engine_initialized(self): ...

    def log(self): ...                                   # 几乎不被调用 (issue #20175)
    def record_sleep_state(self, is_awake: int, level: int): ...
```

**实施要点**：
- 所有逻辑放 `record()`，不放 `log()`
- `__init__` 不能做重 I/O（启 FastAPI、连 DuckDB），延迟到 `log_engine_initialized()`
- `record_sleep_state` v0.1 实现成 no-op（仅记录 transition，不影响 metric）
- 工厂用 `PerEngineStatLoggerFactory`（DP=1 限制）

### 4.3 vLLM stats 字段 → 我们的 metric 映射

#### SchedulerStats（每 step）

| vLLM 字段 | 我们的 metric name | 说明 |
|---|---|---|
| `num_running_reqs` | `vllm.scheduler.running_reqs` | gauge |
| `num_waiting_reqs` | `vllm.scheduler.waiting_reqs` | gauge |
| `num_skipped_waiting_reqs` | `vllm.scheduler.skipped_waiting_reqs` | gauge |
| `step_counter` | （不导出，用于 dt 计算） | — |
| `current_wave` | `vllm.scheduler.current_wave` | gauge |
| `kv_cache_usage` | `vllm.scheduler.kv_cache_usage_ratio` | 0..1 |
| `prefix_cache_stats.hit_rate` | `vllm.scheduler.prefix_cache_hit_ratio` | 派生：hits / queries |
| `prefix_cache_stats.preempted_*` | `vllm.scheduler.prefix_cache_preempted_*` | counter delta |
| `kv_cache_eviction_events` (len) | `vllm.scheduler.kv_evict_events` | counter delta |
| `spec_decoding_stats.num_accepted_tokens` | `vllm.spec.accepted_tokens` | counter delta |
| `spec_decoding_stats.num_draft_tokens` | `vllm.spec.draft_tokens` | counter delta |
| `cudagraph_stats.num_unpadded_tokens` | `vllm.cudagraph.unpadded_tokens` | counter delta |
| `cudagraph_stats.num_padded_tokens` | `vllm.cudagraph.padded_tokens` | counter delta |
| `cudagraph_stats.num_paddings` | `vllm.cudagraph.paddings` | counter delta |
| `cudagraph_stats.runtime_mode` | `vllm.cudagraph.runtime_mode` | label only（FULL/PIECEWISE） |
| **派生** | `vllm.cudagraph.padding_ratio` | `(padded - unpadded) / padded`，0..1，**duty-cycle 诊断关键** |
| `perf_stats.num_flops_per_gpu` | `vllm.perf.flops_per_gpu` | counter delta |
| `perf_stats.num_read_bytes_per_gpu` | `vllm.perf.read_bytes_per_gpu` | counter delta |
| `perf_stats.num_write_bytes_per_gpu` | `vllm.perf.write_bytes_per_gpu` | counter delta |
| **派生** | `vllm.perf.mfu_ratio` | `flops_per_step / (gpu_peak_flops * step_time)`，0..1 |
| **派生** | `vllm.perf.mem_bw_util_ratio` | `(read+write)_bytes / (gpu_peak_bw * step_time)`，0..1 |
| `perf_stats.debug_stats` | （v0.1 跳过） | 字段不稳定 |
| `waiting_lora_adapters` (count) | `vllm.lora.waiting_adapters` | gauge |
| `running_lora_adapters` (count) | `vllm.lora.running_adapters` | gauge |

#### IterationStats（每 step）

| vLLM 字段 | 我们的 metric name | 处理 |
|---|---|---|
| `num_generation_tokens` | `vllm.iter.gen_tokens` | counter delta |
| `prompt_token_stats.total` | `vllm.iter.prompt_tokens` | counter delta |
| `prompt_token_stats.local_cache_hit` | `vllm.iter.prompt_cache_hit_tokens` | counter delta |
| `prompt_token_stats.external_kv_transfer` | `vllm.iter.prompt_kv_transfer_tokens` | counter delta |
| `num_preempted_reqs` | `vllm.iter.preempted_reqs` | counter delta |
| `num_corrupted_reqs` | `vllm.iter.corrupted_reqs` | counter delta |
| `time_to_first_tokens_iter` (list) | `vllm.req.ttft_ms` | 每个元素一个点 |
| `inter_token_latencies_iter` (list) | `vllm.req.itl_ms` | 每个元素一个点 |
| `iteration_timestamp` | （不导出，用于 dt 校验） | — |

#### FinishedRequestStats（每个完成请求一份，attached to IterationStats.finished_requests）

| vLLM 字段 | 我们的 metric name |
|---|---|
| `e2e_latency` | `vllm.req.e2e_latency_ms` |
| `queued_time` | `vllm.req.queued_ms` |
| `prefill_time` | `vllm.req.prefill_ms` |
| `inference_time` | `vllm.req.inference_ms` |
| `decode_time` | `vllm.req.decode_ms` |
| `mean_time_per_output_token` | `vllm.req.tpot_ms` |
| `num_prompt_tokens` | `vllm.req.prompt_tokens` |
| `num_generation_tokens` | `vllm.req.gen_tokens` |
| `num_cached_tokens` | `vllm.req.cached_tokens` |
| `finish_reason` | label on counter `vllm.req.finished_total{reason=...}` |
| `is_corrupted` | （并入 corrupted_reqs counter） |

> 单位换算：vLLM 的 `*_time` / `*_latency` 是秒（float），我们统一存毫秒。换算在 collector 里完成。

#### NVML（每 100ms，按 GPU）

| 来源 | metric name |
|---|---|
| `nvmlDeviceGetUtilizationRates().gpu` | `gpu.utilization_pct` |
| `nvmlDeviceGetUtilizationRates().memory` | `gpu.mem_util_pct` |
| `nvmlDeviceGetMemoryInfo().used` | `gpu.mem_used_bytes` |
| `nvmlDeviceGetPowerUsage()` | `gpu.power_w`（mW → W） |
| `nvmlDeviceGetTemperature(GPU)` | `gpu.temp_c` |
| `nvmlDeviceGetClockInfo(SM)` | `gpu.sm_clock_mhz` |
| `nvmlDeviceGetClockInfo(MEM)` | `gpu.mem_clock_mhz` |

### 4.4 版本兼容

- 实测基线：vLLM 0.20.x（main 分支当前抓取）
- 字段缺失保护：`getattr(stats, "field_name", None)`，缺则跳过该 metric
- entry point group 字符串如未来变更：`pyproject.toml` 改一行 + 兼容矩阵跑测

---

## 5. 指标命名规范

### 5.1 单一规范

```
<domain>.<subsystem>?.<name>_<unit_suffix>?
```

- **domain**：`gpu`、`vllm`、`pping_lang`（自我观测）
- **subsystem**（可选）：`scheduler`、`iter`、`req`、`spec`、`cudagraph`、`lora`、`sink`
- **name**：snake_case
- **unit_suffix**（强制有单位的指标必须带）：

| suffix | 含义 |
|---|---|
| `_ms` | 毫秒 |
| `_s` | 秒（避免，能用 ms 就 ms） |
| `_bytes` | 字节 |
| `_pct` | 百分比 0..100 |
| `_ratio` | 比值 0..1 |
| `_w` | 瓦特 |
| `_c` | 摄氏度 |
| `_mhz` | 兆赫 |
| `_total` | 累计 counter |
| 无 suffix | 无量纲（计数、id） |

### 5.2 内部 vs OTel 导出

- **内部**（DuckDB 列、规则 metric 字段、API 响应、UI）：直接用规范名，**不加前缀**
- **OTel 导出**：机械加 `pping_lang.` 前缀
  - `gpu.utilization_pct` → `pping_lang.gpu.utilization_pct`
  - `vllm.scheduler.kv_cache_usage_ratio` → `pping_lang.vllm.scheduler.kv_cache_usage_ratio`

> 不做单位转换（如 pct → ratio）。OTel semconv 偏好 ratio，但翻译会引入两套名字，不值得。

### 5.3 单一常量源

```python
# pping_lang/metrics_catalog.py
class M:
    # GPU
    GPU_UTIL_PCT = "gpu.utilization_pct"
    GPU_MEM_USED_BYTES = "gpu.mem_used_bytes"
    GPU_POWER_W = "gpu.power_w"
    # ... 全部列出

# 白名单（规则引擎用）
ALLOWED_METRICS: frozenset[str] = frozenset(
    v for k, v in vars(M).items() if not k.startswith("_") and isinstance(v, str)
)
```

- 规则的 `condition.metric` 字段必须命中 `ALLOWED_METRICS`，否则规则保存时 422
- `/api/metrics/available` 直接返回 `sorted(ALLOWED_METRICS)`
- OTel exporter 遍历 `M` 注册 instruments，用同一份名字加前缀

### 5.4 同步修正设计文档

下列处需要在 v0.3 设计文档迭代时同步：

| 位置 | 原文 | 改为 |
|---|---|---|
| §10.2 example | `gpu.utilization_pct` | 保持（已对） |
| §13.2 metrics 列表 | `pping_lang.gpu.utilization` | `pping_lang.gpu.utilization_pct` |
| §13.2 | `pping_lang.gpu.memory_used` | `pping_lang.gpu.mem_used_bytes` |
| §13.2 | `pping_lang.gpu.power_watts` | `pping_lang.gpu.power_w` |
| §15.1 | "rank 0 启 FastAPI，其他 worker 通过 IPC 上报" | "EngineCore 进程内单点；NVML 由 EngineCore 进程统一采" |

---

## 6. 对 v0.1 日历的影响

| 第 1 周 Day | 原计划 | 修正 |
|---|---|---|
| Day 1 | 项目骨架 + entry point + plugin 加载验证 | + 落地 §1 数据类型、§5 metrics_catalog |
| Day 2 | Sink 抽象设计 + LocalSink + StatLogger 接入 | 抽象按 §3 实现（基类带 ring buffer）；StatLogger 按 §4.3 映射表 |
| Day 3 | NVML 采样 + DuckDB schema + 异步批量写 | 删去原 IPC 工作；schema 按 §1.1 调整（加 `gpu_idx`） |
| Day 4 | 规则引擎 + 5 条默认规则 + 终端打印 | 规则 metric 字段通过 ALLOWED_METRICS 校验 |
| Day 5 | 端到端 demo | 不变 |

**净影响**：Day 2-3 工作量略增（基类 + 映射表），Day 3 减负（无 IPC）。**总日历不变**。

---

## 7. 仍未决但不阻塞 v0.1 的事项

| 议题 | 何时决定 |
|---|---|
| 规则触发去重/抑制策略 | 第 2 周 Day 6 写规则引擎时 |
| 窗口聚合实现细节（环形 buffer 还是 query DuckDB） | 第 1 周 Day 4 写规则前一晚 |
| 配置合并策略（深度合并 vs 覆盖） | 第 3 周打磨期 |
| plotly inline HTML 体积优化（CDN fallback？） | 第 2 周 Day 10 出报告时实测 |
| CI 用的轻量模型 | 第 3 周 Day 12 性能基准时定 |
| DP > 1 (AggregateStatLoggerBase) 实施 | v0.2 |
| ~~`cudagraph_stats` / `perf_stats` 字段细节~~ | ✅ 已收口（见 §4.3） |
| `gpu_peak_flops` / `gpu_peak_bw` 常量表（按 GPU 型号） | 第 1 周 Day 4，从 NVML `nvmlDeviceGetName` 查表 |
| MFU / Roofline 派生指标 v0.1 是否包含 | **建议包含**：见 §8 |

---

---

## 8. 附加发现：v0.1 范围扩展（已接受）

**状态**：2026-05-14 决定纳入 v0.1，design v0.2.1 同步修订（§8.1 / §10.4 / §12.1 / §21）。

实测 vLLM `cudagraph_stats` / `perf_stats` 后，发现两个原计划放到 v0.2 的诊断能力其实 v0.1 就能做：

### 8.1 Padding-ratio 诊断（强烈建议 v0.1 加）

`vllm.cudagraph.padding_ratio` 直接揭穿"GPU util 高但实际吞吐低"——这是设计文档 §2.2 的核心卖点之一。

新增第 10 条默认规则：

```json
{
  "id": "high-cudagraph-padding",
  "category": "throughput",
  "severity": "warning",
  "condition": {
    "metric": "vllm.cudagraph.padding_ratio",
    "op": ">",
    "threshold": 0.3,
    "window_seconds": 60,
    "aggregation": "avg"
  },
  "message": "CUDA graph padding 比例 {value:.0%}，约 {value:.0%} 的 GPU 算力浪费在补 0",
  "suggestion": "调小 max_num_seqs 或开启更细粒度的 cudagraph capture（PIECEWISE 模式）"
}
```

成本：派生指标 1 行 + 规则 JSON 1 条。**这就是 pping-lang 区别于 Prometheus+Grafana 的"诊断能力"具象化样本。**

### 8.2 MFU / 内存带宽利用率（建议 v0.1 加）

`perf_stats` 给的是 ground truth FLOPs 和 byte counts，比 NVML duty cycle 准确得多。

需要的支撑工作：
- GPU peak 性能常量表（`pping_lang/hardware.py`）：
  - A100 80G: 312 TFLOPS (BF16), 2039 GB/s
  - H100 80G: 989 TFLOPS (BF16), 3350 GB/s
  - L40S: 362 TFLOPS (BF16), 864 GB/s
  - 等
- 通过 `nvmlDeviceGetName` → 查表得 peak（未识别型号降级到不计算 MFU，给 warning）

新增 `vllm.perf.mfu_ratio` 和 `vllm.perf.mem_bw_util_ratio`，原 §10.4 的 `memory-bw-saturated` 规则（原本无数据源）现在可以真实工作。

**对 §10.4 默认规则的影响**：原 9 条 → 11 条，`memory-bw-saturated` 从"v0.1 无数据可用"变为可用。

### 8.3 v0.1 日历影响

| Day | 增量 |
|---|---|
| Day 1 | + GPU peak 性能常量表（半小时） |
| Day 3 | + perf_stats / cudagraph_stats 采集（已在白名单，~30 分钟） |
| Day 4 | + 派生指标计算 + 2 条新规则（1 小时） |

总计 **+ 约 2 小时**，不影响 Day 5 端到端 demo。

### 8.4 实施前提确认

- `cudagraph_stats` 仅在 vLLM 启动加 `--enable-cudagraph-metrics` 时填充。我们应：
  1. 检测启动参数缺失时打 hint：`[pping-lang] tip: add --enable-cudagraph-metrics for padding diagnostics`
  2. 字段为 None 时跳过派生计算，不告警
- `perf_stats` 同样按 vLLM 默认开关。如非默认开启，同样 hint。

---

**文档结束。本 RFC 落地后即可开始 Day 1 编码。**
