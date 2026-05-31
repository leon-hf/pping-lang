# pping-lang bench: 压测模块设计文档

**版本**：v0.1（设计阶段）
**目标 release**：pping-lang v0.3（embedded + sidecar 模式稳定之后）
**关联文档**：[pping-lang-design-v0.2.md](pping-lang-design-v0.2.md)（主设计）

---

## 1. 一句话定义

> **pping-lang bench 是一个生成 vLLM 流量、同时联动诊断引擎的压测工具——你跑场景、它直接告诉你瓶颈在哪、最优参数应该改到什么**。

不是又一个 `benchmark_serving.py`。区别在最后那半句——业界压测工具都给数字，pping-lang 把数字 + 诊断 + 建议一起给。

---

## 2. 背景：为什么再做一个

社区已经有的：

| 工具 | 定位 | 缺什么 |
|---|---|---|
| vLLM `benchmarks/benchmark_serving.py` | 官方基准 | 只出数字，没诊断 |
| GuideLLM | scenario-driven | 偏离线、和 vLLM 内部状态脱节 |
| llmperf (Anyscale) | 标准化指标 | 同上 |
| locust / k6 + LLM 插件 | 通用压测 | 完全不懂 LLM 语义 |

**它们共同的盲点**：跑完拿到 TTFT/TPOT 数字，**不知道为什么是这个数字、改什么能更好**。

pping-lang 已经有：
- 完整的 vLLM 内部指标采集（KV / padding / MFU / Roofline）
- 10 条诊断规则
- HTML 报告 + 实时仪表盘

把"流量生成"补上，闭环就成了：

```
        ┌─────────────────────────────────────────────┐
        │  pping-lang bench                            │
        │  ┌─────────────┐   ┌─────────────────────┐  │
        │  │ 场景生成器   │──▶│ HTTP client (vLLM) │  │
        │  └─────────────┘   └─────────────────────┘  │
        │         ▲                    │              │
        └─────────┼────────────────────┼──────────────┘
                  │                    ▼
                  │            ┌──────────────┐
                  │            │  vLLM        │
                  │            │  + pping-lang │
                  │            │  StatLogger  │
                  │            └──────┬───────┘
                  │                   │
                  │                   ▼
                  │          ┌────────────────┐
                  └──────────┤ 诊断 + 报告     │
                  自动建议      └────────────────┘
```

---

## 3. 设计目标与非目标

### 3.1 必须做到

- **零额外依赖**：用 `httpx` + `asyncio`，不引入 locust/wrk/grpc
- **静态 + 动态两种模式**：见 §4
- **场景可声明式**：YAML / Python dataclass 都能描述
- **客户端测量与服务端解耦**：客户端测 TTFT 头到 first chunk、TPOT 字节间 dt，不依赖 vLLM 内部上报
- **跑完自动诊断**：调用现有规则引擎 + 报告生成器，输出"瓶颈在哪 / 改哪个参数"
- **跑场景时仪表盘实时可看**：场景元数据 push 到 sink，dashboard 显示"正在跑 scenario X"
- **结果可对比**：两次 bench run 并排 diff（参数 → 指标 → 建议）

### 3.2 明确不做

| 不做 | 原因 |
|---|---|
| 分布式压测（多 client 节点） | vLLM 单实例瓶颈通常在服务端而非客户端；要分布式去用 k6 |
| GUI 场景编辑器 | YAML/CLI 够 v0.1 |
| 跨模型质量评估 | eval 框架的事，与压测正交 |
| 复杂 SLO 引擎 | v0.1 用简单阈值；复杂 SLO 推 v0.2 |
| 推理服务自动调参（auto-tune）| v0.4+，需要先有可靠诊断数据积累 |
| 非 OpenAI 协议 | v0.1 只打 `/v1/completions` + `/v1/chat/completions`；TGI/Triton 推后 |

---

## 4. 核心概念：静态 vs 动态

**这是模块的核心分类，所有后续设计围绕这两类展开。**

### 4.1 静态测试（Static Bench）

**特征**：参数固定、可复现、面向回归与对比。

| 维度 | 含义 |
|---|---|
| Prompt 形状 | 固定 token 数（如 prompt=512, output=128） |
| 并发 | 固定 concurrency=K |
| 时长 | 固定 duration=T 或固定 num_requests=N |
| 到达分布 | 均匀（client 持续保持 K 并发） |

**典型用途**：
- **回归门禁**：换 vLLM 版本前后跑同一 static scenario，对比 TTFT p99 是否退化
- **配置对比**：`max_num_seqs=128` vs `256` 在固定形状下哪个更优
- **基准刷分**：发布性能数据时引用的"标准 workload"
- **CI 集成**：每个 PR 自动跑一遍 mini static bench，5 分钟内出结果

**输出**：
- 单组 (TTFT p50/p95/p99, TPOT p50/p99, throughput, MFU, padding, KV peak)
- 诊断快照：bench 期间触发了哪些规则、各几次
- 对比模式：两个 run 的指标 + 配置 + 诊断 diff

### 4.2 动态测试（Dynamic Bench）

**特征**：参数变化、模拟真实流量、面向瓶颈探索与稳定性。

| 维度 | 形态 |
|---|---|
| Prompt 形状 | 采样分布（lognormal、固定语料、ShareGPT trace） |
| 并发 | 随时间变化（ramp / spike / steady / chaos） |
| 时长 | 由场景定义（通常分钟到小时级） |
| 到达分布 | Poisson、Trace replay、Step function |

**典型用途**：
- **瓶颈探索**：concurrency 从 1 ramp 到 256，看 throughput 拐点在哪、何时 KV 触发抢占
- **稳定性 soak**：steady 流量跑 4 小时，看有没有内存泄漏、抖动
- **Spike 模拟**：突发 100 并发，看抢占率、恢复时间
- **真实 trace replay**：从生产 access log 抽 prompt 长度分布，离线 replay

**输出**：
- 时间序列（TTFT/TPOT/throughput vs concurrency 或 vs t）
- 拐点检测：concurrency 多少时 throughput 饱和、TTFT 开始恶化
- 故障注入对账：spike 期间触发了 `preemption-spike` 规则、持续了 12 秒、TTFT p99 飙到 3.2s
- 配置建议：基于 bench 数据自动给"max_num_seqs 应该到 384"这类结论

### 4.3 选择矩阵

| 你想知道 | 用 |
|---|---|
| "升级 vLLM 后回归没有？" | 静态 + 对比 |
| "我这台 H100 服务我的模型最高能撑多少 QPS？" | 动态 ramp |
| "max_num_seqs 调到多少最好？" | 静态 sweep |
| "凌晨流量低、白天流量高，会不会抖？" | 动态 spike + soak |
| "我的真实流量长啥样、瓶颈在哪？" | 动态 trace replay |

---

## 5. 用户故事

### 5.1 个人开发者小王

```bash
$ pping-lang bench static \
    --endpoint http://localhost:8000/v1 \
    --model Qwen2.5-32B \
    --prompt-tokens 500 \
    --output-tokens 100 \
    --concurrency 16 \
    --duration 60s

[bench] starting static run "static-2026-05-22-001"...
[bench] warmup 10s
[bench] running 60s @ concurrency=16...
[==============================================] 60/60s

Results
─────────────────────────────────────────────────
TTFT  p50 / p99:    142ms / 387ms
TPOT  p50 / p99:    28ms / 41ms
Throughput:         412 output tok/s
Total requests:     247 (0 errors)
─────────────────────────────────────────────────
Diagnoses fired during bench
  warning  CUDA graph padding 过高 (fired 4 times)
  warning  MFU 偏低                  (fired 6 times)

Auto-suggestion
  调小 max_num_seqs (当前 256 → 试试 128)，padding 应该能从 47% 降到 < 20%
  详细报告:  http://localhost:8765/api/bench/runs/static-2026-05-22-001
```

### 5.2 平台工程师老李

老李要给业务团队定容量。他写一个 YAML 场景：

```yaml
# capacity-planning.yaml
name: capacity-planning-h100x8
type: dynamic
endpoint: https://vllm.team.internal/v1
model: Qwen2.5-32B-Instruct
scenarios:
  - id: ramp
    profile: ramp
    from_concurrency: 1
    to_concurrency: 128
    duration: 600s
    prompt: { dist: lognormal, mean_tokens: 400, sigma: 0.8 }
    output: { dist: lognormal, mean_tokens: 150, sigma: 1.2 }
slo:
  ttft_p99_ms: 1000
  tpot_p99_ms: 50
report:
  format: html
  output: ./capacity-h100.html
```

```bash
$ pping-lang bench run capacity-planning.yaml
```

报告里直接给：
- 在 SLO 内最大 throughput = 287 tok/s @ concurrency=42
- 超过 concurrency=58 后 KV 触发抢占，吞吐倒挂

### 5.3 MLOps 张工 — CI 集成

PR pipeline 跑：

```yaml
# .github/workflows/perf-regression.yml
- run: |
    pping-lang bench static \
      --scenario .perf/standard.yaml \
      --baseline main \
      --fail-on regression
```

退化超过阈值（如 TTFT p99 +10%）就阻断合并。

---

## 6. 整体架构

```
pping_lang/bench/
├── __init__.py
├── microbench.py             # 已存在：pping-lang 自身热路径基准（保留）
├── cli.py                    # 新：pping-lang bench CLI
├── runner.py                 # 新：核心运行器（asyncio）
├── client.py                 # 新：vLLM HTTP client（streaming 支持）
├── scenarios/
│   ├── __init__.py
│   ├── schema.py             # YAML/dataclass schema
│   ├── static.py             # 静态场景执行
│   ├── dynamic.py            # 动态场景执行（ramp/spike/soak/trace）
│   └── load_pattern.py       # 并发/到达分布生成器
├── prompts/
│   ├── __init__.py
│   ├── synthetic.py          # 合成 prompt（按 token 数）
│   ├── distribution.py       # lognormal/uniform 等分布
│   └── corpus.py             # 语料/trace replay
├── measurement.py            # 客户端测量（TTFT/TPOT/e2e）
├── store.py                  # bench_runs 表读写
└── report.py                 # bench 专用报告 section（接进现有 report generator）
```

### 6.1 与现有模块的关系

```
┌─────────────────────────────────────────────────┐
│  pping-lang bench                                │
│   │                                              │
│   ▼                                              │
│  HTTP → vLLM ──→ pping-lang StatLogger          │
│                       │                          │
│                       ▼                          │
│                  ┌─────────────────────────┐    │
│                  │ LocalSink + Rule Engine │    │
│                  └────────────┬────────────┘    │
│                               │                  │
│  bench metadata ─────────────▶│                  │
│  (run_id, scenario, params)   │                  │
│                               ▼                  │
│                  ┌─────────────────────────┐    │
│                  │ DuckDB                  │    │
│                  │  • metrics (existing)   │    │
│                  │  • diagnoses (existing) │    │
│                  │  • bench_runs (new)     │    │
│                  └────────────┬────────────┘    │
│                               ▼                  │
│                       FastAPI + Dashboard       │
│                       (压测 tab)                │
└─────────────────────────────────────────────────┘
```

**关键设计**：bench 进程**独立于** vLLM 进程，但**共享** DuckDB。bench 启动时通过 `/api/bench/start` 通知 pping-lang plugin 在 metrics 上打 `bench_run_id` tag。

---

## 7. 静态测试详细设计

### 7.1 子模式

| 子模式 | 用途 | 参数变量 |
|---|---|---|
| **fixed** | 单点基准 | 无（全固定） |
| **sweep** | 单维扫描 | 一个参数遍历（concurrency、output_tokens 等） |
| **matrix** | 多维笛卡尔积 | N 个参数交叉 |
| **compare** | 双 run 对比 | 两个 fixed/sweep 配置 |

### 7.2 静态场景 schema

```python
@dataclass
class StaticScenario:
    name: str
    endpoint: str
    model: str
    prompt_tokens: int = 500
    output_tokens: int = 100
    concurrency: int = 16
    duration_s: int | None = 60       # 或 num_requests
    num_requests: int | None = None
    warmup_s: int = 10
    timeout_s: int = 30
    # 协议
    api: Literal["completions", "chat"] = "chat"
    # sweep / matrix 时用
    sweep: dict[str, list] | None = None
    # SLO 校验（可选）
    slo: SLO | None = None
```

### 7.3 静态运行流程

```
1. parse + validate scenario
2. dry-run 1 request 验证 endpoint 可达 / model 正确
3. 通知 pping-lang plugin: bench run 开始（POST /api/bench/start）
4. warmup_s（不计入指标）
5. main 阶段：保持 concurrency 个并发请求，跑 duration 或 num_requests
6. 每个请求：测量 (req_start, ttft, last_token, completion_tokens, error)
7. 通知 pping-lang plugin: bench run 结束（POST /api/bench/stop）
8. 拉取期间 vLLM 内部指标 + 诊断
9. 计算客户端聚合 + join 服务端窗口聚合
10. 调用 report.generate_bench_report(run_id)
```

### 7.4 输出格式

```json
{
  "run_id": "static-2026-05-22-001",
  "scenario": { ... },
  "started_at_ns": ...,
  "duration_s": 60.4,
  "client_metrics": {
    "total_requests": 247,
    "errors": 0,
    "ttft_ms": {"p50": 142, "p95": 320, "p99": 387},
    "tpot_ms": {"p50": 28, "p99": 41},
    "e2e_ms": {"p50": 1850, "p99": 4220},
    "output_throughput_tps": 412.4,
    "input_throughput_tps": 2061
  },
  "server_metrics_summary": {
    "kv_cache_peak_pct": 67,
    "padding_ratio_avg": 0.47,
    "mfu_avg": 0.12,
    "preemption_total": 0
  },
  "diagnoses": [
    {"rule_id": "high-cudagraph-padding", "fire_count": 4, "severity": "warning"},
    {"rule_id": "low-mfu", "fire_count": 6, "severity": "warning"}
  ],
  "slo_status": "pass" | "fail" | "n/a",
  "suggestions": [
    {
      "title": "调小 max_num_seqs",
      "evidence": "padding_ratio 平均 47%，约一半算力浪费",
      "action": "max_num_seqs: 256 → 128",
      "expected_impact": "+18% throughput, -45% padding"
    }
  ]
}
```

---

## 8. 动态测试详细设计

### 8.1 子模式

| 子模式 | 形态 | 用途 |
|---|---|---|
| **ramp** | concurrency 从 A 线性升到 B，over T | 容量探索、找拐点 |
| **spike** | base K 并发，T 秒突发 K' 并发，恢复 | 抗突发、恢复时间 |
| **soak** | 固定并发 K，跑 H 小时 | 稳定性、内存泄漏 |
| **wave** | concurrency 正弦波动 | 模拟昼夜流量 |
| **trace** | 按 timestamp+prompt log replay | 真实流量复盘 |
| **chaos** | 随机插入长 prompt / 突发短 prompt | 抢占场景压力 |

### 8.2 动态场景 schema

```python
@dataclass
class RampProfile:
    type: Literal["ramp"] = "ramp"
    from_concurrency: int
    to_concurrency: int
    duration_s: int
    step_duration_s: int = 30  # 每个 concurrency 等级停留多久（采样窗口）

@dataclass
class SpikeProfile:
    type: Literal["spike"] = "spike"
    base_concurrency: int
    spike_concurrency: int
    spike_duration_s: int
    repeat: int = 1
    interval_s: int = 60

@dataclass
class TraceProfile:
    type: Literal["trace"] = "trace"
    trace_file: Path             # JSONL: {ts_ms, prompt_tokens, output_tokens}
    speed: float = 1.0           # 1.0 = 实时，2.0 = 2× 加速

@dataclass
class PromptDistribution:
    dist: Literal["lognormal", "uniform", "fixed", "corpus"]
    mean_tokens: int | None = None
    sigma: float | None = None
    min_tokens: int | None = None
    max_tokens: int | None = None
    corpus_path: Path | None = None    # JSONL of prompts
```

### 8.3 动态运行流程

ramp 例：

```
t=0    : concurrency=1,  跑 step_duration_s=30s → 收集本档指标
t=30   : concurrency=2
...
t=N    : concurrency=to_concurrency
        每个档位独立汇总（与静态 sweep 类似），但 vLLM 状态延续
        最终输出：按档位的指标矩阵 + 拐点分析
```

spike 例：

```
t=0    : base 流量
t=60   : 注入 spike，记录 onset → 系统识别 → 抢占触发 → 恢复时间
t=90   : 恢复到 base
... 重复
```

### 8.4 拐点检测

动态 ramp 完成后自动分析：

```python
def detect_knee(rows: list[tuple[int, float]]) -> int | None:
    """rows = [(concurrency, throughput), ...]
    返回 throughput 增速突变（饱和）的 concurrency"""
```

- 用最大曲率点（"kneedle" 算法）
- 输出："concurrency = 42 时吞吐饱和（412 tok/s），继续加并发只增延迟不增吞吐"

### 8.5 SLO 边界识别

```python
def find_slo_capacity(rows, slo):
    """返回满足 SLO 的最大 concurrency"""
```

- 输出："concurrency ≤ 38 时 TTFT p99 在 1000ms 内；超过即破"

---

## 9. CLI 接口 — **dropped（UI-only）**

**v0.1 撤回了 CLI**。原方案里 `pping-lang bench static ...` / `bench ls` / `bench show <id>` 的所有功能都通过 dashboard 「压测」tab + 下面 §10 的 HTTP API 提供。

理由：
- 用户实际用 dashboard，CLI 的覆盖率太低不值两份代码维护
- "轻量" — 少一个 entry point、少一份 argparse / 输出格式化代码、少一组测试
- 自动化场景仍然可用：脚本调 `POST /api/bench/start` + 轮询 `GET /api/bench/runs/{id}` 就能集成进 CI（curl + jq 三行）

如果未来真有 headless 需求（无浏览器的远程机器、CI 节点），再补一个**薄壳** CLI——纯 HTTP client，不复刻 scenario 引擎。

CI 集成的等效命令（替代原 `--fail-on regression`）：
```bash
RUN_ID=$(curl -sX POST host/api/bench/start -d '...' | jq -r .run_id)
# poll until done
until [ "$(curl -s host/api/bench/runs/$RUN_ID | jq -r .status)" != "running" ]; do sleep 5; done
SLO=$(curl -s host/api/bench/runs/$RUN_ID | jq -r .slo_status)
[ "$SLO" = "pass" ] || exit 1
```

---

## 10. HTTP API 端点

| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/bench/start` | POST | 启动一个 run（body: scenario JSON） |
| `/api/bench/runs` | GET | 列出所有 run（id, scenario name, started_at, status） |
| `/api/bench/runs/{id}` | GET | run 详情（含 client/server metrics, diagnoses, suggestions） |
| `/api/bench/runs/{id}/stop` | POST | 中止 |
| `/api/bench/runs/{id}/report` | GET | 该 run 的 HTML 报告 |
| `/api/bench/runs/{id}/compare/{other_id}` | GET | 双 run 对比 |
| `/api/bench/status` | GET | 当前是否有 run 在跑、进度% |

### 10.1 启动 run 示例

```http
POST /api/bench/start
Content-Type: application/json

{
  "type": "static",
  "scenario": {
    "name": "ad-hoc-001",
    "endpoint": "http://localhost:8000/v1",
    "model": "Qwen2.5-32B",
    "prompt_tokens": 500,
    "output_tokens": 100,
    "concurrency": 16,
    "duration_s": 60
  }
}

→ 202 Accepted
{
  "run_id": "static-2026-05-22-001",
  "status": "running",
  "estimated_finish_at_ns": ...
}
```

---

## 11. 仪表盘集成

新增第四个 tab：**压测**。

```
┌─ 实时  规则(2)  报告  压测 ──────────────────────────┐
│                                                       │
│  当前运行: static-2026-05-22-001 (00:42 / 01:00)      │
│  [==========================================------]   │
│                                                       │
│  ┌─ Live metrics ──────────────────────────────────┐ │
│  │  TTFT p99  142ms   throughput  411 tok/s        │ │
│  │  KV cache  67%     padding     47%              │ │
│  │  (这里复用实时 tab 的小卡片)                     │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─ 历史 runs ────────────────────────────────────┐  │
│  │  static-2026-05-22-001  static  pass  详情→    │  │
│  │  ramp-h100-001          dynamic warn  详情→    │  │
│  └────────────────────────────────────────────────┘  │
│                                                       │
│  [+ 新建 run]                                        │
└─────────────────────────────────────────────────────┘
```

**新建 run 表单**：
- 选「静态 / 动态」
- 静态：endpoint / model / 形状参数 / concurrency / duration
- 动态：选 ramp/spike/soak/wave，对应参数
- "高级：YAML"：粘 YAML 直接跑

---

## 12. 数据模型

新增 `bench_runs` 表：

```sql
CREATE TABLE bench_runs (
    run_id        VARCHAR PRIMARY KEY,
    scenario_name VARCHAR,
    scenario_type VARCHAR,         -- 'static' | 'dynamic'
    started_at_ns BIGINT,
    finished_at_ns BIGINT,
    status        VARCHAR,         -- 'running' | 'done' | 'failed' | 'aborted'
    scenario_json JSON,
    client_metrics_json JSON,
    server_summary_json JSON,
    slo_status    VARCHAR,         -- 'pass' | 'fail' | 'n/a'
    suggestions_json JSON,
    error         VARCHAR
);
CREATE INDEX idx_bench_runs_started ON bench_runs(started_at_ns DESC);
```

`metrics` 和 `diagnoses` 表**不改 schema**——bench run 期间的所有数据按 ts_ns 落表，查询时按 `bench_runs.started_at_ns/finished_at_ns` 窗口反查即可。

### 12.1 关联 bench_run_id 到 metrics 的两种方案

| 方案 | 实现 | 取舍 |
|---|---|---|
| A. 按时间窗口隐式关联 | 查询时 join `WHERE ts_ns BETWEEN run.started AND run.finished` | 零 schema 改动，但多 run 重叠时混淆 |
| B. 给 metrics 加 bench_run_id 列 | 改 schema + plugin 在 sink 出口打 tag | 干净，但 schema migration 成本 |

**v0.1 用 A**（embedded 模式只有一个 vLLM 实例、bench 串行跑），v0.2 同时支持多 bench 时升级到 B。

---

## 13. 与规则引擎的集成（核心差异点）

普通压测：跑完吐 TTFT 数字 → 结束。
pping-lang bench：跑完后**自动**：

1. 拉本次 run 时间窗口内的所有 diagnoses
2. 按 fire_count + severity 排序
3. 调 `report.analysis._audit_heuristics(...)` 拿"建议"
4. 把 client-side metrics + server-side diagnoses + suggestions **拼成一份针对本次 run 的报告**

报告结构（沿用现有 jinja2 模板，加 bench section）：

```
┌──────────────────────────────────────────┐
│ Bench Run: static-2026-05-22-001         │
│ ─────────────────────────────────────    │
│ § Scenario          (参数 dump)          │
│ § Client metrics    (TTFT/TPOT/...)      │
│ § Server snapshot   (KV/padding/MFU)     │
│ § Diagnoses         (规则触发统计)        │
│ § Suggestions       (启发式建议)          │
│ § Roofline          (本次 run 的散点)     │
│ § Per-request CDF   (TTFT/TPOT 累积分布) │
└──────────────────────────────────────────┘
```

### 13.1 拐点 + SLO + 诊断的联合判断

举例：动态 ramp 跑完，分析逻辑：

```python
def synthesize(run):
    # 1. 找吞吐拐点
    knee = detect_knee(run.throughput_by_concurrency)

    # 2. 找 SLO 上限
    slo_cap = find_slo_capacity(run, run.slo)

    # 3. 在拐点 / SLO 边界附近，查那段时间触发了什么诊断
    fired_near_knee = diagnoses_in_window(knee.ts ± 10s)

    # 4. 拼故事
    if "kv-cache-pressure" in fired_near_knee:
        return f"在 concurrency={knee.c} 处 KV 触发抢占，吞吐拐点正是 KV 上限"
    if "high-cudagraph-padding" dominates:
        return f"throughput 早于 KV/带宽饱和拐点（padding ratio {pad:.0%}）"
    ...
```

**这是 pping-lang bench 的真正卖点**——把"数字 / 诊断 / 解释"在一份报告里串起来，业界没有第二家。

---

## 14. 报告

### 14.1 单 run 报告

复用 `pping_lang/report/generator.py`，新增 `bench_section.html.j2`。每个 run 一个独立 HTML，可邮件分享。

### 14.2 双 run 对比报告

```
┌──────────────────────────────────────────────────────┐
│ Compare: run-A vs run-B                              │
│ ─────────────────────────────────────────────────    │
│                          run-A         run-B    diff │
│ TTFT p99                  387ms         142ms   -63% │
│ Throughput                412 tok/s     587 tok/s +42%│
│ Padding ratio avg         47%           18%      ✓   │
│ MFU avg                   12%           34%      ✓   │
│ ─────────────────────────────────────────────────    │
│ 配置差异                                              │
│   max_num_seqs            256           128          │
│   enable_chunked_prefill  false         true         │
└──────────────────────────────────────────────────────┘
```

### 14.3 CI 集成产物

`--fail-on regression --threshold 0.1`：
- 退出码 0 = 通过
- 退出码 1 = 退化超阈值
- stderr 输出具体哪些指标退化

---

## 15. 客户端与协议

### 15.1 协议支持

| 协议 | v0.1 |
|---|---|
| OpenAI `/v1/chat/completions`（streaming） | ✓ |
| OpenAI `/v1/completions`（streaming） | ✓ |
| TGI `/generate_stream` | v0.2 |
| Triton GRPC | v0.3+ |

### 15.2 客户端测量精度

```python
# 每个 streaming 请求：
t0 = monotonic_ns()                  # 发出
t_ttft = None
t_last = None
n_tokens = 0
async for chunk in response.aiter_lines():
    if chunk has new content:
        if t_ttft is None:
            t_ttft = monotonic_ns()
        n_tokens += count
        t_last = monotonic_ns()
# 客户端测：
#   ttft = t_ttft - t0
#   e2e  = t_last - t0
#   tpot = (t_last - t_ttft) / (n_tokens - 1)
```

关键：`monotonic_ns()` 单调时钟、不被 NTP 调整。

### 15.3 控制并发

`asyncio.Semaphore(concurrency)`，保证常驻 K 个 in-flight，不靠 sleep。

---

## 16. 性能与边界

- **客户端开销**：< 5% CPU @ concurrency=128（asyncio + httpx 实测）
- **单进程并发上限**：~512（再多受 file descriptor + asyncio event loop 影响，需分布式）
- **结果存储**：单 run 客户端原始数据 ~MB 量级，DuckDB 容量不是瓶颈
- **trace replay 文件大小**：100k 请求约 10MB JSONL，能直接加载到内存

---

## 17. 实施计划

按 pping-lang 的 "Day N" 节奏，估约 2.5 周（13 天有效）。

### Week 1：静态 + 客户端基础

| Day | 任务 |
|---|---|
| 1 | scaffold + scenarios/schema.py + client.py（OpenAI streaming） |
| 2 | runner.py 静态 fixed 模式 + measurement.py |
| 3 | CLI `bench static` + 终端表格输出 |
| 4 | sweep + matrix 子模式 |
| 5 | bench_runs 表 + store.py + 跨 run 持久化 |

### Week 2：动态 + 集成

| Day | 任务 |
|---|---|
| 6 | dynamic ramp + load_pattern.py |
| 7 | dynamic spike / soak / wave |
| 8 | prompt distribution + corpus 加载 |
| 9 | `/api/bench/*` 端点 |
| 10 | 仪表盘"压测" tab + 新建表单 + 历史列表 |

### Week 3：报告 + 收口

| Day | 任务 |
|---|---|
| 11 | 单 run 报告（接 jinja2） |
| 12 | compare 报告 + 拐点检测 + SLO + suggestions |
| 13 | trace replay + chaos + CI fail-on-regression + 文档 |

---

## 18. 风险与对冲

| 风险 | 影响 | 对冲 |
|---|---|---|
| 客户端成为瓶颈（不是 vLLM） | bench 结果失真 | 单进程上限 512 并发；超过推外部 locust |
| 模型 tokenizer 差异导致 prompt_tokens 不准 | 静态对比失效 | 用 vLLM `/tokenize` 端点（如可用）或 tiktoken 近似 |
| 长 soak 期间 vLLM 进程 OOM | bench 跑半截死 | 客户端检测连接失败 → 自动 abort + 标记 |
| 高并发下客户端 GIL | Python 单进程 CPU 顶满 | 用 httpx + uvloop；必要时支持子进程并行 |
| trace replay 时序漂移 | 长 trace 累积误差 | 每 60s 重对时；漂移超阈值 warn |
| YAML 场景错配（如 endpoint 写错） | 跑空数据 | dry-run 必做：1 个真实请求验证 200 + token 流 |
| bench 期间 dashboard 刷新慢 | 用户体验差 | bench 进程独立、不抢 pping-lang 进程；DuckDB 多连接共存 |

---

## 19. 开放问题

1. **是否支持自定义 prompt 模板**（带 system message / multi-turn）？v0.2 加。
2. **多模型对比**（同一场景跑多个 endpoint） — v0.2 加。
3. **prompt token 计数策略**：tiktoken 近似 vs vLLM `/tokenize` vs server 回报。v0.1 用 vLLM tokenize 优先、tiktoken fallback。
4. **多 bench run 并发**（一个 vLLM 实例同时跑两组场景）— v0.2 引入 bench_run_id tag 列。
5. **GuideLLM / llmperf 兼容层**：是否 import 它们的 scenario 格式？v0.3 评估。
6. **自动调参**（pping-lang 自动跑 sweep → 选最优配置 → 回写 vllm 启动参数建议）— v0.4。
7. **多机分布式压测**：超出 v0.1 单进程上限后怎么扩。预留 RemoteClient interface，v0.3 接 k8s Job-based fan-out。

---

## 20. 附录：完整场景 YAML 示例

```yaml
# 一个综合 dynamic 场景示例
name: capacity-and-stability-h100x8
type: dynamic
endpoint: https://vllm.team.internal/v1
model: Qwen2.5-32B-Instruct
api: chat

# 全局 SLO（任一指标破即标 fail）
slo:
  ttft_p99_ms: 1000
  tpot_p99_ms: 50
  error_rate: 0.001

# 多个 sub-scenario 顺序执行
phases:
  - id: warmup
    profile:
      type: ramp
      from_concurrency: 1
      to_concurrency: 16
      duration_s: 60
    prompt: { dist: fixed, mean_tokens: 400 }
    output: { dist: fixed, mean_tokens: 100 }

  - id: capacity-ramp
    profile:
      type: ramp
      from_concurrency: 16
      to_concurrency: 128
      duration_s: 600
      step_duration_s: 30
    prompt: { dist: lognormal, mean_tokens: 400, sigma: 0.8 }
    output: { dist: lognormal, mean_tokens: 150, sigma: 1.2 }

  - id: stability-soak
    profile:
      type: soak
      concurrency: 32          # 取 capacity-ramp 找到的 SLO cap 80%
      duration_s: 3600
    prompt: { dist: corpus, corpus_path: ./prompts-sample.jsonl }

  - id: spike-test
    profile:
      type: spike
      base_concurrency: 32
      spike_concurrency: 96
      spike_duration_s: 30
      repeat: 3
      interval_s: 120
    prompt: { dist: fixed, mean_tokens: 400 }
    output: { dist: fixed, mean_tokens: 100 }

report:
  format: html
  output: ./capacity-h100-{date}.html
  compare_with: ./baseline-h100.json    # 可选：和上次跑做 diff

ci:
  fail_on: regression
  baseline: ./baseline-h100.json
  threshold: 0.10
```

---

## 21. 一句话总结

> 把"流量生成 + 指标采集 + 自动诊断 + 配置建议"做成一个 CLI / dashboard 一体的闭环——别人给你数字，pping-lang bench 给你**结论**。

设计文档结束。Review、讨论、实施。
