"""P3 行级归因：把 PC 样本的 (cubinCrc, pcOffset, functionName) 关联到源码行 / SASS 偏移。

双轨(M0 实测划清的边界,见 _design-notes/phase-3-行级归因-M0铁证与价值边界.md):
  - 可映射 kernel(Triton/自编译,带 lineinfo)→ `file.py:line`(wow 那一轨);
  - 闭源库(cutlass/flash,无 lineinfo)→ 退到 SASS 偏移 + 解码 kernel 名(tile/dtype)。

两个 CUPTI API 都在主 libcupti.so.13,离线可调(无需 CUDA context):
  - cuptiGetCubinCrc(cubin 字节)→ crc：用来匹配 PC 样本里的 cubinCrc(运行时 crc 与磁盘
    cubin 的 cuptiGetCubinCrc 实测相等,M0 已证);
  - cuptiGetSassToSourceCorrelation(cubin, fn, pcOffset)→ lineNumber/fileName。

全程 fail-soft:libcupti 加载不了 / 无 cubin / 关联失败 → 返回 None,绝不抛断采集。
"""
from __future__ import annotations

import ctypes
import glob
import logging
import os
import re
from typing import Any

logger = logging.getLogger("pping_lang.sass_source")


# ---- ctypes 镜像 cupti_pcsampling.h ----
class _GetCubinCrcParams(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("cubinSize", ctypes.c_size_t),
        ("cubin", ctypes.c_void_p),
        ("cubinCrc", ctypes.c_uint64),
    ]


class _SassToSourceParams(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_size_t),
        ("cubin", ctypes.c_void_p),
        ("functionName", ctypes.c_char_p),
        ("cubinSize", ctypes.c_size_t),
        ("lineNumber", ctypes.c_uint32),
        ("pcOffset", ctypes.c_uint64),
        ("fileName", ctypes.c_char_p),
        ("dirName", ctypes.c_char_p),
    ]


def _find_libcupti() -> str | None:
    for pat in (
        "/usr/local/lib/python3*/dist-packages/nvidia/*/lib/libcupti.so.1*",
        "/usr/local/cuda*/lib64/libcupti.so.1*",
        "/usr/local/cuda*/targets/*/lib/libcupti.so.1*",
    ):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def _triton_cache_globs() -> list[str]:
    home = os.environ.get("TRITON_CACHE_DIR")
    pats = []
    if home:
        pats.append(os.path.join(home, "**", "*.cubin"))
    pats += [
        os.path.expanduser("~/.triton/cache/**/*.cubin"),
        "/root/.triton/cache/**/*.cubin",
    ]
    return pats


# kernel 名解码：闭源 GEMM/attention 名里编码了 tile/dtype/指令类,本身就有诊断价值。
_CUTLASS_RE = re.compile(r"cutlass_\w*?(?P<arch>\d+)_(?P<inst>wmma_tensorop|tensorop|simt)"
                         r"_(?P<dtype>[a-z0-9]+)_\w*?(?P<tile>\d+x\d+)_(?P<stages>\d+x\d+)")


def decode_kernel_name(name: str) -> str | None:
    """把 mangled kernel 名解码成人话(库来源 / tile / dtype)。认不出返回 None。

    注意区分：`_ZN...` 是 C++ Itanium mangled(cutlass/cuBLAS/PyTorch/vLLM 自定义 CUDA);
    Triton JIT kernel 是未 mangle 的普通名(如 `_topk_topp_kernel`)。早期把所有下划线开头
    当 Triton 是错的(M1 实测踩到)。
    """
    if not name:
        return None
    low = name.lower()
    mangled = name.startswith("_Z")
    m = _CUTLASS_RE.search(low)
    if m:
        return (f"cutlass GEMM · {m['dtype']} · {m['inst'].replace('_', ' ')} · "
                f"tile {m['tile']} · SM{m['arch']}")
    if "flash_fwd_splitkv" in low:
        return "FlashAttention · split-KV decode kernel"
    if "flash_fwd" in low:
        return "FlashAttention · forward kernel"
    if "cutlass" in low and "gemm" in low:
        return "cutlass GEMM kernel"
    # 常见 C++ kernel(_ZN mangled)：按内嵌库标识归类
    if mangled:
        if "cublaslt" in low or "cublas" in low:
            if "splitkreduce" in low:
                return "cuBLAS · split-K reduce"
            return "cuBLAS GEMM kernel"
        if "fused_add_rms_norm" in low:
            return "vLLM 自定义 · fused add + RMSNorm"
        if "rms_norm" in low:
            return "vLLM 自定义 · RMSNorm"
        if "rotary" in low or "rope" in low:
            return "vLLM 自定义 · RoPE"
        if "silu" in low or "act_and_mul" in low:
            return "vLLM 自定义 · SiLU/激活"
        if low.find("3c10") >= 0 and "vllm" in low:
            return "vLLM 自定义 CUDA kernel"
        if "flashinfer" in low:
            if "sampling" in low or "topk" in low or "topp" in low:
                return "FlashInfer · 采样 kernel"
            return "FlashInfer kernel"
        if "gemvx" in low or "gemv" in low:
            return "cuBLAS GEMV(矩阵×向量,小 batch 典型)"
        if "at6native" in low or "at::native" in low:
            return "PyTorch · 原生 elementwise/reduce kernel"
        if "elementwise" in low:
            return "elementwise kernel"
        return "C++ 编译 kernel(闭源/无 lineinfo)"
    # 未 mangle 的普通名 + 像 kernel → Triton JIT
    if "kernel" in low:
        return "Triton JIT kernel"
    return None


class SourceCorrelator:
    """PC → 源码行 / SASS 偏移 关联器。构造一次复用;crc→cubin 与关联结果都带缓存。

    fail-soft：任何一步出问题(无 libcupti / 无 cubin / API 失败)→ correlate 返回降级结果。
    """

    def __init__(self, libcupti_path: str | None = None) -> None:
        self._lib: Any = None
        self._crc_to_cubin: dict[int, bytes] | None = None
        self._line_cache: dict[tuple[int, int, str], tuple[int, str] | None] = {}
        self._file_cache: dict[str, list[str]] = {}  # 源码文件 → 行列表(读一次缓存)
        path = libcupti_path or _find_libcupti()
        if not path:
            logger.info("[pping-lang] sass_source：未找到 libcupti,源码行关联禁用(SASS 偏移仍可用)")
            return
        try:
            lib = ctypes.CDLL(path)
            lib.cuptiGetCubinCrc.argtypes = [ctypes.POINTER(_GetCubinCrcParams)]
            lib.cuptiGetCubinCrc.restype = ctypes.c_int
            lib.cuptiGetSassToSourceCorrelation.argtypes = [ctypes.POINTER(_SassToSourceParams)]
            lib.cuptiGetSassToSourceCorrelation.restype = ctypes.c_int
            self._lib = lib
        except Exception as e:  # noqa: BLE001
            logger.info("[pping-lang] sass_source:libcupti 绑定失败 %s,源码行关联禁用", e)

    @property
    def available(self) -> bool:
        return self._lib is not None

    def _cubin_crc(self, blob: bytes) -> int | None:
        p = _GetCubinCrcParams()
        p.size = ctypes.sizeof(_GetCubinCrcParams)
        p.cubinSize = len(blob)
        p.cubin = ctypes.cast(ctypes.c_char_p(blob), ctypes.c_void_p)
        try:
            if self._lib.cuptiGetCubinCrc(ctypes.byref(p)) == 0:
                return int(p.cubinCrc)
        except Exception:  # noqa: BLE001
            return None
        return None

    def _ensure_crc_map(self) -> dict[int, bytes]:
        """扫 triton cache 建 crc→cubin 字节表(惰性,只建一次)。"""
        if self._crc_to_cubin is not None:
            return self._crc_to_cubin
        m: dict[int, bytes] = {}
        if self._lib is not None:
            seen: set[str] = set()
            for pat in _triton_cache_globs():
                for cb in glob.glob(pat, recursive=True):
                    if cb in seen:
                        continue
                    seen.add(cb)
                    try:
                        with open(cb, "rb") as _f:
                            blob = _f.read()
                    except Exception:  # noqa: BLE001
                        continue
                    crc = self._cubin_crc(blob)
                    if crc is not None:
                        m[crc] = blob
        self._crc_to_cubin = m
        logger.info("[pping-lang] sass_source:crc→cubin 表建好,%d 个 triton cubin", len(m))
        return m

    def refresh(self) -> None:
        """triton 可能在运行中 JIT 新 kernel → 让下次关联重扫 cache。"""
        self._crc_to_cubin = None
        self._line_cache.clear()

    def correlate(self, crc: int, offset: int, fn: str) -> tuple[int, str] | None:
        """(crc, pcOffset, functionName) → (lineNumber, fileName);无 lineinfo/未匹配返回 None。"""
        if self._lib is None:
            return None
        key = (crc, offset, fn)
        if key in self._line_cache:
            return self._line_cache[key]
        blob = self._ensure_crc_map().get(crc)
        res: tuple[int, str] | None = None
        if blob is not None:
            p = _SassToSourceParams()
            p.size = ctypes.sizeof(_SassToSourceParams)
            p.cubin = ctypes.cast(ctypes.c_char_p(blob), ctypes.c_void_p)
            p.functionName = fn.encode()
            p.cubinSize = len(blob)
            p.pcOffset = offset
            try:
                if self._lib.cuptiGetSassToSourceCorrelation(ctypes.byref(p)) == 0 and p.lineNumber:
                    fname = p.fileName.decode() if p.fileName else "?"
                    dname = p.dirName.decode() if p.dirName else ""
                    # CUPTI 把目录(dirName)和文件名(fileName)分开返回 —— 拼成全路径才能读到
                    # 源码原文(os.path.join 对 fname 已是绝对路径的情况也安全)
                    full = os.path.join(dname, fname) if dname else fname
                    res = (int(p.lineNumber), full)
            except Exception:  # noqa: BLE001
                res = None
        self._line_cache[key] = res
        return res

    def source_line(self, path: str, line: int) -> str | None:
        """读取源码文件第 line 行原文(带缓存)。文件在引擎机磁盘上(triton/vLLM 安装的 .py);
        读不到 / 越界返回 None。"""
        if not path or line <= 0:
            return None
        lines = self._file_cache.get(path)
        if lines is None:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            except Exception:  # noqa: BLE001
                lines = []
            self._file_cache[path] = lines
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()
        return None

    def hotspot(self, crc: int, offset: int, fn: str) -> dict[str, Any]:
        """单个热点 PC → 双轨结果(源码行 / SASS 偏移 + kernel 名解码)。"""
        src = self.correlate(crc, offset, fn)
        out: dict[str, Any] = {
            "sass_offset": f"0x{offset:x}",
            "kernel_decode": decode_kernel_name(fn),
        }
        if src:
            line, path = src
            out["source"] = f"{os.path.basename(path)}:{line}"
            out["source_path"] = path
            out["source_line"] = line
        else:
            out["source"] = None
        return out
