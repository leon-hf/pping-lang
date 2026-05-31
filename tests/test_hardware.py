"""GPU peak 表测试 — 验证常见 NVML 设备名能匹配上。"""
from __future__ import annotations

import pytest

from pping_lang.hardware import GPUPeak, known_gpu_patterns, lookup_peak


@pytest.mark.parametrize(
    "nvml_name,expected_tflops",
    [
        ("NVIDIA B200", 2250.0),                 # Blackwell flagship
        ("NVIDIA B100", 1800.0),
        ("NVIDIA H100 80GB HBM3", 989.0),       # SXM 默认
        ("NVIDIA H100 PCIe", 756.0),
        ("NVIDIA H100 NVL", 835.0),
        ("NVIDIA H200", 989.0),
        ("NVIDIA A100-SXM4-80GB", 312.0),
        ("NVIDIA A100-PCIE-40GB", 312.0),
        ("NVIDIA L40S", 362.0),
        ("NVIDIA L40", 181.0),
        ("NVIDIA L4", 121.0),
        ("NVIDIA A10G", 125.0),
        ("Tesla T4", 65.0),
        ("Tesla V100-SXM2-32GB", 125.0),
        ("NVIDIA GeForce RTX 4090", 165.0),
        ("NVIDIA GeForce RTX 4090 Laptop GPU", 82.6),
        ("NVIDIA GeForce RTX 4080", 97.4),
        ("NVIDIA GeForce RTX 4080 Laptop GPU", 48.7),
        ("NVIDIA GeForce RTX 4070 Ti", 80.1),
        ("NVIDIA GeForce RTX 4070", 58.0),
        ("NVIDIA GeForce RTX 4070 Laptop GPU", 33.3),  # 本次 WSL 实测命中
        ("NVIDIA GeForce RTX 4060", 31.3),
        ("NVIDIA GeForce RTX 3090", 71.0),
    ],
)
def test_lookup_known_gpus(nvml_name: str, expected_tflops: float):
    peak = lookup_peak(nvml_name)
    assert peak is not None, f"未识别 {nvml_name}"
    assert peak.bf16_tflops == expected_tflops


def test_lookup_unknown_returns_none():
    assert lookup_peak("Some Future GPU 2030") is None
    assert lookup_peak("") is None


def test_specific_pattern_wins_over_general():
    """H100 PCIe 应匹配 PCIe 条目而不是默认 SXM。"""
    pcie = lookup_peak("NVIDIA H100 PCIe")
    sxm = lookup_peak("NVIDIA H100 80GB HBM3")
    assert pcie is not None and sxm is not None
    assert pcie.bf16_tflops == 756.0
    assert sxm.bf16_tflops == 989.0
    assert pcie != sxm


def test_lookup_case_insensitive():
    assert lookup_peak("nvidia h100 80gb hbm3") is not None
    assert lookup_peak("NVIDIA H100 80GB HBM3") is not None


def test_gpu_peak_immutable():
    peak = GPUPeak(312.0, 2039.0)
    with pytest.raises((AttributeError, Exception)):
        peak.bf16_tflops = 999.0  # type: ignore[misc]


def test_known_patterns_returned():
    patterns = known_gpu_patterns()
    assert "H100" in patterns
    assert "A100" in patterns
    assert len(patterns) >= 10
