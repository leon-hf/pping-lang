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
