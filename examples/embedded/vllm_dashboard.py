"""真实 vLLM + CUPTI kernel 级 dashboard —— 自搭服务版(不依赖 stat_logger 插件)。

为什么要这个脚本:
    vLLM **离线** `LLM()` 不会触发 stat_logger_plugins(那只在 `vllm serve` 在线
    模式触发)。所以这里手动把 CuptiKernelCollector + NvmlSampler + HTTP API 拼起来,
    vLLM 在主线程持续生成,CUPTI 进程级捕获照样抓到它的 kernel。

跑法(只能在 Linux / WSL2 + NVIDIA GPU 上):
    PYTHONPATH=src python examples/embedded/vllm_dashboard.py
    # 然后浏览器开 http://127.0.0.1:8765/  → Kernel tab

前置:
    - 驱动 ≥ 580 / CUDA 13(vLLM 0.20+ 的 torch cu130 要求);老驱动用 CUDA-12 栈
    - pip: vllm、cupti-python(版本配 CUDA major)、cuda-python、pping-lang 本体
    - 国内: VLLM_USE_MODELSCOPE=True(默认已设)走魔搭下载模型

可用环境变量覆盖:
    PPING_DEMO_MODEL        默认 Qwen/Qwen2.5-0.5B-Instruct
    PPING_DEMO_PORT         默认 8765
    PPING_DEMO_GPU_MEM      默认 0.75(gpu_memory_utilization)
    PPING_DEMO_MAX_LEN      默认 2048
"""
from __future__ import annotations

import os
import signal
import tempfile
import time

# --- vLLM 行为(必须在 import vllm 前设)---
os.environ.setdefault("VLLM_USE_MODELSCOPE", "True")          # 国内走魔搭下载
os.environ.setdefault("VLLM_ENABLE_V1_MULTIPROCESSING", "0")  # 进程内:CUPTI 才抓得到 kernel
os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")     # 免 flashinfer 的 nvcc JIT
os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from pping_lang.api.routes import build_app  # noqa: E402
from pping_lang.api.server import ApiServer  # noqa: E402
from pping_lang.collector.cupti import CuptiKernelCollector  # noqa: E402
from pping_lang.collector.nvml import NvmlSampler, detect_first_gpu_name  # noqa: E402
from pping_lang.rules.store import RuleStore  # noqa: E402
from pping_lang.sink.local import LocalSink  # noqa: E402

MODEL = os.environ.get("PPING_DEMO_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
PORT = int(os.environ.get("PPING_DEMO_PORT", "8765"))
GPU_MEM = float(os.environ.get("PPING_DEMO_GPU_MEM", "0.75"))
MAX_LEN = int(os.environ.get("PPING_DEMO_MAX_LEN", "2048"))

PROMPTS = [
    "Explain how a transformer neural network works, step by step.",
    "Write a short story about a robot learning to paint.",
    "What are the tradeoffs between TCP and UDP? Be detailed.",
    "Describe photosynthesis and the Calvin cycle in detail.",
    "Compare REST and GraphQL APIs with concrete examples.",
    "Explain how GPUs accelerate matrix multiplication.",
]


def main() -> int:
    db = os.path.join(tempfile.gettempdir(), "pping-vllm-demo.duckdb")
    if os.path.exists(db):
        os.remove(db)
    sink = LocalSink(db_path=db, instance_id="vllm-demo", flush_interval_s=1.0)

    # CUPTI 采集器:进程级捕获,必须在 vLLM 建 CUDA context 前 start。
    # capture_stacks=True 开调用栈火焰图(on-demand 深度模式,抓 Python 栈,贵)。
    kcoll = CuptiKernelCollector(sink, rollup_interval_s=2.0, top_n=100, capture_stacks=True)
    kcoll.start()
    print(f"[cupti] enabled={kcoll.enabled}", flush=True)
    if not kcoll.enabled:
        print("[cupti] 采集器未启用(需 Linux + cupti-python + GPU)。继续但 Kernel tab 无数据。", flush=True)

    nvml = NvmlSampler(sink, interval_s=0.5)
    nvml.start()

    app = build_app(
        db_path=db, instance_id="vllm-demo", engine_index=0,
        sink=sink, rule_store=RuleStore(), cupti=kcoll, nvml=nvml,
        gpu_name=detect_first_gpu_name(),
    )
    api = ApiServer(app, host="0.0.0.0", port=PORT)
    api.start()
    print(f"dashboard up: http://127.0.0.1:{PORT}/   → Kernel tab 看真实 kernel", flush=True)

    from vllm import LLM, SamplingParams  # noqa: E402  (import 慢,放这)

    print(f"[vllm] loading {MODEL} ...", flush=True)
    llm = LLM(model=MODEL, gpu_memory_utilization=GPU_MEM, max_model_len=MAX_LEN, dtype="bfloat16")
    sp = SamplingParams(max_tokens=200, temperature=0.7)
    print("=== 持续生成中(Ctrl-C 停),Kernel tab 每 2s 刷新真实数据 ===", flush=True)

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    try:
        n = 0
        while not stop["flag"]:
            outs = llm.generate(PROMPTS, sp, use_tqdm=False)
            n += sum(len(o.outputs[0].token_ids) for o in outs)
            time.sleep(0.3)
    finally:
        print(f"\n[done] 共生成 {n} tokens,收尾...", flush=True)
        kcoll.stop()
        nvml.stop()
        api.stop()
        sink.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
