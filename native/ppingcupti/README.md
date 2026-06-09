# libppingcupti — 阶段 2 PC Sampling 原生数据平面

把 CUPTI **连续 PC Sampling**(SASS/指令级 stall reason)采集进 pping-lang,补 zymtrace
那张 GPU 图的"下 1/3":不只是"哪类 kernel 慢",而是"**为什么**慢"(访存依赖 / 计算管线 /
同步 …)。藏在 Python 的 `KernelActivitySource` / `PcSamplingController` 边界后,经 C-ABI + ctypes 调用。

> 设计与踩坑全记录见 `_design-notes/phase-2-PC-Sampling-采集设计.md`(本仓 gitignore,本地)。

## 真机验证状态

| 场景 | 结果 |
|------|------|
| 干净 CUDA 容器 NVRTC kernel | ✅ 430 万样本 |
| vLLM 镜像 + torch GEMM | ✅ 8800 万样本 |
| **真 vLLM 推理(单次取证窗)** | ✅ **4112 万样本**,访存依赖 ~74% 主导,kernel 全归因(cutlass GEMM / flash attention / RoPE / RMSNorm / SwiGLU)|
| 容器 live dashboard(持续采样)| ✅ **已解(§11)**:500ms drain cadence;/api/kernels/deep_evidence 连打 6 次返真数据零崩;headless 90s / 9.3 亿样本稳 |

## 构建(Linux / WSL,需 cu13 CUPTI 头/库 + g++,**不需要 nvcc**)

```bash
make                      # 自动从 ~/pping-cupti 的 venv 找 cu13
make VENV=/path/to/venv   # 或指定
```

容器内(官方 vLLM 镜像,CUPTI 在 dist-packages):
```bash
CU13=/usr/local/lib/python3.12/dist-packages/nvidia/cu13
STUB=$(dirname $(ls /usr/local/cuda*/targets/*/lib/stubs/libcuda.so | head -1))
g++ -O2 -fPIC -std=c++17 -I$CU13/include -shared \
    -L$CU13/lib -L$STUB -Wl,-rpath,$CU13/lib \
    -o libppingcupti.so ppingcupti.cpp -l:libcupti.so.13 -lcuda -pthread
```

## 用法(C-ABI,Python 经 ctypes,见 `collector/cupti.py` 的 `CtypesPcSamplingLib`)

- `pping_pcs_start(period_log2)` — enable+config+start;**★ 必须在 workload 干重活之前调**
  (context 还"新"时),否则 `getNumStallReasons` 返 0。
- `pping_pcs_drain(rows, max)` — 拉走库内已聚合的 `(kernel, stallReason)→count` 小行。
- `pping_pcs_stop()` / `pping_pcs_stall_reason_name()` / `pping_pcs_overhead()` / `pping_pcs_available()`。
- ctypes 加载用 `RTLD_DEEPBIND`(进程里可能并存多版本 libcupti)。

产品里由 `PcSamplingController` 编排:**prime(早 enable 一次)+ run_window(按需 drain 一段窗)**。

## 部署配方:在容器里对真 vLLM 采样(已验证)

根因:本机 venv 的 pip CUDA 库 **cu12/cu13 串味**会让 PC Sampling `getNumStallReasons` 返 0
(**不是 WSL、不是 torch、不是代码**)。解法 = 单一一致 CUDA 栈,用官方 vLLM 镜像:

1. **网络(WSL)**:`~/.wslconfig` 加 `[wsl2]\nnetworkingMode=mirrored`,`wsl --shutdown`
   (NAT 模式下宿主 localhost 代理进不了 WSL/Docker)。容器联网:`-e HTTPS_PROXY=http://host.docker.internal:7890`。
2. **profiling 权限(宿主)**:NVIDIA 控制面板 → 开发者 → 管理 GPU 性能计数器 → 允许所有用户访问
   (透传进容器;手动改注册表会被驱动开机覆盖)。
3. **镜像**:`docker pull vllm/vllm-openai:latest`;`docker run --rm --gpus all -v <repo>:/work …`。
4. **跑**:容器里装 `duckdb`(镜像缺)、`PYTHONPATH=/work/src`、`PPING_LANG_PCS_SO=…/libppingcupti.so`,
   流程 = warmup → `prime` → 载 vLLM → 推理 → drain。参考 `_scratch/phase2-probe/vllm_img_real.sh`。

## §11 持续采样稳定性(已解)

**现象**:持续采样 + vLLM 持续 JIT triton kernel 时 segfault,栈 = `strdup ← cuptiPCSamplingGetData
← worker_loop`。**真因**(真机诊断坐实):worker 的 GetData 在 strdup 一个已被**异步卸载/释放**的
module functionName(source);这个释放发生在 CUPTI 内部/CUDA 模块缓存,在我们 bracket 不到的地方。

**走过的弯路**(均被证伪):RESOURCE 回调 flush(太晚)、DRIVER_API load/unload bracketing + 互斥锁
(注入抢槽成功、回调确实触发、锁也加了 —— 2ms cadence 下**仍崩**,证明同步串行化挡不住异步释放)。

**真正起效的**:**拉开 worker drain 间隔**,把"worker 泡在 GetData 里"的占空比从 ~46%(2ms)降到
<1%,与那个异步释放几乎不再重叠。实测(注入 + 持续推理 + 持续 JIT):

| drain cadence | 占空比 | 结果 |
|---|---|---|
| 2ms | ~46% | 崩(iter 12) |
| 200ms | ~0.8% | 稳 60s / 3.95 亿样本 |
| 500ms(默认) | ~0.35% | 稳 90s / 9.3 亿样本;live dashboard 连打 6 次零崩 |
| 1s | ~0.17% | 稳 120s / 5.6 亿样本 |

默认 cadence = 500ms(`PpingPcs` worker;`PPING_PCS_DRAIN_US` 可调,单位 us)。均 `dropped=0 hwfull=0`。
注:这是**竞窗收敛**而非硬同步——对 vLLM 推理这档采样率,500ms 落在"既不崩、HW 缓冲也不溢"的安全窗内;
满速 kernel 采样率更高需更勤 drain(更接近崩区),按负载用 `PPING_PCS_DRAIN_US` 自调。

## 其他限制

- **enable 必须早**(见上)。Deep Evidence 因此是"早 prime + 按需 drain 窗",不是"按需晚 enable"。
- **Linux/WSL only**;Windows 本机 `available()`→False,优雅 no-op。
