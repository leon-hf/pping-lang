"""Autopilot —— 诊断驱动的自主性能调优 Agent(M0)。

闭环:observe(诊断) → hypothesize(agent 挂证据选旋钮) → act(沙盒 serve 重启) →
measure(标准 bench 打分) → decide(跟 best 比 Δ:留下 / 回滚 / 平)。3–6 轮收敛,
收尾强制回 best,给实测验证过的 `vllm serve` 配置 + 全程证据×推理轨迹。

设计见 `_design-notes/autopilot-自动调优agent-设计.md` §6/§7/§9。

M0 落地分两层:
- **决策/记录核**(本包内,纯 Python、无 GPU、可单测):objective / action_space /
  scorecard / session_store / agent(可插拔)/ sandbox(协议)/ runner(状态机)。
- **真 GPU 沙盒**(DockerSandbox 真起 vLLM 容器 + 真 bench):独占整张卡,会顶掉在跑的
  serve(单卡现实,决策 G2),作下一增量;本层先用 SimSandbox(确定性、无 GPU)跑通闭环。
"""
