"""EngineCore 侧 PC sampling 驱动 + 跨进程回流(① 多进程 serve 方案)。

背景:`vllm serve` 是 **2 进程**(前端 APIServer + EngineCore)。GPU kernel 在
EngineCore 里跑,注入的 .so 也在那个进程抢到 CUPTI subscriber;但 stat_logger
插件(含 dashboard)在前端进程、没有 CUDA context,无法驱动 PC sampling。

桥接方案:
  - **EngineCore 侧**:通过 `vllm.general_plugins` 入口点(vLLM 在 EngineCore.__init__
    里 `load_general_plugins()` 调用每个入口点函数),起一个后台线程,等 CUDA 就绪后
    prime PC sampling,持续 drain,把每个窗口的 Deep-Evidence 结果**原子写入共享 JSON 文件**。
  - **前端**:`FilePcSampling`(实现与 PcSamplingController 同样的接口)只读那个共享文件,
    喂给 dashboard 的 Deep Evidence。两进程靠同一个文件路径(PPING_LANG_PCS_RESULT_FILE)对接。

幂等:general_plugin 会在多个进程被调用(前端也会),靠"等 CUDA 就绪"自然只在
EngineCore 真 prime;前端那次等不到 CUDA、超时退出。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger("pping_lang.engine_pcs")

DEFAULT_RESULT_FILE = "/tmp/pping-lang-pcs-result.json"

_started = False


def _result_file() -> str:
    return os.environ.get("PPING_LANG_PCS_RESULT_FILE", DEFAULT_RESULT_FILE)


def init_engine_pcs() -> None:
    """`vllm.general_plugins` 入口点。无参、幂等。

    PPING_LANG_ENABLE_PCS=1 时,起后台驱动线程(等 CUDA → prime → drain 循环 → 写文件)。
    会在 EngineCore / worker / 前端都被调一次;只有真有 CUDA 的进程(EngineCore)会成功 prime。
    """
    global _started
    if _started:
        return
    if os.environ.get("PPING_LANG_ENABLE_PCS") != "1":
        return
    _started = True
    t = threading.Thread(target=_driver_loop, name="pping-pcs-engine", daemon=True)
    t.start()
    logger.info("[pping-lang] PC sampling 引擎驱动线程已起(等 CUDA 就绪)")


def _driver_loop() -> None:
    result_file = _result_file()
    # ★ 默认 2^16(实测教训):2^12 在 decode 负载下 >2000 万样本/s,几秒打满 CUPTI 硬件
    # 缓冲 → 会话**永久楔死**(官方只给预防不给恢复)→ 之前误判为 "cudagraph 采不到"。
    # 2^16 下排水稳定跟得上(实测 cudagraph serve 120s 持续 ~1100 万样本/窗、hwfull=0)。
    period_log2 = int(os.environ.get("PPING_LANG_PCS_PERIOD_LOG2", "16"))
    window_s = float(os.environ.get("PPING_LANG_PCS_WINDOW_S", "5.0"))
    cuda_wait_s = float(os.environ.get("PPING_LANG_PCS_CUDA_WAIT_S", "120"))

    # 1) 等 CUDA 就绪(模型加载后);前端进程永远等不到 → 超时退出(无害)
    try:
        import torch  # noqa: PLC0415
    except Exception:
        return
    deadline = time.monotonic() + cuda_wait_s
    while time.monotonic() < deadline:
        if torch.cuda.is_available() and torch.cuda.is_initialized():
            break
        time.sleep(0.2)
    else:
        logger.info("[pping-lang] PCS 驱动:等不到 CUDA(大概率前端进程),退出")
        return

    # 2) 把 CUDA context 绑到本线程(prime 走驱动 API 查当前线程 context)
    try:
        torch.cuda.set_device(0)
        torch.zeros(1, device="cuda")
        torch.cuda.synchronize()
    except Exception as e:  # noqa: BLE001
        logger.warning("[pping-lang] PCS 驱动:绑定 CUDA context 失败 %s", e)
        return

    # 3) prime。
    #    (历史勘误:曾误判"cudagraph 图回放采不到样是底层限制" —— 真因是 2^12 周期下
    #    样本产率打满 CUPTI HW 缓冲、会话永久楔死,见上方 period_log2 注释。2^16 后
    #    cudagraph/eager 都持续工作,无需延迟 prime。延迟开关留作排查工具。)
    prime_delay_s = float(os.environ.get("PPING_LANG_PCS_PRIME_DELAY_S", "0"))
    if prime_delay_s > 0:
        logger.info("[pping-lang] PCS 驱动:延迟 %.0fs 再 prime", prime_delay_s)
        time.sleep(prime_delay_s)

    from pping_lang.collector.cupti import (  # noqa: PLC0415
        CtypesPcSamplingLib,
        PcSamplingController,
    )
    lib = CtypesPcSamplingLib()
    ctl = PcSamplingController(lib)
    prime = ctl.prime(period_log2)
    logger.info("[pping-lang] PCS 驱动 prime=%s available=%s", prime, lib.available())
    if not prime.get("available"):
        logger.warning("[pping-lang] PCS 驱动:prime 失败,Deep Evidence 不可用")
        return

    # 4) 持续 drain → 每窗原子写共享文件
    logger.info("[pping-lang] PCS 驱动:开始持续采样,结果写 %s", result_file)
    # 自愈:cudagraph capture 会把早 prime 的采样打停(实测:capture 后 drain 全空窗,
    # 但 capture 后重启采样即恢复)。检测"曾有样本→连续空窗"的打断特征,重启采样。
    # 纯空闲(从未有样本)不触发,避免无谓重启;重启有冷却,避免抖动。
    # ★ reprime 安全边界(py-spy 实锤,见设计文档 §cudagraph):
    #   - cudagraph capture 会把早 prime 的采样打哑(之后全空窗)→ 需要一次 reprime 救活;
    #   - 但 reprime 的 cuptiPCSamplingStop 若在引擎**带载推理中**执行,会与在飞的图回放
    #     在驱动层互等 → EngineCore 静默挂死(MainThread 卡 CUDA 调用,我们卡在 stop())。
    #   解:reprime 全程**最多 1 次**(正好落在 capture 后、流量前的空闲间隙,实测把采样
    #   救活后稳定工作);空窗阈值 2(防瞬时空窗误触发);用完配额后空窗只记不动。
    seen_samples = False
    dry_windows = 0
    last_reprime = 0.0
    reprime_count = 0
    window_count = 0
    reprime_cooldown_s = float(os.environ.get("PPING_LANG_PCS_REPRIME_COOLDOWN_S", "20"))
    dry_threshold = int(os.environ.get("PPING_LANG_PCS_REPRIME_DRY_WINDOWS", "2"))
    max_reprimes = int(os.environ.get("PPING_LANG_PCS_MAX_REPRIMES", "1"))
    status_file = result_file + ".status"
    while True:
        try:
            res = ctl.run_window(window_s=window_s, period_log2=period_log2)
            window_count += 1
            samples = (res.get("sample_total") or 0) if res.get("available") else 0
            ov = res.get("overhead") or {}
            did_reprime = False
            if samples > 0:
                seen_samples = True
                dry_windows = 0
                _atomic_write_json(result_file, res)  # 空窗不覆盖,保留最近真实采样
            else:
                dry_windows += 1
                now = time.monotonic()
                # 曾经有样本、现在连续空窗 → 疑似被 capture 打断,重启采样自愈。
                # ★ 最多 max_reprimes 次:带载中 stop 会与图回放在驱动层互等挂死引擎(见上)。
                if (seen_samples and dry_windows >= dry_threshold
                        and reprime_count < max_reprimes
                        and now - last_reprime >= reprime_cooldown_s):
                    logger.info("[pping-lang] PCS 驱动:采样枯竭(连续 %d 空窗),重启采样自愈"
                                "(%d/%d 次,疑似 cudagraph capture 打断)",
                                dry_windows, reprime_count + 1, max_reprimes)
                    rp = ctl.reprime(period_log2)
                    last_reprime = now
                    dry_windows = 0
                    reprime_count += 1
                    did_reprime = True
                    res.setdefault("_reprime", {})["result"] = rp
            # 心跳:每窗写状态(诊断 cudagraph 自愈),与主结果文件分开
            extra: dict[str, Any] = {}
            try:  # 诊断计数(老 .so 无符号则跳过)
                raw = getattr(ctl, "_lib", None)
                clib = getattr(raw, "_lib", None)
                if clib is not None and hasattr(clib, "pping_pcs_skipped_drains"):
                    extra["skipped"] = int(clib.pping_pcs_skipped_drains())
                    extra["module_cbs"] = int(clib.pping_pcs_module_cbs())
            except Exception:  # noqa: BLE001
                pass
            _atomic_write_json(status_file, {
                "window": window_count, "samples": samples, "seen": seen_samples,
                "dry": dry_windows, "reprimes": reprime_count, "did_reprime": did_reprime,
                "getdata_ms": ov.get("getdata_ms"), "dropped": ov.get("dropped"),
                "hwfull": ov.get("hwfull"), **extra,
                "available": res.get("available"), "err": res.get("error"),
            })
        except Exception:  # noqa: BLE001
            logger.exception("[pping-lang] PCS 驱动:窗口失败")
            time.sleep(2.0)


def _atomic_write_json(path: str, obj: Any) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)  # 原子替换,读端不会读到半截


class FilePcSampling:
    """前端用:不采样,只读 EngineCore 写的共享结果文件(跨进程回流)。

    实现 PcSamplingController 的鸭子接口(started / available / run_window /
    last_result / close),让 CuptiKernelCollector / dashboard 无感复用。
    """

    def __init__(self, result_file: str | None = None) -> None:
        self._file = result_file or _result_file()

    @property
    def available(self) -> bool:
        return os.path.exists(self._file)

    @property
    def started(self) -> bool:
        return os.path.exists(self._file)

    def last_result(self) -> dict[str, Any] | None:
        return self._read()

    def run_window(self, **_kwargs: Any) -> dict[str, Any]:
        # 不真跑窗,返回 EngineCore 写的最近一窗
        r = self._read()
        if r:
            return r
        return {
            "available": False,
            "error": "引擎侧 PC sampling 结果暂无(等首个窗口写入)",
        }

    def close(self) -> None:
        pass

    def _read(self) -> dict[str, Any] | None:
        try:
            with open(self._file) as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None
