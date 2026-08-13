"""#4 ncu 离线标定表:加载 / 查表 / deep_profile 行填充 / raw CSV 解析。

标定表 key_by=family(不是 mangled 名):eager 标定与 graph 生产的 marlin 模板
参数不同,精确名 join 会 miss —— 这是 runw 实测撞出来的,不是预防性设计。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pping_lang.collector.deep_profile import build_deep_profile
from pping_lang.collector.kernel_calibration import load_calibration, lookup
from pping_lang.hardware import SMLimits

LIM = SMLimits(mp_count=36, max_threads_per_sm=1536, max_regs_per_sm=65536,
               max_smem_per_sm=102400, max_blocks_per_sm=32)

MARLIN_K = "_ZN6marlin6MarlinILl1125899906910725ELl1125899906843648EEEvPK4int4"
FLASH_K = "_ZN5flash24flash_fwd_splitkv_kernelI23Flash_fwd_kernel_traitsXX"

CALIB = {
    "schema": 1,
    "key_by": "family",
    "gpu": "NVIDIA GeForce RTX 5060 Ti",
    "model": "Qwen/Qwen2.5-7B-Instruct-AWQ",
    "created_at": "2026-08-08T00:00:00",
    "metric_source": "ncu --page raw",
    "kernels": {
        "marlin-int4": {"l2_hit_pct": 8.4, "dram_gbps": 123.4,
                        "duration_us": 850.0, "launches": 30,
                        "example_kernel": MARLIN_K},
    },
}


def _result(kernel=MARLIN_K):
    return {"available": True, "kernel_table": [{
        "kernel": kernel, "cls": "other", "time_pct": 71.5,
        "launch_cfg": {"launches": 0, "grid": [36, 1, 1], "block": [256, 1, 1],
                       "dyn_smem": 101376, "regs": 64, "static_smem": 0},
    }]}


def test_load_and_lookup(tmp_path: Path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(CALIB))
    d = load_calibration(str(p))
    assert d is not None and d["gpu"].startswith("NVIDIA")
    hit = lookup(d, MARLIN_K)          # 任意 marlin mangled 名 → 家族命中
    assert hit["l2_hit_pct"] == 8.4 and hit["dram_gbps"] == 123.4
    assert lookup(d, "_ZN6marlin6MarlinILl999EDifferentTemplateEE") is not None
    assert lookup(d, FLASH_K) is None          # 家族不在表里 → None
    assert lookup(d, "_ZN2at6native29vectorized_elementwiseXX") is None  # 认不出 → None


def test_cutlass_falls_back_to_gemv_entry():
    """lm_head 载体漂移:生产窗里 cutlass 充当 lm_head 时,回退用 gemv 的标定值。"""
    calib = dict(CALIB, kernels={**CALIB["kernels"],
                                 "cublas-gemv": {"l2_hit_pct": 0.4, "dram_gbps": 414.8,
                                                 "duration_us": 2600.0, "launches": 40}})
    hit = lookup(calib, "_ZN7cutlass7Kernel2I64cutlass_80_tensorop_f16_XX")
    assert hit is not None and hit["dram_gbps"] == 414.8
    # 非量化部署(表里没有 marlin)→ 不做这个回退(cutlass 是层 GEMM,不是 lm_head)
    calib_no_marlin = dict(calib, kernels={"cublas-gemv": calib["kernels"]["cublas-gemv"]})
    assert lookup(calib_no_marlin, "_ZN7cutlass7Kernel2I64cutlass_80_tensorop_f16_XX") is None


def test_load_fail_closed(tmp_path: Path):
    assert load_calibration(str(tmp_path / "missing.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_calibration(str(bad)) is None
    wrong_schema = tmp_path / "s.json"
    wrong_schema.write_text(json.dumps({"schema": 99, "kernels": {"k": {}}}))
    assert load_calibration(str(wrong_schema)) is None
    empty = tmp_path / "e.json"
    empty.write_text(json.dumps({"schema": 1, "kernels": {}}))
    assert load_calibration(str(empty)) is None


def test_row_filled_from_calibration():
    r = build_deep_profile(_result(), LIM, calibration=CALIB)
    k = r["kernels"][0]
    assert k["l2_hit_pct"] == 8.4 and k["dram_gbps"] == 123.4
    assert k["metrics_source"] == "calibrated"


def test_row_without_calibration_unchanged():
    r = build_deep_profile(_result(), LIM)
    k = r["kernels"][0]
    assert k["l2_hit_pct"] is None and k["dram_gbps"] is None
    assert k["metrics_source"] is None
    # 有标定表但该行家族不在表里 → 同样 None
    r2 = build_deep_profile(_result(kernel=FLASH_K), LIM, calibration=CALIB)
    k2 = r2["kernels"][0]
    assert k2["l2_hit_pct"] is None and k2["metrics_source"] is None


def test_parse_ncu_raw(tmp_path: Path):
    """解析器:容忍 ==PROF== 头 + 单位行;按家族聚合,字节加权。"""
    csv_path = tmp_path / "raw.csv"
    # 两行同家族 marlin:bytes 差 8 倍(qkv vs gate_up 投影),验证字节加权而非简单平均
    csv_path.write_text(
        '==PROF== Connected to process 148\n'
        '"ID","Kernel Name","dram__bytes.sum","gpu__time_duration.sum","lts__t_sector_hit_rate.pct"\n'
        '"","","byte","ns","%"\n'
        f'"0","{MARLIN_K}","1,000,000","1,000","10.0"\n'
        f'"1","{MARLIN_K}","8,000,000","4,000","20.0"\n'
        f'"2","{FLASH_K}","2,000,000","2,000","50.0"\n'
        '"3","_ZN2at6native29vectorized_elementwiseXX","5,000,000","1,000","1.0"\n'
    )
    out = tmp_path / "out.json"
    subprocess.run(
        [sys.executable, "scripts/calibrate_l2/parse_ncu_raw.py", str(csv_path),
         "--gpu", "G", "--model", "M", "-o", str(out)],
        check=True, capture_output=True, text=True)
    d = json.loads(out.read_text())
    m = d["kernels"]["marlin-int4"]
    # bytes/ns == GB/s:9e6 B / 5000 ns = 1800 GB/s;L2 = (10×1 + 20×8)/9 ≈ 18.9%
    assert m["dram_gbps"] == 1800.0
    assert abs(m["l2_hit_pct"] - 18.9) < 0.1
    assert m["launches"] == 2
    assert d["kernels"]["flash-attn"]["l2_hit_pct"] == 50.0
    assert "elementwise" not in json.dumps(d["kernels"])   # 认不出的家族不进表
    assert d["rows_unclassified"] == 1
