#!/usr/bin/env python3
"""把 ncu raw CSV 解析成 pping 在线查表用的 kernel_calibration.json。

输入:`ncu --csv --page raw` 的逐 launch 记录(可多个文件,每文件一个家族
—— ncu 的 --launch-count 是全局计数,多家族一起采会被先发的 marlin 挤掉)。
输出按 **kernel 家族**(marlin-int4 / cublas-gemv / cutlass-gemm / flash-attn,
复用 roofline_est.classify_family)聚合。

为什么按家族而不是按 mangled 名:标定侧 eager、生产侧 CUDA graph,vLLM 给同
投影选出的 marlin 模板参数会变(实测 Li4/Li16 vs Li1/Li8),精确名 join 会
miss;流式权重 kernel 的 L2/DRAM 行为对 tile 参数不敏感,家族级足够。

ncu raw CSV 的坑(实测 2025.4.1):
- 文件开头可能混有 "==PROF==" 行 → 找首个字段为 ID 的行当表头
- 表头下一行是单位行(ID 空)→ 跳过
- 数值带千分位逗号;gpu__time_duration.sum 单位 ns(单位行确认)

聚合口径:一个家族/模板聚合多个投影(marlin 盖 qkv/o/gate_up/down,逐 launch
字节差 8 倍),所以 dram_gbps = Σbytes/Σduration、l2_hit 按字节加权 ——
恰好对应线上 kernel_table"同名一行"的语义。

用法:
    python3 scripts/calibrate_l2/parse_ncu_raw.py calib_marlin.csv calib_lmhead.csv ... \
        --gpu "NVIDIA GeForce RTX 5060 Ti" --model Qwen/Qwen2.5-7B-Instruct-AWQ \
        -o kernel_calibration.json
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import defaultdict


# 家族分类:与 src/pping_lang/collector/roofline_est.py 的 classify_family 保持同步
# (含前缀判定的原因:triton 融合 kernel 名里带 marlin/gemv 字样,子串会误归)。
# 这里故意内联而不 import —— 本脚本在 runw 宿主机裸 python3 下跑,import pping_lang
# 会连带拉起 fastapi 等依赖(实测 ModuleNotFoundError),ops 脚本必须零依赖。
def classify_family(kernel_name: str) -> str | None:
    n = kernel_name or ""
    low = n.lower()
    if n.startswith("_ZN6marlin"):
        return "marlin-int4"
    if "gemvx" in low:
        return "cublas-gemv"
    if n.startswith("_ZN5flash") or "fmha" in low:
        return "flash-attn"
    if n.startswith("_ZN7cutlass"):
        return "cutlass-gemm"
    return None


def _num(s: str | None) -> float | None:
    """ncu CSV 数值:可能带千分位逗号/百分号。取不出 → None。"""
    if s is None:
        return None
    t = s.strip().strip('"').replace(",", "").removesuffix("%")
    try:
        return float(t)
    except ValueError:
        return None


def _rows(path: str):
    """产出 (kernel_name, dram_bytes, duration_ns, l2_pct),跳过 PROF 行/单位行/坏行。"""
    with open(path, newline="") as f:
        rdr = csv.reader(f)
        header = None
        for row in rdr:
            if not row:
                continue
            if header is None:
                if row[0].strip().strip('"') == "ID":
                    header = [c.strip() for c in row]
                continue
            rec = dict(zip(header, row, strict=False))
            rid = (rec.get("ID") or "").strip().strip('"')
            if not rid.isdigit():
                continue  # 单位行 / 空行
            name = (rec.get("Kernel Name") or "").strip()
            b = dur = l2 = None
            for k, v in rec.items():
                kl = k.strip().lower()
                if kl.startswith("dram__bytes.sum"):
                    b = _num(v)
                elif kl.startswith("gpu__time_duration.sum"):
                    dur = _num(v)
                elif kl.startswith("lts__t_sector_hit_rate.pct"):
                    l2 = _num(v)
            if name and b is not None and dur is not None and dur > 0:
                yield name, b, dur, l2 or 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_paths", nargs="+")
    ap.add_argument("--gpu", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    agg: dict[str, dict[str, float]] = defaultdict(
        lambda: {"bytes": 0.0, "ns": 0.0, "l2w": 0.0, "n": 0.0})
    examples: dict[str, str] = {}
    total = skipped_fam = 0
    for path in args.csv_paths:
        for name, b, dur, l2 in _rows(path):
            total += 1
            fam = classify_family(name)
            if fam is None:
                skipped_fam += 1
                continue
            a = agg[fam]
            a["bytes"] += b
            a["ns"] += dur
            a["l2w"] += l2 * b
            a["n"] += 1
            examples.setdefault(fam, name)

    kernels = {}
    for fam, a in sorted(agg.items(), key=lambda kv: -kv[1]["bytes"]):
        kernels[fam] = {
            "l2_hit_pct": round(a["l2w"] / a["bytes"], 2) if a["bytes"] else None,
            "dram_gbps": round(a["bytes"] / a["ns"], 1),   # bytes/ns == GB/s
            "duration_us": round(a["ns"] / 1000.0 / a["n"], 1),
            "launches": int(a["n"]),
            "example_kernel": examples.get(fam),
        }

    out = {
        "schema": 1,
        "key_by": "family",
        "gpu": args.gpu,
        "model": args.model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "metric_source": "ncu --page raw (lts__t_sector_hit_rate.pct, dram__bytes.sum, gpu__time_duration.sum)",
        "rows_total": total,
        "rows_unclassified": skipped_fam,
        "kernels": kernels,
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"[calib] {len(kernels)} families -> {args.out} "
          f"({total} rows, {skipped_fam} unclassified)")
    for fam, k in kernels.items():
        print(f"  {fam:<13} {k['dram_gbps']:>8.1f} GB/s  L2 {k['l2_hit_pct']}%  n={k['launches']}")


if __name__ == "__main__":
    main()
