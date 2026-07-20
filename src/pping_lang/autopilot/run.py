"""host 侧 orchestrator CLI —— 在有 docker 的主机上跑一条**真 GPU** autopilot session。

    python -m pping_lang.autopilot.run \
        --model Qwen/Qwen2.5-0.5B-Instruct --image <pping-image> \
        --serve-container <主-serve-容器名> --session-dir <落 JSONL 的目录> \
        --rounds 12 --target throughput --ttft 1000 \
        [--agent-base-url ... --agent-key ... --agent-model ...]

流程:停主 serve 腾卡 → 逐候选 `docker run` 起容器跑真 bench/打分 → teardown →
**finally 必重启主 serve**(dashboard 回来后读 session JSONL 展示这条真实轨迹)。
runner 自身只连 LLM API + 摆 docker,不碰 GPU;占卡的只有被测候选。

公开仓库不写死镜像名/容器名/IP —— 都从 flag 传入(runw 专属调用放本地 deploy 技能)。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from pping_lang.autopilot.api import build_agent, build_objective
from pping_lang.autopilot.runner import Runner
from pping_lang.autopilot.sandbox import BENCH_SPEC, DockerSandbox
from pping_lang.autopilot.session_store import SessionStore
from pping_lang.autopilot.workload import WORKLOAD_SHAPES, resolve_bench, resolve_sla


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _serve_running(name: str) -> bool:
    r = _docker("inspect", "-f", "{{.State.Running}}", name)
    return r.returncode == 0 and r.stdout.strip() == "true"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pping-autopilot", description="真 GPU autopilot 调优 session")
    p.add_argument("--model", required=True)
    p.add_argument("--image", required=True, help="候选 vLLM 容器镜像(带 pping 插件最佳,可读真诊断)")
    p.add_argument("--serve-container", default=None,
                   help="主 serve 容器名;给了就 session 期间停它腾卡、结束必重启")
    p.add_argument("--restore-cmd", default=None,
                   help="收尾时在 host 执行的完整恢复命令(shell)。容器主进程是 sleep "
                        "infinity、serve 靠 docker exec 起的部署形态必须给:单纯 docker "
                        "start 只会拉起 sleep,serve/dashboard 不会回来")
    p.add_argument("--skip-serve-restart", action="store_true",
                   help="收尾只停不启:恢复主 serve 交给外部编排(bridge watcher)。"
                        "bridge 接管主面板端口期间,这里的 docker start 会撞端口,"
                        "把容器打进'跑着但端口没发布'的病态——恢复权必须单一")
    p.add_argument("--session-dir", default=".", help="session JSONL 落盘目录")
    p.add_argument("--session-id", default=None, help="外部指定 session id(bridge 用)")
    p.add_argument("--resume", default=None, help="从已有 session JSONL 恢复并继续跑")
    p.add_argument("--port", type=int, default=8011, help="候选 OpenAI 端口(host 侧)")
    p.add_argument("--internal-port", type=int, default=8000, help="候选容器内 serve 端口")
    p.add_argument("--dash-port", type=int, default=None,
                   help="发布候选 dashboard 端口 → 读真 /api/diagnoses(③ 真诊断);不给则 observe 走启发式")
    p.add_argument("--gpus", default="all")
    p.add_argument("--candidate-name", default="ap-cand")
    p.add_argument("--serve-cmd", default="pping-vllm serve", help="简单形:容器内 serve 命令前缀")
    p.add_argument("--entrypoint", default=None, help="覆盖镜像 entrypoint(模板形配 /bin/bash)")
    p.add_argument("--cmd-template", default=None,
                   help="shell 模板,占位 {model}/{port}/{flags};镜像 entrypoint 非 serve 时用")
    p.add_argument("--volume", action="append", default=[], help="docker -v(可多次)")
    p.add_argument("--env", action="append", default=[], help="docker -e KEY=VAL(可多次)")
    p.add_argument("--cap-add", action="append", default=[], help="docker --cap-add(可多次)")
    p.add_argument("--ready-timeout", type=float, default=300.0)
    p.add_argument("--baseline-max-num-seqs", type=int, default=64, help="基线起点(压低=造喂不饱工况)")
    p.add_argument("--baseline-gpu-util", type=float, default=0.90)
    p.add_argument("--baseline-max-model-len", type=int, default=None,
                   help="基线 max_model_len;候选由 --cmd-template 固定,不传则 baseline 不限制")
    p.add_argument("--bench-concurrency", type=int, default=None,
                   help="压测并发(拉高让提并发真有收益);不给则按 --workload 形态,再无形态兜底 8")
    p.add_argument("--bench-duration", type=int, default=30)
    p.add_argument("--bench-warmup", type=int, default=20)
    p.add_argument("--bench-timeout", type=float, default=BENCH_SPEC["timeout_s"],
                   help="单请求超时秒数(长上下文/容量墙工况需拉高)")
    p.add_argument("--bench-prompt-source", default=BENCH_SPEC["prompt_source"],
                   help="prompt 来源:synthetic / builtin:<name> / file:<path>")
    p.add_argument("--bench-prompt-tokens", type=int, default=None,
                   help="synthetic prompt 的目标 token 数(造 D/KV 容量墙时拉高);不给则按形态")
    p.add_argument("--bench-output-tokens", type=int, default=None,
                   help="解码长度(拉长填 KV 造容量墙 D);不给则按形态,再无形态兜底 128")
    p.add_argument("--bench-repeats", type=int, default=1, help="每个 baseline/candidate 重复 bench 次数(M1 去噪)")
    p.add_argument("--search-mode", default="agent", choices=["agent", "grid", "bo"],
                   help="P2 搜索模式:agent=旧单步候选,grid=坐标小网格,bo=热启动 BO v1 排序")
    p.add_argument("--search-width", type=int, default=3, help="grid/bo 每个旋钮展开的候选档数")
    p.add_argument("--workload", default=None, choices=sorted(WORKLOAD_SHAPES),
                   help="业务形态:自带 bench 负载参数与默认 SLA(M1 业务形态 WorkloadSpec);"
                        "显式 --bench-*/--ttft/--tpot 优先于形态,custom=全手动透传")
    p.add_argument("--quality-gate", action="store_true", help="放开 T2 质量类候选(默认只 T1)")
    p.add_argument("--rounds", type=int, default=12)
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--target", default="throughput", choices=["throughput", "latency", "cost"])
    p.add_argument("--ttft", type=float, default=None, help="TTFT p99 SLA(ms)")
    p.add_argument("--tpot", type=float, default=None, help="TPOT p99 SLA(ms)")
    p.add_argument("--e2e", type=float, default=None, help="E2E p99 SLA(ms),agent/工具调用场景的 deadline")
    p.add_argument("--latency-metric", default=None, choices=["ttft", "tpot"],
                   help="target=latency 时主看哪个指标(默认 tpot)")
    p.add_argument("--floor", type=float, default=None,
                   help="target=latency 时的吞吐硬下限(tok/s),防止'最小化延迟'跌成零吞吐")
    p.add_argument("--agent-base-url", default=None)
    p.add_argument("--agent-key", default=None)
    p.add_argument("--agent-model", default=None)
    p.add_argument("--agent-provider", default=None)
    p.add_argument("--agent-guidance", default="")
    p.add_argument("--agent-lang", default=None, choices=["zh", "en"],
                   help="Agent 自由文本应答语言(rationale/reason/思考过程),默认中文")
    args = p.parse_args(argv)

    agent_cfg = None
    agent_key = args.agent_key or os.environ.get("AGENT_KEY")
    agent_base = args.agent_base_url or os.environ.get("AGENT_BASE_URL")
    agent_model = args.agent_model or os.environ.get("AGENT_MODEL")
    agent_provider = args.agent_provider or os.environ.get("AGENT_PROVIDER")
    agent_guidance = args.agent_guidance or os.environ.get("AGENT_GUIDANCE", "")
    agent_lang = args.agent_lang or os.environ.get("AGENT_LANG", "zh")
    if agent_key and agent_base and agent_model:
        agent_cfg = {"base_url": agent_base, "api_key": agent_key,
                     "model": agent_model, "provider": agent_provider,
                     "guidance": agent_guidance, "lang": agent_lang}

    env = {}
    for kv in args.env:
        k, _, v = kv.partition("=")
        env[k] = v

    baseline_config = {"max_num_seqs": args.baseline_max_num_seqs,
                       "gpu_memory_utilization": args.baseline_gpu_util}
    if args.baseline_max_model_len is not None:
        baseline_config["max_model_len"] = args.baseline_max_model_len
    # 业务形态 WorkloadSpec:显式 flag > 形态默认 > 旧兜底(8/128/BENCH_SPEC)
    shape = args.workload or ""
    _rb = resolve_bench(shape, {"prompt_tokens": args.bench_prompt_tokens,
                                "output_tokens": args.bench_output_tokens,
                                "concurrency": args.bench_concurrency})
    sla_ttft, sla_tpot = resolve_sla(shape, args.ttft, args.tpot)
    bench_spec = {**BENCH_SPEC, "concurrency": _rb.get("concurrency", 8),
                  "duration_s": args.bench_duration, "warmup_s": args.bench_warmup,
                  "timeout_s": args.bench_timeout,
                  "prompt_source": args.bench_prompt_source,
                  "prompt_tokens": _rb.get("prompt_tokens", BENCH_SPEC["prompt_tokens"]),
                  "output_tokens": _rb.get("output_tokens", 128)}

    session_dir = Path(args.session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    agent = build_agent(agent_cfg)
    elapsed_s = 0.0
    if args.resume:
        session_path = Path(args.resume)
        store = SessionStore(session_path)
        sess = store.resume_session()
        if sess is None:
            print(f"[autopilot] resume failed: cannot load {session_path}", file=sys.stderr)
            return 2
        sid = sess.session_id
        objective = sess.objective
        budget = sess.budget
        # 时间预算按 session 起点算,不因进程重启而重置(§9.1 G5:双预算任一到顶即停)
        try:
            import calendar
            started = calendar.timegm(time.strptime(sess.started_ts, "%Y-%m-%dT%H:%M:%SZ"))
            elapsed_s = max(0.0, time.time() - started)
        except (ValueError, TypeError):
            pass
    else:
        objective = {"target": args.target,
                     "sla": {"ttft_p99_ms": sla_ttft, "tpot_p99_ms": sla_tpot,
                             "e2e_p99_ms": args.e2e}}
        if shape:
            objective["workload"] = shape          # 报告/status 可见本次按什么业务形态调的
        if args.latency_metric:
            objective["latency_metric"] = args.latency_metric
        if args.floor is not None:
            objective["floor"] = {"output_tps": args.floor}
        budget = {"rounds": args.rounds, "minutes": args.minutes}
        sid = args.session_id or "ap-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        store = SessionStore(session_dir / f"{sid}.jsonl")
        store.new_session(sid, objective, budget, getattr(agent, "model", ""))

    sandbox = DockerSandbox(
        args.model, args.image, port=args.port, internal_port=args.internal_port,
        gpus=args.gpus, container=args.candidate_name, serve_cmd=tuple(args.serve_cmd.split()),
        entrypoint=args.entrypoint, cmd_template=args.cmd_template,
        env=env, volumes=tuple(args.volume), cap_add=tuple(args.cap_add),
        ready_timeout_s=args.ready_timeout, bench_spec=bench_spec, dash_port=args.dash_port)

    serve = args.serve_container
    serve_was_up = bool(serve) and _serve_running(serve)

    print(f"[autopilot] session {sid} · model={args.model} · agent={getattr(agent, 'model', '?')}"
          f"{' · resume' if args.resume else ''}")
    try:
        if serve_was_up:
            print(f"[autopilot] 停主 serve 容器 '{serve}' 腾卡 …")
            _docker("stop", serve)
        Runner(store=store, sandbox=sandbox, agent=agent,
               obj=build_objective(objective), budget=budget, model=args.model,
               step_delay_s=0.0, baseline_config=baseline_config,
               quality_gate=args.quality_gate, bench_repeats=args.bench_repeats,
               search_mode=args.search_mode, search_width=args.search_width,
               elapsed_s=elapsed_s).run()                        # 同步跑到底
    finally:
        sandbox.teardown()                                       # 兜底清候选容器
        if serve_was_up and args.skip_serve_restart:
            print(f"[autopilot] 主 serve '{serve}' 由外部编排恢复(--skip-serve-restart)")
        elif serve_was_up:
            print(f"[autopilot] 重启主 serve 容器 '{serve}' …")
            _docker("start", serve)
            if args.restore_cmd:                                 # sleep-infinity 形态还需 exec serve
                subprocess.run(["bash", "-c", args.restore_cmd])
        store.close()

    st = store.status_dict()
    print(f"\n[autopilot] state={st['state']} · baseline={st.get('baseline_score')} "
          f"· best={st.get('best', {}).get('score')}")
    for r in st.get("rounds", []):
        sc = r.get("scorecard_after") or {}
        p0 = ((r.get("diagnosis") or {}).get("p0_kvfit") or {})
        p0_note = f" p0_pruned={p0.get('pruned')}" if p0.get("pruned") else ""
        print(f"  R{r['round']:<2} {r['kind']:<9} {r['decision']:<9} "
              f"tps={round(sc.get('output_tps', 0)) if sc else '-':<6} "
              f"{(r.get('action') or {}).get('flag', '')}{p0_note}")
    print(f"[autopilot] 推荐: {st.get('recommended_command')}")
    print(f"[autopilot] JSONL: {session_dir / (sid + '.jsonl')}")
    return 0


if __name__ == "__main__":                                       # pragma: no cover
    sys.exit(main())
