"""沙盒 —— serve 生命周期(§7)。

接口:apply(config) 起+就绪、measure(obj) 跑标准 bench 打分、teardown() 干净停。
- **SimSandbox**:确定性公式(无 GPU、无网络),复现 mock 那条"提并发→撞 KV 容量瓶颈"的
  调优曲线;M0 本层跑通闭环 + 单测用。
- **DockerSandbox**:真起 `vllm/vllm-openai` 一次性容器 → 真 bench → `docker rm -f`。
  独占整张卡(决策 G2),会顶掉在跑的 serve;作下一增量,**未单测**(需 runw)。
"""
from __future__ import annotations

import re
import socket
import time
from typing import Any
from urllib.parse import urlencode

from pping_lang.autopilot.action_space import bottleneck_label
from pping_lang.autopilot.objective import ObjectiveSpec, Scorecard
from pping_lang.autopilot.scorecard import BENCH_SPEC, BenchError, bench_scorecard

__all__ = [
    "BENCH_SPEC", "BenchError", "DockerSandbox", "LaunchError", "SimSandbox",
    "TeardownError", "bench_scorecard",
]

# §7.1 配置常量(可 env 覆盖):端口与 dashboard 8765 区隔;就绪超时含模型加载+cudagraph。
AUTOPILOT_PORT = 8011
READY_TIMEOUT_S = 180.0
WARMUP_REQUESTS = 3
TEARDOWN_GRACE_S = 10.0
GPU_FREE_TIMEOUT_S = 30.0
EQUIVALENCE_PROMPTS = (
    "Answer with one short factual sentence: what is 2 + 2?",
    "Write exactly one concise sentence about GPU memory.",
)

# 就绪心跳的日志摘取:vLLM 启动日志九成是噪声("Unknown vLLM environment variable
# detected"之类),只挑真的标志进度的行(加载/编译/CUDA graph/显存/KV);挑到的行再
# 剥掉 "(APIServer pid=N) INFO MM-DD HH:MM:SS [file.py:NNN]" 这层前缀,只剩人话。
_LOG_INTERESTING_RE = re.compile(
    r"loading|load(ed|ing)? took|CUDA graph|Capturing|Profiling|torch\.compil|"
    r"Warming up|memory profil|KV cache|GiB|took \d",
    re.IGNORECASE,
)
_LOG_PID_PREFIX_RE = re.compile(r"^\(\w+ pid=\d+\)\s*")
_LOG_TS_PREFIX_RE = re.compile(r"^(?:INFO|WARNING|ERROR)\s+[\d-]+\s+[\d:]+\s+\[[^\]]+\]\s*")


def _clean_log_line(line: str) -> str:
    # 两层前缀顺序剥离:"(EngineCore pid=206) " 和 "INFO 07-08 23:35:42 [monitor.py:53] "
    # 常常叠在一起,单条 ^ 锚定的正则一次 sub 只能吃掉最外层那层。
    s = _LOG_PID_PREFIX_RE.sub("", line.strip())
    return _LOG_TS_PREFIX_RE.sub("", s)

_CONFIG_ATTRS = {
    "max_num_seqs": ("scheduler_config", "max_num_seqs"),
    "max_num_batched_tokens": ("scheduler_config", "max_num_batched_tokens"),
    "max_num_partial_prefills": ("scheduler_config", "max_num_partial_prefills"),
    "long_prefill_token_threshold": ("scheduler_config", "long_prefill_token_threshold"),
    "gpu_memory_utilization": ("cache_config", "gpu_memory_utilization"),
    "kv_cache_dtype": ("cache_config", "cache_dtype"),
    "max_model_len": ("model_config", "max_model_len"),
    "cpu_offload_gb": ("model_config", "cpu_offload_gb"),
    "enable_chunked_prefill": ("scheduler_config", "enable_chunked_prefill"),
    "enable_prefix_caching": ("cache_config", "enable_prefix_caching"),
    "async_scheduling": ("scheduler_config", "async_scheduling"),
}

_PROBE_METRICS = {
    "kv_cache_usage": "vllm.scheduler.kv_cache_usage_ratio",
    "running_reqs": "vllm.scheduler.running_reqs",
    "waiting_reqs": "vllm.scheduler.waiting_reqs",
    "skipped_waiting_reqs": "vllm.scheduler.skipped_waiting_reqs",
    "preempted_reqs": "vllm.iter.preempted_reqs",
    "mfu": "vllm.perf.mfu_ratio",
    "gpu_mem_bw_pct": "gpu.mem_util_pct",
}


class LaunchError(RuntimeError):
    """候选起不来(vllm 启动失败 / OOM / 就绪超时)→ 判负回滚。"""


class TeardownError(RuntimeError):
    """候选清理后仍有端口/显存残留 → session failed,给人工清理。"""


class SimSandbox:
    """确定性模拟:Scorecard = f(max_num_seqs, gpu_memory_utilization)。

    物理直觉:并发越高吞吐越高(次线性);但 KV 容量 ∝ gpu_util,并发超过 KV 容量 →
    抢占 → 吞吐回落 + TTFT 飙升破 SLA。这正是 B↔D 一根绳 / demo trace 的内核。
    """

    def __init__(self, model: str = "Qwen/Qwen2.5-0.5B-Instruct", gpu_name: str = "sim-GPU") -> None:
        self._model = model
        self._gpu = gpu_name
        self._cfg: dict = {}

    def apply(self, config: dict) -> None:
        # 朴素越界守卫:gpu_util > 0.97 视为 OOM 起不来(launch-catch)
        if config.get("gpu_memory_utilization", 0.7) > 0.97:
            raise LaunchError("gpu_memory_utilization too high (sim OOM)")
        self._cfg = dict(config)

    def endpoint(self) -> str:
        return "sim://local"

    def effective_config(self) -> dict:
        return dict(self._cfg)

    def sample_outputs(self, prompts: tuple[str, ...] = EQUIVALENCE_PROMPTS) -> list[str]:
        return [f"sim:{p}" for p in prompts]

    def teardown(self) -> None:
        self._cfg = {}

    def measure(self, obj: ObjectiveSpec) -> Scorecard:
        seqs = float(self._cfg.get("max_num_seqs", 32))
        util = float(self._cfg.get("gpu_memory_utilization", 0.70))
        kv_capacity = 140.0 * (util / 0.70)                  # KV 能撑住的并发上限
        eff = min(seqs, kv_capacity)
        tps = 1240.0 * (eff / 32.0) ** 0.62                  # 吞吐:次线性升
        ttft = 380.0 * (eff / 32.0) ** 0.55
        tpot = 22.0 * (eff / 32.0) ** 0.25
        err = 0.0
        if seqs > kv_capacity:                               # 过订阅 → 抢占
            over = seqs / kv_capacity
            tps *= 0.97                                       # 吞吐略回落(重算开销)
            ttft *= over ** 1.7                               # TTFT 飙升 → 破 SLA
            if over > 1.6:
                err = 0.02
        return Scorecard(
            output_tps=round(tps, 1), ttft_p99_ms=round(ttft, 0),
            tpot_p99_ms=round(tpot, 1), e2e_p99_ms=round(ttft + tpot * 100, 0),
            error_rate=err,
            run_meta={**BENCH_SPEC, "model": self._model, "gpu": self._gpu, "sim": True},
        )


class DockerSandbox:
    """真沙盒:`docker run -d` 一次性候选 vLLM 容器 → 等就绪 → 真 bench → `docker rm -f`。

    跑在有 docker 的主机上(runner 是 host 侧 CLI,自身不碰 GPU)。候选独占整张卡(G2):
    调 session 期间机上无 prod 要保护,当前 serve 让位。teardown 幂等;起不来/OOM/就绪超时
    都抓成 LaunchError(带容器日志尾)→ 判负回滚。image 由调用方给(公开仓库不写死镜像名)。
    """

    def __init__(self, model: str, image: str, *, port: int = 8011, internal_port: int = 8000,
                 gpus: str = "all", container: str = "ap-cand",
                 serve_cmd: tuple[str, ...] = ("pping-vllm", "serve"),
                 entrypoint: str | None = None, cmd_template: str | None = None,
                 env: dict | None = None, extra_args: tuple[str, ...] = (),
                 volumes: tuple[str, ...] = (), cap_add: tuple[str, ...] = (),
                 ready_timeout_s: float = READY_TIMEOUT_S, poll_s: float = 3.0,
                 host: str = "127.0.0.1", bench_spec: dict | None = None,
                 dash_port: int | None = None, dash_internal: int = 8765) -> None:
        self._model, self._image, self._port = model, image, int(port)
        self._internal = int(internal_port)
        self._gpus, self._container = gpus, container
        self._serve_cmd = list(serve_cmd)
        self._entrypoint = entrypoint
        # cmd_template:用 {model}/{port}/{flags} 占位的 shell 串(需配 --entrypoint /bin/bash)。
        # 给镜像 entrypoint 不是 serve 的场景(如本仓 image entrypoint=vllm serve,必须覆盖)。
        self._cmd_template = cmd_template
        # dash_port:发布候选自己的 dashboard 端口 → measure 后读真 /api/diagnoses(③ 真诊断)
        self._dash_port = int(dash_port) if dash_port else None
        self._dash_internal = int(dash_internal)
        self._env = dict(env or {})
        if self._dash_port:                              # 让候选插件把 dashboard 绑到已知内端口
            self._env.setdefault("PPING_LANG_API_PORT", str(self._dash_internal))
        self._extra = list(extra_args)
        self._volumes = list(volumes)
        self._cap_add = list(cap_add)
        self._ready_timeout = float(ready_timeout_s)
        self._poll = float(poll_s)
        self._host = host
        self._spec = bench_spec or BENCH_SPEC
        self._cfg: dict = {}
        self._gpu_baseline_mib: int | None = None
        self._progress = None            # set_progress(cb):apply 长静默窗内给 UI 喂心跳

    def set_progress(self, cb) -> None:
        """cb(message: str) —— runner 接到 session 事件流;apply 起容器+等就绪要 2 分钟,
        没有中间反馈时 UI 是死的。"""
        self._progress = cb

    def _report(self, msg: str) -> None:
        if self._progress:
            try:
                self._progress(msg)
            except Exception:  # noqa: BLE001 — 进度反馈绝不打断调优
                pass

    def endpoint(self) -> str:
        return f"http://{self._host}:{self._port}/v1"

    def effective_config(self) -> dict:
        """Best-effort live config read.

        When the candidate publishes the pping dashboard, /api/info exposes vLLM's
        resolved config captured by the plugin. Fall back to the tracked flag dict
        if that endpoint is unavailable.
        """
        cfg = dict(self._cfg)
        info = self._read_info()
        resolved = (info or {}).get("resolved_config") or {}
        extracted = _extract_config(resolved)
        if extracted:
            cfg.update(extracted)
        return cfg

    def sample_outputs(self, prompts: tuple[str, ...] = EQUIVALENCE_PROMPTS) -> list[str]:
        """Deterministic smoke outputs for T2 equivalence checks."""
        return [self._chat_once(p, max_tokens=48) for p in prompts]

    def _docker(self, *args: str):
        import subprocess
        return subprocess.run(["docker", *args], capture_output=True, text=True)

    def teardown(self) -> None:
        self._docker("rm", "-f", self._container)        # 幂等:容器不存在也不报错
        self._verify_teardown()

    def _port_open(self, port: int) -> bool:
        try:
            with socket.create_connection((self._host, int(port)), timeout=0.3):
                return True
        except OSError:
            return False

    def _gpu_used_mib(self) -> int | None:
        import subprocess
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True)
        except FileNotFoundError:
            return None
        if r.returncode != 0:
            return None
        vals: list[int] = []
        for line in r.stdout.splitlines():
            try:
                vals.append(int(line.strip()))
            except ValueError:
                pass
        return sum(vals) if vals else None

    def _verify_teardown(self) -> None:
        """显式 teardown 验证(§7.2):容器停、端口释放、GPU 显存回到启动前基线附近。

        GPU 查询不可用时只跳过显存项;端口仍是硬验证。这样本地无 GPU 单测不会被环境误伤。
        """
        deadline = time.monotonic() + GPU_FREE_TIMEOUT_S
        ports = [self._port] + ([self._dash_port] if self._dash_port else [])
        while time.monotonic() < deadline:
            running = self._alive()
            ports_open = [p for p in ports if self._port_open(p)]
            used = self._gpu_used_mib()
            gpu_ok = used is None or self._gpu_baseline_mib is None or used <= self._gpu_baseline_mib + 256
            if not running and not ports_open and gpu_ok:
                return
            time.sleep(0.5)
        used = self._gpu_used_mib()
        open_ports = [p for p in ports if self._port_open(p)]
        raise TeardownError(
            f"候选清理未完成: running={self._alive()} open_ports={open_ports} "
            f"gpu_used_mib={used} baseline_mib={self._gpu_baseline_mib}")

    def _alive(self) -> bool:
        r = self._docker("inspect", "-f", "{{.State.Running}}", self._container)
        return r.returncode == 0 and r.stdout.strip() == "true"

    def _logs_tail(self, n: int = 40) -> str:
        r = self._docker("logs", "--tail", str(n), self._container)
        return ((r.stdout or "") + (r.stderr or ""))[-1500:]

    def _find_log_line(self, needle: str, tail: int = 400) -> str | None:
        r = self._docker("logs", "--tail", str(tail), self._container)
        for line in (((r.stdout or "") + (r.stderr or "")).splitlines()):
            if needle in line:
                return line.strip()
        return None

    def _logs_tail_interesting(self, n: int = 30) -> str | None:
        """就绪心跳用:最近日志里最后一条真正标志进度的行(加载/编译/CUDA graph/显存),
        跳过环境变量警告之类的噪声;命中的行剥掉 pid/时间戳前缀。没有可报的行 → None
        (心跳退化成纯计时,总比刷一行没用的噪声强)。"""
        r = self._docker("logs", "--tail", str(n), self._container)
        lines = ((r.stdout or "") + (r.stderr or "")).splitlines()
        for line in reversed(lines):
            if _LOG_INTERESTING_RE.search(line):
                return _clean_log_line(line)[-100:]
        return None

    def apply(self, config: dict) -> None:
        from pping_lang.autopilot.action_space import render_flags
        self.teardown()                                  # 清掉上一候选
        self._gpu_baseline_mib = self._gpu_used_mib()
        flags = render_flags(config)
        head = ["run", "-d", "--name", self._container, "--gpus", self._gpus,
                "-p", f"{self._port}:{self._internal}"]
        if self._dash_port:                              # 发布候选 dashboard → 读真 /api/diagnoses
            head += ["-p", f"{self._dash_port}:{self._dash_internal}"]
        for c in self._cap_add:
            head += ["--cap-add", c]
        for k, v in self._env.items():
            head += ["-e", f"{k}={v}"]
        for v in self._volumes:
            head += ["-v", v]
        if self._cmd_template:                           # entrypoint 覆盖 + shell 模板(tuning flags 在尾,覆盖基线 flag)
            import shlex
            quoted = " ".join(shlex.quote(f) for f in flags)   # JSON flag 值须防 shell 拆词/吃引号
            shell = self._cmd_template.format(model=self._model, port=self._internal, flags=quoted)
            tail = ["--entrypoint", self._entrypoint or "/bin/bash", self._image, "-c", shell]
        else:                                            # 简单形:直接 serve_cmd model flags
            ep = ["--entrypoint", self._entrypoint] if self._entrypoint else []
            tail = [*ep, self._image, *self._serve_cmd, self._model, *flags, *self._extra]
        run = self._docker(*head, *tail)
        if run.returncode != 0:
            raise LaunchError(f"docker run 失败:{(run.stderr or run.stdout).strip()}")
        self._cfg = dict(config)
        self._report("候选容器已启动,等待 vLLM API 就绪(模型加载 + 显存分配)…")
        self._wait_ready()

    def _http_ok(self, url: str, timeout: float) -> bool:
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001 — 还没起好
            return False

    def _inference_probe(self, timeout: float) -> bool:
        """发一个真推理请求:既确认推理可用,又触发并跑完冷引擎首个 Triton kernel JIT 编译。

        Blackwell(sm_120)上冷引擎首请求要 JIT 编译 _compute_slot_mapping_kernel,耗时常
        超过整个 bench 窗口 → 不预热则窗口内零请求完成(0 样本判负)。给足超时让它编完。
        """
        import json
        import urllib.request
        body = json.dumps({"model": self._model, "max_tokens": 8, "stream": False,
                           "messages": [{"role": "user", "content": "warmup"}]}).encode("utf-8")
        req = urllib.request.Request(
            f"http://{self._host}:{self._port}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200 and bool(json.loads(resp.read()).get("choices"))
        except Exception:  # noqa: BLE001
            return False

    def _chat_once(self, prompt: str, *, max_tokens: int = 8, timeout: float = 60.0) -> str:
        import json
        import urllib.request
        body = json.dumps({
            "model": self._model, "max_tokens": max_tokens, "stream": False,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"http://{self._host}:{self._port}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return (data["choices"][0]["message"].get("content") or "").strip()

    def _wait_ready(self) -> None:
        import time
        start = time.monotonic()
        deadline = start + self._ready_timeout
        last_report = start
        # 阶段1:API server 起来(/v1/models 200)
        url = f"http://{self._host}:{self._port}/v1/models"
        while not self._http_ok(url, timeout=4):
            if not self._alive():                        # 容器退出 = 起不来/OOM
                logs = self._logs_tail()
                self.teardown()
                raise LaunchError(f"候选容器退出(起不来/OOM)。日志尾:\n{logs}")
            now = time.monotonic()
            if now >= deadline:
                logs = self._logs_tail()
                self.teardown()
                raise LaunchError(f"候选 API 就绪超时({self._ready_timeout:.0f}s)。日志尾:\n{logs}")
            if now - last_report >= 15:                  # 心跳:长静默窗内 UI 不能死着
                stage = self._logs_tail_interesting()     # 过滤噪声,只留有信息量的进度行
                self._report(f"等待 vLLM API 就绪 {int(now - start)}s / {self._ready_timeout:.0f}s"
                             + (f" · {stage}" if stage else " …"))
                last_report = now
            time.sleep(self._poll)
        kv_line = self._find_log_line("KV cache size")   # 引擎实测 KV 池,D regime 的关键底数
        if kv_line:
            self._report(f"引擎就绪:{_clean_log_line(kv_line)[-110:]}")
        # 阶段2:真推理探针 —— 确认推理可用 + 跑完首个 JIT(冷引擎首请求慢,否则 bench 0 样本)。
        # 重试式:Blackwell 首推理时长高方差(实测 26s ~ >180s,JIT/cudagraph 竞争),单发长超时
        # 会被一次卡死请求挂满;短超时 + 重试,新连接往往立即成功。
        self._report(f"API 已就绪({int(time.monotonic() - start)}s),推理探针 + kernel JIT 预热中…")
        warm_deadline = time.monotonic() + min(self._ready_timeout, 180.0)
        attempt = 0
        while True:
            attempt += 1
            if self._inference_probe(timeout=60.0):
                break
            if time.monotonic() >= warm_deadline:
                logs = self._logs_tail()
                self.teardown()
                raise LaunchError(f"候选推理探针失败(JIT/推理卡死,{attempt} 次尝试)。日志尾:\n{logs}")
            self._report(f"推理探针第 {attempt} 次未通过,重试(JIT 预热可能仍在进行)…")
            time.sleep(2.0)
        self._report("候选就绪,进入压测")

    def read_diagnosis(self) -> dict | None:
        """读候选自己的 /api/diagnoses(同一诊断引擎),取最严重的一条 → bottleneck A/B/C/D。

        rule_id 直接就是瓶颈字母。没发布 dashboard 端口 / 读不到 / 没命中 → None(Runner 回退启发式)。
        """
        if not self._dash_port:
            return None
        import json
        import urllib.request
        try:
            url = f"http://{self._host}:{self._dash_port}/api/diagnoses?seconds=120"
            with urllib.request.urlopen(url, timeout=5) as r:
                diags = (json.loads(r.read()).get("diagnoses")) or []
        except Exception:  # noqa: BLE001
            return None
        rank = {"critical": 3, "warning": 2, "info": 1}
        top = max(diags, key=lambda d: (rank.get(d.get("severity"), 0), d.get("ts_ns", 0)),
                  default=None)
        if not top or top.get("rule_id") not in ("A", "B", "C", "D"):
            return None
        bn = top["rule_id"]
        ctx = top.get("context") or {}
        return {
            "bottleneck": bn,
            "evidence_refs": [f"{bottleneck_label(bn)}·实时确认", f"diagnosis:{top.get('message', '')}",
                              *[f"{k}={round(v, 3)}" for k, v in list(ctx.items())[:3]]],
            "metrics": ctx,
            "source": "live:/api/diagnoses",
        }

    def _read_info(self) -> dict | None:
        if not self._dash_port:
            return None
        import json
        import urllib.request
        try:
            with urllib.request.urlopen(
                    f"http://{self._host}:{self._dash_port}/api/info", timeout=5) as r:
                return json.loads(r.read())
        except Exception:  # noqa: BLE001
            return None

    def _runtime_probe(self, start_ns: int, end_ns: int) -> dict:
        """Summarize scheduler metrics emitted during the measured bench window.

        Reading /api/diagnoses after the bench can miss short KV-pressure spikes
        because the diagnosis window may already include idle/teardown tail. This
        probe binds evidence to the actual bench interval.
        """
        if not self._dash_port or end_ns <= start_ns:
            return {}
        import json
        import urllib.request

        window_s = max(1, int((end_ns - start_ns) / 1e9) + 5)
        out: dict[str, dict[str, float | int | None]] = {}
        for label, name in _PROBE_METRICS.items():
            qs = urlencode({"name": name, "seconds": window_s, "limit": 10000})
            try:
                with urllib.request.urlopen(
                        f"http://{self._host}:{self._dash_port}/api/metrics/recent?{qs}",
                        timeout=5) as r:
                    points = (json.loads(r.read()).get("points")) or []
            except Exception:  # noqa: BLE001
                continue
            vals = [
                float(p["value"]) for p in points
                if start_ns <= int(p.get("ts_ns", 0)) <= end_ns and p.get("value") is not None
            ]
            if not vals:
                continue
            out[label] = {
                "max": max(vals),
                "avg": sum(vals) / len(vals),
                "sum": sum(vals),
                "last": vals[-1],
                "n": len(vals),
            }
        return out

    def _bench_progress(self, p: dict) -> None:
        """run_static 的运行中快照 → 直播事件:看着分数长出来。"""
        msg = f"压测 {p['elapsed_s']}s:完成 {p['ok']} req · 瞬时 {p['tps']:g} tok/s"
        if p.get("ttft_p50_ms") is not None:
            msg += f" · TTFT p50 {p['ttft_p50_ms']:g}ms"
        if p.get("errors"):
            msg += f" · ⚠ 错误 {p['errors']}"
        self._report(msg)

    def live_stats_line(self) -> str | None:
        """候选引擎此刻的关键指标一行(诊断证据实时形成)。没发布 dashboard → None。"""
        if not self._dash_port:
            return None
        import json
        import urllib.request
        parts: list[str] = []
        specs = (("running_reqs", "vllm.scheduler.running_reqs", "running {v:g}"),
                 ("kv", "vllm.scheduler.kv_cache_usage_ratio", "KV {pct:.0f}%"),
                 ("mfu", "vllm.perf.mfu_ratio", "MFU {pct:.1f}%"))
        for _, name, fmt in specs:
            qs = urlencode({"name": name, "seconds": 15, "limit": 5})
            try:
                with urllib.request.urlopen(
                        f"http://{self._host}:{self._dash_port}/api/metrics/recent?{qs}",
                        timeout=2) as r:
                    points = (json.loads(r.read()).get("points")) or []
            except Exception:  # noqa: BLE001
                continue
            vals = [float(p["value"]) for p in points if p.get("value") is not None]
            if vals:
                v = vals[-1]
                parts.append(fmt.format(v=v, pct=v * 100))
        return " · ".join(parts) if parts else None

    def measure(self, obj: ObjectiveSpec) -> Scorecard:
        bench_start_ns = time.time_ns()
        sc = bench_scorecard(self.endpoint(), self._model, self._spec,
                             run_meta={**self._cfg, "container": self._container},
                             on_progress=self._bench_progress)
        bench_end_ns = time.time_ns()
        probe = self._runtime_probe(bench_start_ns, bench_end_ns)
        if probe:
            sc.run_meta["runtime_probe"] = probe
        diag = self.read_diagnosis()                     # bench 刚跑完、候选还在 → 读真诊断
        if diag:
            sc.run_meta["diagnosis"] = diag
        eff = self.effective_config()
        if eff:
            sc.run_meta["effective_config"] = eff
        return sc


def _extract_config(resolved: dict[str, Any]) -> dict:
    out: dict[str, Any] = {}
    for key, (section, attr) in _CONFIG_ATTRS.items():
        sub = resolved.get(section) or {}
        if attr in sub:
            out[key] = sub[attr]
    return out
