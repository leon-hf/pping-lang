"""GPU peak 性能常量表 — for MFU / memory bandwidth utilization 派生指标。

数据源：NVIDIA 官方白皮书。值是 BF16 dense（无 sparsity），LLM serving 的实际理论上限。
匹配策略：列表顺序 = 优先级，更具体的 pattern 写前面（H100 PCIe → H100 → ...）。
NVML 返回名形如 "NVIDIA H100 80GB HBM3"、"NVIDIA A100-SXM4-80GB" 等。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GPUPeak:
    bf16_tflops: float
    mem_bw_gbs: float


# (substring pattern, peak)
# 列表顺序很重要：更具体的在前。lookup 用大小写不敏感子串匹配。
_GPU_PEAK_TABLE: list[tuple[str, GPUPeak]] = [
    # Blackwell (BF16 dense; FP8 doubles these on 5th-gen Tensor Cores)
    ("B200",       GPUPeak(2250.0, 8000.0)),   # HBM3e 192GB, 8 TB/s
    ("B100",       GPUPeak(1800.0, 8000.0)),   # HBM3e 192GB, 8 TB/s
    # Hopper
    ("H200",       GPUPeak(989.0, 4800.0)),
    ("H100 PCIe",  GPUPeak(756.0, 2000.0)),
    ("H100 NVL",   GPUPeak(835.0, 3900.0)),
    ("H100",       GPUPeak(989.0, 3350.0)),    # 默认 SXM
    ("A100-PCIE",  GPUPeak(312.0, 1935.0)),
    ("A100",       GPUPeak(312.0, 2039.0)),    # 默认 SXM4 80GB
    ("L40S",       GPUPeak(362.0, 864.0)),
    ("L40",        GPUPeak(181.0, 864.0)),
    ("L4",         GPUPeak(121.0, 300.0)),
    ("A30",        GPUPeak(165.0, 933.0)),
    ("A10G",       GPUPeak(125.0, 600.0)),
    ("A10",        GPUPeak(125.0, 600.0)),
    ("V100",       GPUPeak(125.0, 900.0)),
    ("T4",         GPUPeak(65.0, 320.0)),
    # Ada Lovelace consumer / mobile — laptop variants are dedicated entries
    # because NVML reports the literal "RTX 4090 Laptop GPU", and laptop dies
    # are roughly half the desktop in both FLOPS and bandwidth.
    # IMPORTANT: laptop pattern must precede the desktop pattern (substring match).
    ("RTX 4090 Laptop", GPUPeak(82.6, 576.0)),    # AD103 mobile
    ("RTX 4080 Laptop", GPUPeak(48.7, 432.0)),    # AD104 mobile
    ("RTX 4070 Laptop", GPUPeak(33.3, 256.0)),    # AD106 mobile
    ("RTX 4060 Laptop", GPUPeak(22.6, 256.0)),    # AD107 mobile
    ("RTX 4090",   GPUPeak(165.0, 1008.0)),       # AD102 desktop (existing)
    ("RTX 4080",   GPUPeak(97.4, 716.8)),         # AD103 desktop
    ("RTX 4070 Ti", GPUPeak(80.1, 504.2)),
    ("RTX 4070",   GPUPeak(58.0, 504.2)),
    ("RTX 4060 Ti", GPUPeak(44.0, 288.0)),
    ("RTX 4060",   GPUPeak(31.3, 272.0)),
    # Blackwell 桌面(bf16 dense ≈ 官方 AI TOPS / 8,与上面 Ada 同口径估算)
    ("RTX 5090",   GPUPeak(209.5, 1792.0)),       # GB202, 512-bit GDDR7
    ("RTX 5080",   GPUPeak(112.0, 960.0)),        # GB203, 256-bit GDDR7
    ("RTX 5070 Ti", GPUPeak(87.9, 896.0)),        # GB203, 16GB GDDR7
    ("RTX 5070",   GPUPeak(123.0, 672.0)),        # GB205
    ("RTX 5060 Ti", GPUPeak(94.9, 448.0)),        # GB206, 759 AI TOPS, 16GB GDDR7 128-bit 448GB/s
    ("RTX 5060",   GPUPeak(76.5, 448.0)),         # GB206
    ("RTX 3090",   GPUPeak(71.0, 936.0)),
]


def lookup_peak(gpu_name: str) -> GPUPeak | None:
    """按 NVML 返回的设备名查 peak 性能。

    Returns:
        GPUPeak if matched, else None. 调用方应跳过 MFU 计算并打 warning。
    """
    name_upper = gpu_name.upper()
    for pattern, peak in _GPU_PEAK_TABLE:
        if pattern.upper() in name_upper:
            return peak
    return None


def known_gpu_patterns() -> list[str]:
    """返回所有已知的 GPU pattern，主要给文档/help 用。"""
    return [p for p, _ in _GPU_PEAK_TABLE]


# bf16 dense Tensor FLOPs / SM / clock，按 **计算能力**(major, minor)。
# 这是架构常量 —— 没有任何运行时 API 能读到 Tensor Core 吞吐,NVIDIA 不暴露。
# 但配合现读的 SM 数 × SM 时钟,同一架构的任何卡都能算对;**出新架构在此补一行**。
# 数值由各代旗舰的官方 dense bf16 TFLOPS 反推(如 4090=165→512, H100=989→4096)。
_BF16_FLOPS_PER_SM_CLK: dict[tuple[int, int], int] = {
    (7, 0): 1024,   # Volta   (V100)
    (7, 5): 512,    # Turing  (T4 / RTX20)
    (8, 0): 2048,   # Ampere  数据中心 (A100)
    (8, 6): 512,    # Ampere  消费级 (RTX30)
    (8, 7): 512,
    (8, 9): 512,    # Ada     (RTX40 / L40 / L4)
    (9, 0): 4096,   # Hopper  (H100 / H200)
    (10, 0): 4096,  # Blackwell 数据中心 (B100/B200,估)
    (12, 0): 1024,  # Blackwell 消费级 (RTX50)
}

# cudaDeviceGetAttribute 的 enum 值(driver_types.h,跨 CUDA 版本稳定)
_CU_ATTR_CLOCK_RATE = 13        # SM 时钟峰值 kHz
_CU_ATTR_MP_COUNT = 16          # SM 数
_CU_ATTR_MEM_CLOCK = 36         # 显存时钟峰值 kHz
_CU_ATTR_BUS_WIDTH = 37         # 显存位宽 bit
_CU_ATTR_CC_MAJOR = 75
_CU_ATTR_CC_MINOR = 76


def _load_cudart():  # noqa: ANN202
    import ctypes
    import glob
    for pat in (
        "/usr/local/lib/python3*/dist-packages/nvidia/cuda_runtime/lib/libcudart.so*",
        "/usr/local/cuda*/lib64/libcudart.so*",
        "/usr/lib/x86_64-linux-gnu/libcudart.so*",
    ):
        for p in sorted(glob.glob(pat)):
            try:
                return ctypes.CDLL(p)
            except OSError:
                continue
    return ctypes.CDLL("libcudart.so")  # 兜底:靠 ld 找


def read_gpu_peak(device: int = 0) -> GPUPeak | None:
    """从 CUDA 设备属性**现读现算**峰值(不查型号表):

      Memory roof = 2 × 显存时钟 × 位宽/8   —— 全读出来。GDDR5/6/HBM 准;
                    GDDR6X(PAM4)/GDDR7(PAM3)的 ×2 是近似,可能略偏低。
      Compute roof = SM 数 × SM 时钟 × bf16-Tensor-FLOPs/SM/clk(按计算能力查常量)
                     —— SM 数/时钟现读,Tensor 吞吐无 API 故用 per-架构常量。

    任意一步失败(无 cudart / 未知计算能力 / 读到 0)→ None,调用方回退型号表。
    """
    try:
        import ctypes
        cudart = _load_cudart()

        def attr(a: int) -> int:
            v = ctypes.c_int()
            rc = cudart.cudaDeviceGetAttribute(ctypes.byref(v), ctypes.c_int(a), ctypes.c_int(device))
            if rc != 0:
                raise RuntimeError(f"cudaDeviceGetAttribute({a}) rc={rc}")
            return v.value

        sm_clock_khz = attr(_CU_ATTR_CLOCK_RATE)
        mp_count = attr(_CU_ATTR_MP_COUNT)
        mem_clock_khz = attr(_CU_ATTR_MEM_CLOCK)
        bus_width = attr(_CU_ATTR_BUS_WIDTH)
        cc = (attr(_CU_ATTR_CC_MAJOR), attr(_CU_ATTR_CC_MINOR))
        fpc = _BF16_FLOPS_PER_SM_CLK.get(cc)
        if not fpc or min(sm_clock_khz, mp_count, mem_clock_khz, bus_width) <= 0:
            return None
        compute_tflops = mp_count * (sm_clock_khz * 1e3) * fpc / 1e12
        mem_bw_gbs = 2.0 * (mem_clock_khz * 1e3) * (bus_width / 8) / 1e9
        return GPUPeak(bf16_tflops=round(compute_tflops, 1), mem_bw_gbs=round(mem_bw_gbs, 1))
    except Exception:
        return None
