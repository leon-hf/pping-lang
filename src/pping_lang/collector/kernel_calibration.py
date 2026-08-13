"""#4 离线标定表加载:ncu 一次性标定(kernel_calibration.json)→ 在线查表。

标定表由 `scripts/calibrate_l2/`(bench_decode.py + parse_ncu_raw.py)在 GPU
空闲维护窗口生成,按 **kernel 家族**(roofline_est.classify_family)键控——
标定侧 eager、生产侧 CUDA graph,vLLM 给同投影选出的 marlin 模板参数会变
(实测 Li4/Li16 vs Li1/Li8),精确 mangled 名 join 会 miss;流式权重 kernel 的
L2/DRAM 行为对 tile 参数不敏感,家族级足够。
文件缺失/损坏/schema 不符都 fail-closed(返回 None),面板退回 "—"。
"""
from __future__ import annotations

import json
import os
from typing import Any

from pping_lang.collector.roofline_est import classify_family

DEFAULT_PATH = "/models/pping/kernel_calibration.json"


def calibration_path() -> str:
    return os.environ.get("PPING_LANG_KERNEL_CALIBRATION") or DEFAULT_PATH


def load_calibration(path: str | None = None) -> dict[str, Any] | None:
    """读标定表。任何一步不对(没有文件/坏 JSON/schema 不符/空表)→ None。"""
    p = path or calibration_path()
    try:
        with open(p) as f:
            d = json.load(f)
        if d.get("schema") != 1 or not isinstance(d.get("kernels"), dict) or not d["kernels"]:
            return None
        return d
    except Exception:
        return None


def lookup(calibration: dict[str, Any] | None, kernel_name: str) -> dict[str, Any] | None:
    """按家族查一行(标定表 key_by=family:eager 标定与 graph 生产的模板名不同,
    家族级才是稳定键)。命中返回 {l2_hit_pct, dram_gbps, ...},否则 None。

    lm_head 特例:它的载体在生产窗里 gemv/cutlass 两种都出现过(vLLM 按批状态选),
    而标定只采到 gemv —— 量化部署(marlin 在表)下 cutlass 行回退用 gemv 的标定值
    (同一个 lm_head,角色唯一,与 roofline_est 的 cutlass_lm_head 判定同一条启发式)。
    """
    if not calibration:
        return None
    fam = classify_family(kernel_name)
    if fam is None:
        return None
    kernels = calibration.get("kernels") or {}
    k = kernels.get(fam)
    if isinstance(k, dict):
        return k
    if fam == "cutlass-gemm" and "marlin-int4" in kernels:
        fallback = kernels.get("cublas-gemv")
        if isinstance(fallback, dict):
            return fallback
    return None
