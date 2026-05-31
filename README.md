<div align="center">

# pping-lang

### vLLM 跑得慢？它直接告诉你为什么、以及怎么改。

给 vLLM 装上自动性能诊断 —— 一行 `pip install`、不改任何启动参数，<br/>
浏览器打开一个网址，就能看到推理服务卡在哪、为什么卡、改哪个参数能提多少。

<br/>

[![PyPI](https://img.shields.io/pypi/v/pping-lang?color=4c8bf5&label=PyPI)](https://pypi.org/project/pping-lang/)
[![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-4c8bf5)](https://pypi.org/project/pping-lang/)
[![License](https://img.shields.io/badge/license-Apache%202.0-43a047)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--alpha-fb8c00)](#项目状态)
[![Tests](https://img.shields.io/badge/tests-209%20passing-43a047)](tests/)

**[快速上手](#快速上手)** · **[仪表盘](#仪表盘)** · **[配置](#配置)** · **[工作原理](#工作原理)** · **[路线图](#路线图)**

</div>

---

```text
[pping-lang] [!] WARNING: CUDA graph padding 过高
  CUDA graph padding 比例 70%，约 70% 的 GPU 算力浪费在补 0
  → 调小 max_num_seqs 或开启更细粒度的 cudagraph capture（PIECEWISE 模式）

[pping-lang] [!] WARNING: MFU 偏低
  MFU = 5% < 20%（理论峰值的小部分都没跑到）
  → 检查 padding ratio / batch 大小 / dtype（应为 bf16/fp16）
```

<div align="center"><sub>不是"GPU 利用率 94%"这种看了等于没看的数字 —— 而是照着做就行的结论。</sub></div>

<br/>

## 它解决什么问题

vLLM 把指标都给你了 —— GPU 利用率、TTFT、KV cache 用量、队列深度…… 但**没人帮你解读**。

> "GPU 利用率 94%" 看了等于没看。业界已知的坑是 **GPU util 是 duty cycle，不是吞吐** ——
> 97% 利用率照样能掉 3 倍吞吐。Grafana 仪表盘只是把数字画出来，要人盯着、还得自己会看。

pping-lang 把 vLLM 自带指标，加上工具自己采的 GPU 物理层数据，喂给一个轻量规则引擎，
输出的不是数字，而是**结论**："GPU 有一半在空转、改一个参数能提 58% 吞吐" —— 照着做就行。

<br/>

## 特性

<table>
<tr>
<td width="50%" valign="top">

#### 零配置接入
走 vLLM 官方 `stat_logger_plugins` 机制，装上即生效，不改一行 vLLM 启动参数。

</td>
<td width="50%" valign="top">

#### 看穿 GPU duty cycle
用 vLLM 的 `cudagraph_stats` / `perf_stats` 算出真实的 padding ratio、MFU、显存带宽利用率。

</td>
</tr>
<tr>
<td width="50%" valign="top">

#### 自动诊断
内置 10 条经过验证的规则，覆盖吞吐 / 延迟 / 稳定性 / 效率 / 瓶颈五类问题。

</td>
<td width="50%" valign="top">

#### 规则可视化编辑
阈值不合适？网页上直接改、点"测试当前数据"立即预览，热生效、不重启 vLLM。

</td>
</tr>
<tr>
<td width="50%" valign="top">

#### 可分享报告
一键导出单个 HTML 文件，含趋势、配置审计、Roofline 图，邮件发给同事或老板。

</td>
<td width="50%" valign="top">

#### 故障隔离 & 低开销
任何 bug 不会拖垮 vLLM 推理；性能开销 < 2% 吞吐。

</td>
</tr>
<tr>
<td width="50%" valign="top">

#### OTel 兼容
设了 `OTEL_EXPORTER_OTLP_ENDPOINT` 就把数据同时发去已有后端（Langfuse / Jaeger / Datadog）。

</td>
<td width="50%" valign="top">

#### 零构建前端
单文件 HTML 仪表盘，无需 Node、无需打包，开箱即用。

</td>
</tr>
</table>

<br/>

## 快速上手

#### 1 · 先试试（不用 GPU、不用 vLLM）

```bash
pip install pping-lang
python examples/embedded/demo.py
```

脚本喂入合成的 vLLM 指标，约 7 秒后终端打印两条诊断、浏览器同时能访问仪表盘 —— 确认装好了。

#### 2 · 接到真实的 vLLM

```bash
vllm serve <你的模型>
```

vLLM 启动日志会多出一行仪表盘网址（默认 `http://localhost:8765`），打开即用。

> 完事 —— 不用改 vLLM 任何参数。

<br/>

## 仪表盘

打开网址后是一个单页仪表盘，三个标签页：

| 标签页 | 内容 |
|:--|:--|
| **实时** | GPU / 显存 / 延迟 / 吞吐，每 2 秒刷新；诊断卡片说明哪里慢、为什么、怎么改 |
| **规则** | 卡片式 CRUD：改阈值、加规则、点"测试当前数据"立即预览，保存即热生效 |
| **报告** | 选时间范围，一键导出自包含的单 HTML 文件 |

<br/>

## 配置

大多数情况**零配置即用**。要调就用环境变量：

| 变量 | 默认值 | 作用 |
|:--|:--|:--|
| `PPING_LANG_API_PORT` | `8765` | 仪表盘端口 |
| `PPING_LANG_API_HOST` | `127.0.0.1` | 容器里要设成 `0.0.0.0` 才能从外面访问 |
| `PPING_LANG_DB_PATH` | `~/.pping-lang/local.duckdb` | 时序数据库路径 |
| `PPING_LANG_DISABLE_NVML` | — | 设为 `1` 关闭 GPU 物理层采样（无 GPU 环境） |
| `PPING_LANG_DISABLE_RULES` | — | 设为 `1` 关闭规则引擎 |
| `PPING_LANG_DISABLE_API` | — | 设为 `1` 关闭 HTTP API / 仪表盘 |
| `PPING_LANG_RULES_PATH` | — | 自定义规则 JSON 文件，覆盖默认规则 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | 设了就把数据同时发去已有的 OTel 后端 |

<sub>完整开关列表见<a href="docs/pping-lang-design-v0.2.md">设计文档</a>。</sub>

<br/>

## 工作原理

```text
vLLM 进程
  └── pping-lang 插件
       ├── 采集     vLLM 指标 + NVML GPU 物理层数据
       ├── 规则引擎  周期评估 10 条诊断规则
       ├── 存储     本地 DuckDB（默认 30 天滚动）
       └── 仪表盘    FastAPI + 单文件 HTML
```

热路径只做 O(1) 入队，采样、规则评估、数据库写入全部异步 —— 这是 < 2% 开销的来源。

为什么不重复造 observability 后端、规则引擎为什么自己写、四种部署模式怎么演进 ——
都在<strong><a href="docs/pping-lang-design-v0.2.md">设计文档</a></strong>里。想看一个完整的真实排查过程，读
<strong><a href="docs/case-study-1-padding.md">GPU 在补 0</a></strong>。

<br/>

## 前置条件 / 兼容性

### 操作系统

**Linux 原生** —— 直接 `pip install`。
**Windows** —— vLLM 不原生支持，必须走 **WSL2 + Ubuntu**。从 Windows 浏览器访问 dashboard 时设 `PPING_LANG_API_HOST=0.0.0.0`，端口跨 WSL 自动转发。

### vLLM 版本

实测过的版本和指标可用性差异：

| vLLM | 状态 | `SchedulerStats` | `IterationStats` | `cudagraph_stats` | `perf_stats` |
|:--|:--|:--|:--|:--|:--|
| **0.20.x** | 推荐 | ✓ | ✓ | ✓ | ✓ |
| **0.13.0** | 可用、降级 | ✓ | ✓ | ✓ 但字段不同 | ✗ |
| < 0.13 | 不支持 | — | — | — | — |

**`perf_stats` 字段在 0.20+ 才有** —— 这是 MFU / 显存带宽利用率 / Roofline 派生指标的数据源。低版本上这些图表会显示空数据但**不会崩**（collector 用 `getattr(..., None)` 防御式读取）。

低版本上仍可用的核心能力：TTFT / TPOT / E2E p99、KV cache 用量、prefix cache 命中率、抢占率、GPU util / mem / power（NVML 全套）、所有规则诊断（除 padding/MFU/带宽 那 3 条会因数据源缺失而不触发）。

### 已识别 GPU 列表

NVML 设备名 → BF16 peak (TFLOPs/s, dense) / 显存带宽 (GB/s)。识别失败不影响监控，仅跳过 MFU / Roofline 派生。

```
Blackwell:   B200 (2250 / 8000)  ·  B100 (1800 / 8000)
Hopper:      H200 (989 / 4800)  ·  H100 SXM/PCIe/NVL  ·  A100 SXM/PCIe
Ada Lovelace 数据中心:  L40S (362 / 864)  ·  L40 (181 / 864)  ·  L4 (121 / 300)
Ada Lovelace 桌面:      RTX 4090 / 4080 / 4070 Ti / 4070 / 4060 Ti / 4060
Ada Lovelace 移动:      RTX 4090/4080/4070/4060 Laptop GPU
旧代:        A30  ·  A10G  ·  A10  ·  V100  ·  T4  ·  RTX 3090
```

未在表里的 GPU 会有 warning log（`unknown GPU "<name>" — MFU / Roofline disabled`），其它一切照旧工作。补表只需在 `pping_lang/hardware.py` 的 `_GPU_PEAK_TABLE` 加一条。

### 性能开销

设计目标 < 2% 吞吐。实测：
- 热路径 `record()` ≈ 100 μs / 调用
- 单进程内 NVML 采样 + 规则引擎 + Sink flush 总 CPU < 1%
- **高负载 + 默认 NVML 100ms 采样下，Sink 队列默认 16k 会被打爆**，建议在生产开 `PPING_LANG_NVML_INTERVAL_S=1.0` + `PPING_LANG_SINK_QUEUE_SIZE=65536`。设计文档 §14.1 / §21 实测数据列详细。

<br/>

## 路线图

| 版本 | 模式 | 重点 |
|:--|:--|:--|
| **v0.1** ·&nbsp;当前 | Embedded | 单机本地，pip 装上即用 |
| v0.2 | + Sidecar / Centralized | 独立 server 进程、Docker 镜像、Helm chart、K8s 多副本聚合 |
| v0.3 | + Stateless | OTel-native，从已有 Prometheus / Tempo 反查诊断 |

<br/>

## 项目状态

Pre-alpha (`v0.1.0a1`)。当前为 **Embedded 模式** —— 面向单机本地开发、单卡 / 单 Pod 场景。
Sidecar、K8s 多副本聚合等生产部署模式规划在 v0.2，见上方[路线图](#路线图)。

<br/>

## 开发

```bash
pip install -e ".[dev]"
pytest          # 209 个测试
ruff check .
```

贡献流程见 **[CONTRIBUTING.md](CONTRIBUTING.md)**，版本变更见 **[CHANGELOG.md](CHANGELOG.md)**。

<br/>

<div align="center">

## License

**[Apache 2.0](LICENSE)** © Leon

<sub>用 vLLM 不该靠猜。</sub>

</div>
</content>
