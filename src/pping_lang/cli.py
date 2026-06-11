"""pping-vllm:一条命令把 pping-lang 接到 vllm(纯 pip 装上即用)。

    pping-vllm serve <model> [vllm 的参数...]

等价于:现编/取缓存 libppingcupti.so → 设好注入 + PC sampling 的环境变量 → `vllm serve ...`。
之后 CUDA 驱动在 cuInit 注入 .so、EngineCore 的 general_plugin 采样、前端 dashboard 读结果
(见 engine_pcs.py)。.so 编不出来时自动降级:仍有 KPI / roofline / NVML / 诊断,只是没 Kernel 级采集。
"""
from __future__ import annotations

import os
import shutil
import sys


def pping_vllm_main() -> None:
    args = sys.argv[1:]

    # 1) 确保 .so(现编或取缓存),设注入 + PCS 环境(均 setdefault,用户可覆盖)
    try:
        from pping_lang.native.builder import ensure_so
        so, cupti_libdir = ensure_so()
        os.environ.setdefault("CUDA_INJECTION64_PATH", so)  # 驱动 cuInit 时加载 → EngineCore 抢 subscriber
        os.environ.setdefault("PPING_LANG_PCS_SO", so)
        if cupti_libdir:
            ld = os.environ.get("LD_LIBRARY_PATH", "")
            os.environ["LD_LIBRARY_PATH"] = cupti_libdir + (f":{ld}" if ld else "")
        os.environ.setdefault("PPING_LANG_ENABLE_PCS", "1")
        print(f"[pping-vllm] PC sampling 就绪:.so={so}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[pping-vllm] .so 准备失败,降级(无 Kernel 级采集,其余照常):{e}", file=sys.stderr)

    # 2) exec vllm,透传全部参数(env 已设好,vllm 及其 EngineCore 子进程继承)
    vllm = shutil.which("vllm") or os.path.join(os.path.dirname(sys.executable), "vllm")
    if not os.path.exists(vllm):
        raise SystemExit("[pping-vllm] 找不到 vllm,请先 pip install vllm")
    os.execv(vllm, [vllm, *args])
