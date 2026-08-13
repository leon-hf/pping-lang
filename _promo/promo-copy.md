# pping-lang 推广文案草稿（任务 4）

## 1. Show HN 草稿

**Title:**
Show HN: pping-lang – a diagnosis plugin for vLLM that tells you *why* inference is slow (plus an auto-tuning agent that got 6.2x throughput)

**Body:**

Hi HN — I built pping-lang, an open-source plugin for vLLM that turns raw serving metrics into actionable diagnoses.

The problem: vLLM exposes rich runtime stats, and the standard setup is Prometheus + Grafana. That shows you numbers but never conclusions. The classic trap: GPU "utilization" sits at 70–90% during decode while MFU is under 5%, because decode is memory-bound. The dashboard looks healthy; throughput is terrible.

pping-lang hooks vLLM's `stat_logger_plugins` entry point directly (no Prometheus needed), adds NVML physical-layer sampling, and runs a fact-rule engine that emits structured diagnoses with concrete prescriptions ("batch has collapsed to 1 — check upstream serialization", "memory-bound: raise batch until KV cache ~80%, or try AWQ/speculative decoding"). It also does kernel-level PC sampling via a small CUPTI injection library, so you get per-kernel GPU time, source-line hotspots for Triton kernels, and stall breakdowns ("why is it slow" at the warp level).

The newest piece is Autopilot: a diagnosis-driven tuning agent that runs in a sandbox and iterates diagnose → change one vLLM knob → benchmark → keep/revert. On a real box (RTX 5060 Ti, vLLM 0.21, Qwen2.5-0.5B) it took output throughput from 986 to 6,094 tok/s (6.18x), then produced a human-reviewed `vllm serve` command. It also knows how to lose honestly: in one session it pushed concurrency for 5.1x, the p99 TPOT blew the SLA, so it reverted and converged to the honest optimum under that SLA; on a 7B-AWQ bandwidth wall it argued "no applicable knob" and converged without fabricating gains. Promotion to production stays manual.

Everything is `pip install pping-lang && vllm serve <model>` — the dashboard comes up on :8765 with zero config. Apache 2.0, 612 tests.

Live demo (real data captured from a GPU box, no signup): https://leon-hf.github.io/pping-lang/
Repo: https://github.com/leon-hf/pping-lang

Happy to answer questions — especially about the CUPTI injection trick (it works on the default multi-process `vllm serve`, no single-process mode needed) or the rule engine design.

**HN 发帖注意：**
- 用个人账号发，标题别带 emoji，别求 star
- 发布时机：美西周二~周四上午（北京时间晚上）
- 发完 1 小时内盯评论，每条都认真回

---

## 2. Reddit r/LocalLLaMA 草稿

**Title:**
I built a diagnosis plugin for vLLM that tells you *why* your serving is slow — plus an auto-tuning agent that benchmarked its way from 986 to 6,094 tok/s on my 5060 Ti

**Body:**

Like many of you, I've stared at nvidia-smi showing 85% GPU utilization while my vLLM instance crawled. That number is SM duty cycle, not throughput — decode is memory-bound, so utilization lies to you.

Prometheus + Grafana shows the same misleading numbers prettier. I wanted something that just tells me what's wrong and what to do about it, so I built pping-lang:

- Plugs into vLLM's `stat_logger_plugins` — zero config, `pip install` and your normal `vllm serve` gets a dashboard on :8765
- A fact-rule engine that outputs actual diagnoses: "batch collapsed to 1, check upstream serialization", "memory-bound: raise batch until KV cache ~80%, or quantize", etc.
- Roofline view with automatic memory-bound/compute-bound verdict
- Kernel-level PC sampling (CUPTI injection, works with the default multi-process serve): per-kernel GPU time, Triton kernels mapped to Python source lines, stall breakdowns
- Built-in load tester with SLO validation
- Autopilot: an agent that tunes vLLM knobs in a sandbox — diagnose → change ONE knob → benchmark → keep or revert. It got 6.18x on my box and, my favorite part, it reverts honestly when a change breaks the latency SLA instead of claiming victory

Live demo with real captured data (no signup): https://leon-hf.github.io/pping-lang/
GitHub: https://github.com/leon-hf/pping-lang (Apache 2.0)

Works with vLLM 0.20+ (0.13.x partially). Curious what diagnoses people would want added — my rule set is still small.

**Reddit 注意：**
- r/LocalLLaMA 接受 self-promotion 但要求内容实在；正文别像广告
- 也可转 r/MachineLearning（更严，需周五 Self-Promotion 线程）
- 配一张 dashboard 截图作为帖子图片

---

## 3. awesome 列表 PR 文案

目标列表（按优先级）：
- https://github.com/Hannibal046/Awesome-LLM —— Inference/Deployment 区
- 搜 "awesome vllm" / "awesome llm inference" 选 star 数高的 2-3 个
- vLLM 官方 repo 的 ecosystem / community projects 文档（docs/source/community 或 README 链接区，若有）

**PR title:** Add pping-lang: vLLM performance diagnosis plugin

**PR body:**

> [pping-lang](https://github.com/leon-hf/pping-lang) — a performance diagnosis plugin for vLLM. Hooks `stat_logger_plugins` for zero-config real-time metrics (TTFT/TPOT/KV cache/MFU/roofline), a fact-rule engine that emits actionable diagnoses instead of raw numbers, kernel-level PC sampling via CUPTI injection, and a benchmark-verified auto-tuning agent. `pip install pping-lang`, Apache 2.0. Live demo: https://leon-hf.github.io/pping-lang/

---

## 4. vLLM issue 答疑策略（不做硬广）

- 订阅 vLLM repo 的 issue 通知，关键词：performance / slow / utilization / throughput / memory bound
- 遇到匹配的 issue，先认真分析问题本身，给出通用排查思路；最后一句提 "我维护一个插件就是干这个的，可以装上看诊断输出"
- 每周最多 2-3 条，多了会被当 spam

---

## 5. 中文渠道草稿（知乎/掘金标题方向）

- 《GPU 利用率 90% 但吞吐只有 5%？我给 vLLM 写了个会"下诊断结论"的插件》
- 《让 Agent 自动调 vLLM 参数：从 986 到 6094 tok/s，以及它如何学会诚实回滚》
- 正文直接从 Show HN 草稿翻译改写，去掉 HN 腔，多放截图和真机数据
