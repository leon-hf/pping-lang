"""部署用 live dashboard 启动器:产品全栈(dashboard + PcSamplingController + .so)对真 vLLM。

流程:warmup(cuInit 触发注入抢 CUPTI 槽)→ prime(早 enable PC sampling)→ 起 dashboard →
载 vLLM → pre-warm 吃光 triton JIT → 稳态持续推理 + 采样。详见 native/ppingcupti/README.md §11。

环境变量:
  PPING_MODEL      模型(默认 Qwen/Qwen2.5-0.5B-Instruct;走 modelscope,缓存到挂载的 /models)
  PPING_GPU_NAME   面板显示的 GPU 名
  PPING_PORT       端口(默认 8765)
  PPING_GPU_MEM    gpu_memory_utilization(默认 0.5)
由部署脚本在容器里设好:CUDA_INJECTION64_PATH / PPING_LANG_PCS_SO / LD_LIBRARY_PATH。
"""
import os
import sys
import threading
import time

os.environ.setdefault("VLLM_USE_MODELSCOPE", "True")
os.environ.setdefault("VLLM_ENABLE_V1_MULTIPROCESSING", "0")
os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")
os.environ["PPING_LANG_PCS_SO"] = "/tmp/libppingcupti.so"
os.environ.setdefault("CUDA_INJECTION64_PATH", "/tmp/libppingcupti.so")  # torch 之前抢 CUPTI 槽
os.environ.setdefault("PPING_PCS_DEBUG", "1")
sys.path.insert(0, "/work/src")

MODEL = os.environ.get("PPING_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
GPU_NAME = os.environ.get("PPING_GPU_NAME", "remote GPU")
PORT = int(os.environ.get("PPING_PORT", "8765"))
GPU_MEM = float(os.environ.get("PPING_GPU_MEM", "0.5"))

from pping_lang.api.routes import build_app  # noqa: E402
from pping_lang.api.server import ApiServer  # noqa: E402
from pping_lang.collector.cupti import (  # noqa: E402
    CtypesPcSamplingLib, CuptiKernelCollector, FakeActivitySource, PcSamplingController,
)
from pping_lang.hardware import lookup_peak  # noqa: E402
from pping_lang.rules.store import RuleStore  # noqa: E402
from pping_lang.sink.local import LocalSink  # noqa: E402

DB = "/tmp/pping-vllm.duckdb"
if os.path.exists(DB):
    os.remove(DB)
sink = LocalSink(db_path=DB, instance_id="vllm-deploy", flush_interval_s=1.0)

# warmup → cuInit 触发注入(InitializeInjection 抢 subscriber 槽);注入须先于 ctypes 加载
import torch  # noqa: E402
a = torch.randn(4096, 4096, device="cuda"); b = torch.randn(4096, 4096, device="cuda")
a = (a @ b); torch.cuda.synchronize()

lib = CtypesPcSamplingLib()
print("[pcs] available:", lib.available(), flush=True)
controller = PcSamplingController(lib, sink=sink)
print("[pcs] prime:", controller.prime(12), "| started:", controller.started, flush=True)

coll = CuptiKernelCollector(sink, source=FakeActivitySource(available=False), pc_sampling=controller)
app = build_app(
    db_path=DB, instance_id="vllm-deploy", engine_index=0, sink=sink,
    rule_store=RuleStore(), cupti=coll, gpu_name=GPU_NAME,
    gpu_peak=lookup_peak(GPU_NAME),   # 让 roofline 画出屋脊线(5060 Ti 已加进 hardware 表)
)
api = ApiServer(app, host="0.0.0.0", port=PORT)
api.start()
print(f"[dash] up: http://localhost:{PORT}  -> Kernel tab -> Deep Evidence", flush=True)

from vllm import LLM, SamplingParams  # noqa: E402
print(f"[vllm] loading {MODEL} ...", flush=True)
llm = LLM(model=MODEL, gpu_memory_utilization=GPU_MEM,
          max_model_len=2048, dtype="bfloat16", enforce_eager=True)
sp = SamplingParams(max_tokens=96, temperature=0.7)
PROMPTS = [
    "Explain how a transformer neural network works, step by step.",
    "Write a detailed short story about a robot learning to paint.",
    "Compare TCP and UDP with concrete examples, in depth.",
    "Describe how GPUs accelerate matrix multiplication.",
]

# pre-warm(§11 确定性主路):变长 prompt 把有限的 triton JIT 特化全吃光,稳态无运行时 JIT
print("[warm] pre-warming triton JIT ...", flush=True)
for _i in range(16):
    _plen = 3 + _i * 9
    llm.generate(["word " * _plen],
                 SamplingParams(max_tokens=16 + _i, min_tokens=16 + _i,
                                temperature=0.0, ignore_eos=True),
                 use_tqdm=False)
time.sleep(1.5)
print("[warm] done -- steady sampling start", flush=True)

_stop = {"f": False}


def _gen_loop():
    while not _stop["f"]:
        llm.generate(PROMPTS, sp, use_tqdm=False)


threading.Thread(target=_gen_loop, daemon=True).start()
print("[vllm] running. Open dashboard -> Kernel tab.", flush=True)

try:
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    _stop["f"] = True
    controller.close()
    api.stop()
    sink.close()
