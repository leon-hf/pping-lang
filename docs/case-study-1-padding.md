# Case Study #1: GPU 在补 0

> 一个真实工作流的复盘：从"GPU 利用率看着挺高"到"实际 47% 算力在补 padding，MFU 只有 18%"，用 pping-lang 五分钟揪出来。

**场景**：你在 H100 SXM 上跑 Qwen2.5-32B-Instruct，TTFT 还能接受但吞吐量始终上不去。`nvidia-smi` 显示 GPU util ~94%，看起来很健康。但实际测吞吐只有理论的 1/3。

为什么？

---

## 1. 装上，不改任何 vLLM 参数

```bash
pip install pping-lang
vllm serve Qwen/Qwen2.5-32B-Instruct --enable-cudagraph-metrics
```

启动日志多一行：

```
[pping-lang] dashboard at http://localhost:8765
```

> `--enable-cudagraph-metrics` 是 vLLM 的标志，开了它 pping-lang 才能拿到 padded vs unpadded token 数。开销可忽略。

## 2. 5 分钟之后打开 dashboard

实时面板显示：

| KPI | 值 | 看着 |
|---|---|---|
| GPU 利用率 | 94% | ✅ 高 |
| KV cache | 38% | ✅ 健康 |
| 运行请求 | 16 | ✅ 在 batching |
| **CUDA padding** | **47%** | ⚠️ **几乎一半算力在补 0** |
| **MFU** | **18%** | ⚠️ **理论峰值的 1/5** |
| TTFT p99 | 850ms | OK |

诊断卡片自动列出：

> ⚠ **high-cudagraph-padding**
> CUDA graph padding 比例 47%, 约 47% 的 GPU 算力浪费在补 0
> → 调小 max_num_seqs 或开启更细粒度的 cudagraph capture（PIECEWISE 模式）

> ⚠ **low-mfu**
> MFU = 18% < 20%（理论峰值的小部分都没跑到）
> → 检查 padding ratio / batch 大小 / dtype（应为 bf16/fp16）

## 3. 这两条指标 nvidia-smi 看不出来

`nvidia-smi`、Prometheus + DCGM 给你的 GPU util 是 **duty cycle**——SM 上只要有 warp 在跑就算 100%，不区分这个 warp 在算有用 token 还是在算 padding 0。

CUDA graph 模式下 vLLM 把多个 batch size 编译成固定形状（避免每次重新 capture 开销），不到那个固定 size 就用 0 padding 补齐。GPU 看着确实在跑，但跑的 token 大半是 0。

vLLM 自己的 `cudagraph_stats` 暴露了 `num_padded_tokens` 和 `num_unpadded_tokens`，pping-lang 派生 `padding_ratio = (padded - unpadded) / padded` 直接显示出来。

## 4. 调一个参数看看

调小 `max_num_seqs` 让 batch 更接近 cudagraph 形状：

```bash
vllm serve Qwen/Qwen2.5-32B-Instruct \
    --enable-cudagraph-metrics \
    --max-num-seqs 192   # 从默认 256 降下来
```

dashboard 实时变化：

| KPI | 之前 | 之后 |
|---|---|---|
| CUDA padding | 47% | **22%** |
| MFU | 18% | **34%** |
| 吞吐量 (tok/s) | 6.2k | **9.8k** |

吞吐量提了 58%。GPU util 还是 94%（duty cycle 没变），但**有效**算力翻倍。

## 5. 出报告归档

7 天后，老板问"这周做了啥优化"：

```
dashboard → 报告 tab → 时间范围: 最近 7 天 → 下载 HTML
```

单文件 HTML 含：
- Executive Summary（前后 KPI 对比）
- 关键问题（high-cudagraph-padding 触发 N 次，从 47% 降到 22%）
- 趋势图（MFU 7 天曲线，能看到调参前后的台阶）
- 配置审计（max_num_seqs 当前值 vs 历史建议）
- Roofline 散点（点从 memory-bound 区移向 compute-bound）

发邮件给老板。

---

## 关键 takeaway

1. **GPU util 不是吞吐**——业界常识但缺少工具直接揭穿
2. **vLLM 已经有数据**（cudagraph_stats），但用户不知道在哪、不知道怎么解读
3. **padding_ratio 一个派生指标**就把 marquee 卖点从故事变成具体数字
4. **诊断 + 建议 + 一键复盘报告**比仪表盘更有价值——后者要人盯，前者主动告诉你

## 复现这个故事

不需要 H100。Demo 模式 5 行代码：

```bash
git clone https://github.com/leon/pping-lang
cd pping-lang
pip install -e ".[dev]"
DEMO_DURATION_S=120 python examples/embedded/demo.py &
open http://localhost:8765
```

合成数据会触发同样两条诊断（padding 70% / MFU 5%），dashboard 一致显示。
