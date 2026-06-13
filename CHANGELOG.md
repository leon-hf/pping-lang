# Changelog

按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，遵循 SemVer。

## [Unreleased] — v0.1 alpha (2026-06)

第 1-3 周采集/诊断/Sink 完成后，继续做完 CUPTI kernel 级剖析（阶段 1a/2/3）、压测闭环、
dashboard 诊断面板与 pip 一键打包。准备发首个 alpha tag。

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

**CUPTI kernel 级剖析**（阶段 1a）
- 注入式 `.so`（`CUDA_INJECTION64_PATH`）抢占 CUPTI subscriber，避开 PyTorch Kineto 冲突
- Activity API per-kernel GPU 时间分解 + 算子分类（gemm / attention / comm / norm / activation / …）
- Kernel tab：占比、主导 stall、CUDA Graph 覆盖率、launch 频率、自身开销（cb_ms / dropped）
- 服务端 findings：compute-bound / launch-bound / memcpy 开销 / CUDA Graph 覆盖低 等结论

**PC Sampling**（阶段 2）
- SASS 指令 + stall reason 采样，库内预聚合（不全量回流，守 5% 开销）
- 多进程 `vllm serve` 工作：EngineCore 进程驱动 PC sampling + 跨进程共享 JSON 回流前端
- Deep Evidence 面板：warp 三态、全局 stall 分解、PerfWorks 原始 reason 下钻、测量方法卡
- 按需触发 5–30s 采样的深度分析端点

**行级归因**（阶段 3，对标 zymtrace 阶段 2）
- PC → 源码行 / SASS 热点双轨；源码行显示该行 Python 原文
- kernel → Python launch 栈（向外归因 MVP），派发器噪音清洗后露出真算子名（addmm/mm/linear）
- decode 覆盖 cuBLAS GEMV / FlashInfer 采样；区分 `_ZN` mangled vs Triton

**Roofline 调优地图**（P0-C 实测闭环）
- 一键压测扫并发，实测 scaling 曲线叠加理论 envelope（外推 → 实测）
- scaling verdict 用扩展效率口径（相对基线加速比 / 理想加速比，eff<70% = 收益递减点）
- 散点聚合成簇心 + operating point / batch scaling envelope / ridge point 语义标签

**压测**（bench）
- OpenAI 协议静态压测（chat / completions）+ SLO 校验，结果落 DuckDB 可回看
- 并发 sweep（CLI）
- 任选两条 run 逐指标 A/B/Δ% 对比 + 图表化对比面板（每卡 A/B 双横条 + Δ% 优劣徽标）

**打包与硬件**
- `pping-vllm serve <model>` 一条命令，`.so` 现编（cu12/cu13 自适应，编不出自动降级）
- roofline 峰值改从 CUDA 设备属性现读现算（`read_gpu_peak`），回退型号表 / env
- GPU 名现读 `torch.cuda.get_device_name` + 可选部署标签（`PPING_DEPLOY_TAG`）

**Dashboard 演进**
- Kernel tab 扩成完整诊断面板；HTTP API 扩到 36 端点（含 kernels / deep_evidence / roofline / scaling_sweep / bench）
- KPI 卡 + TTFT/TPOT 分位分布条 + 用户延迟趋势（平均为主、p99 退为浅线参考）

### Changed
- 项目命名从 `llmprof` 改为 `pping-lang`（PyPI 名带连字符，Python 模块带下划线）
- roofline 从「分类器」升级为「调优地图」：operating point + batch scaling envelope，文案改工程口吻
- 用户延迟趋势 / TTFT·TPOT 改以平均为主显，p50/p95/p99 退为分布/参考

### Fixed
- cudagraph 采样：HW 缓冲溢出会永久楔死 CUPTI 会话 → 默认周期 2^12→2^16；capture 打断早 prime 采样 → 检测空窗自愈 reprime
- correlate 拼 `dirName`+`fileName` 成全路径（否则只得 basename，读不到源码原文）
- UI no-cache（`dashboard.js` / `css` / `index.html`，防 F5 复用旧版致新功能不渲染）
- Deep Evidence findings 卡片黏连（`x-show` 与内联 `display:flex` 同元素冲突，拆两层）

### Tests
- 初版 209 测试（采集 / 诊断 / Sink / API CRUD / e2e marquee / 性能 regression）
- 后续新增覆盖：bench（runner / schema / SLO / sweep / store / prompts）、CUPTI collector、scaling、API server 生命周期

---

模板（未来 release）：

## [0.x.0] — YYYY-MM-DD

### Added
### Changed
### Deprecated
### Removed
### Fixed
### Security
