/* libppingcupti —— 阶段 2 PC Sampling 原生数据平面实现。
 *
 * 从 _scratch/phase2-probe/m0.sh(真机已验证 431 万样本)抽出,改成:
 *   - 后台 drain 线程持续 GetData + 库内聚合(M0 是主线程轮询)
 *   - double-buffer / snapshot-swap:drain 线程累加 live,pping_pcs_drain 一把换出
 *   - C-ABI 暴露给 Python ctypes
 *
 * 已知坑(设计文档 §0/§9/§11):
 *   - CUDA 13:不 include 伞头 cupti.h;cuCtxCreate 已 _v4(本文件不建 context,用现成的)
 *   - module load/unload flush:连续 drain 本身即周期 flush,M1 依赖它(steady-state 足够)
 *   - profiling 权限:需宿主放开(ERR_NVGPUCTRPERM),否则 start 报 INSUFFICIENT_PRIVILEGES
 */
#include "ppingcupti.h"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#include <unistd.h>
#include <execinfo.h>   // backtrace —— launch 栈采集(allocation-free)
#include <dlfcn.h>      // dladdr / dlsym —— 符号化 + 动态取 cuFuncGetName
#include <cxxabi.h>     // __cxa_demangle —— C++ 符号还原
#include <cuda.h>
#include <cupti_result.h>
#include <cupti_callbacks.h>
#include <cupti_driver_cbid.h>
#include <cupti_pcsampling.h>

namespace {

char g_err[512] = {0};
void set_err(const char* where, CUptiResult r) {
    const char* s = "?";
    cuptiGetResultString(r, &s);
    std::snprintf(g_err, sizeof g_err, "%s -> CUPTI %d (%s)", where, (int)r, s);
}
void set_err_str(const char* msg) { std::snprintf(g_err, sizeof g_err, "%s", msg); }

struct State {
    CUcontext ctx = nullptr;
    std::atomic<bool> running{false};
    std::atomic<bool> stop_flag{false};
    std::thread worker;

    // 聚合:kernel 名 -> (stallReason 索引 -> 样本数)。drain 线程写,pping_pcs_drain 换出。
    std::mutex mu;
    std::unordered_map<std::string, std::unordered_map<unsigned int, unsigned long long>> live;

    // P3 源码行级:per-(cubinCrc, pcOffset) -> {kernel 名, 累计样本}。默认不开
    // (PPING_LANG_PCS_PC_HIST=1 才填),否则每样本多一次 hash 写,白付开销。
    // 结构:crc -> (offset -> {kernel, samples})。pping_pcs_drain_pc 换出。
    bool pc_hist = false;
    std::unordered_map<unsigned long long,
        std::unordered_map<unsigned long long,
            std::pair<std::string, unsigned long long>>> live_pc;

    // P3 launch 栈(MVP):CUfunction -> {首次抓的 native 栈, launch 计数}。默认不开
    // (PPING_LANG_PCS_LAUNCH_STACK=1)。launch 回调里只 find+count++(持 launch_mu,极短),
    // 首见才 backtrace 一次(同款 kernel 启动路径稳定);**绝不碰 api_mu**(否则每次 launch
    // 与 PC drain 串行,开销爆)。
    bool launch_stack = false;
    static const int kMaxFrames = 24;
    struct LaunchAgg {
        void* frames[kMaxFrames];
        int nframes = 0;
        unsigned long long count = 0;
    };
    std::mutex launch_mu;
    std::unordered_map<void*, LaunchAgg> launches;  // key = CUfunction
    // 串行化所有 PC sampling API 调用(GetData)与 module load/unload —— vLLM 推理中持续 JIT
    // triton kernel,cuModuleLoad/Unload 改 CUPTI 内部函数表,与 worker 的 GetData 并发会
    // use-after-free 崩(§11)。worker GetData 与 module load/unload 的 driver 回调都持此锁,
    // 整个 load/unload 调用被锁包住。recursive:防同线程 module 调用嵌套自死锁。
    std::recursive_mutex api_mu;

    // stall reason 索引<->名字
    std::vector<unsigned int> rIdx;
    std::vector<std::string> rName;
    size_t numReasons = 0;

    // PC sampling 解析缓冲(parsed 模式)
    CUpti_PCSamplingData sd{};

    // 自我观测
    std::atomic<double> getdata_ms{0.0};
    std::atomic<unsigned long long> dropped{0};
    std::atomic<unsigned long long> hwfull{0};

    // §11 诊断:module/library load/unload 的 DRIVER_API 回调实际触发次数。
    // 若持续采样 + vLLM JIT 期间这个一直是 0,说明回调没装上(subscriber 槽被
    // torch/Kineto 抢了 → 串行化没生效 → 崩)。injected = 经 CUDA_INJECTION64_PATH
    // 在 cuInit(torch 之前)抢到 subscriber 槽。
    std::atomic<unsigned long long> module_cbs{0};
    bool injected = false;

    // §11 plan B(JIT 冷却):记录最近一次 module load/unload 回调的时刻。worker 在此后
    // 一小段冷却窗内**不 GetData**,避开 CUPTI 对刚卸载 module 的 functionName 的事后异步
    // 释放(那是崩点,且在我们 bracket 不到的地方发生)。skipped_drains = 因冷却跳过的次数。
    std::atomic<double> last_module_event_ms{0.0};
    std::atomic<unsigned long long> skipped_drains{0};
};

State g;

// PPING_PCS_DEBUG=1 时把 subscribe / 回调诊断打到 stderr(只读一次,缓存)。
bool dbg() {
    static int v = -1;
    if (v < 0) { const char* e = std::getenv("PPING_PCS_DEBUG"); v = (e && *e && *e != '0') ? 1 : 0; }
    return v != 0;
}

double now_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

// 一次 GetData + 累加进 live。**调用方必须已持 g.api_mu**(与 module 回调互斥)。
// 返回 totalNumPcs(这一批拿到的 PC 数)。
size_t drain_sd_apilocked() {
    CUpti_PCSamplingGetDataParams gp;
    std::memset(&gp, 0, sizeof gp);
    gp.size = CUpti_PCSamplingGetDataParamsSize;
    gp.ctx = g.ctx;
    gp.pcSamplingData = (void*)&g.sd;
    double t0 = now_ms();
    CUptiResult r = cuptiPCSamplingGetData(&gp);
    g.getdata_ms.store(g.getdata_ms.load() + (now_ms() - t0));
    if (r == CUPTI_ERROR_OUT_OF_MEMORY) { g.hwfull.fetch_add(1); return 0; }
    if (r != CUPTI_SUCCESS) return 0;
    g.dropped.fetch_add(g.sd.droppedSamples);
    if (g.sd.totalNumPcs) {
        std::lock_guard<std::mutex> lk(g.mu);
        for (size_t i = 0; i < g.sd.totalNumPcs; ++i) {
            CUpti_PCSamplingPCData& pc = g.sd.pPcData[i];
            const char* nm = pc.functionName ? pc.functionName : "?";
            auto& byReason = g.live[nm];
            unsigned long long pcSamples = 0;
            for (size_t j = 0; j < pc.stallReasonCount; ++j) {
                unsigned long long s = pc.stallReason[j].samples;
                byReason[pc.stallReason[j].pcSamplingStallReasonIndex] += s;
                pcSamples += s;
            }
            // P3:per-PC 直方图(cubinCrc + pcOffset 是连到源码行的钥匙)。仅开启时累加。
            if (g.pc_hist && pcSamples) {
                auto& slot = g.live_pc[pc.cubinCrc][pc.pcOffset];
                if (slot.first.empty()) slot.first = nm;  // 首见时记 kernel 名
                slot.second += pcSamples;
            }
        }
    }
    return g.sd.totalNumPcs;
}

// drain 线程主体:持续 GetData,把样本累进 live。不持 GIL(纯 C++)。
// drain 间隔可经 PPING_PCS_DRAIN_US 调(微秒)。
//
// ★ §11 解(真机实测):崩点是 worker 的 GetData strdup 一个已被异步卸载/释放的
// functionName(source);这个释放发生在我们 bracket 不到的地方(CUPTI 内部/CUDA 模块缓存),
// 靠 DRIVER_API 回调串行化挡不住(已证伪:注入抢槽+回调触发+互斥锁,2ms 仍崩)。真正起效的是
// 拉开 drain 间隔——把"worker 泡在 GetData 里"的占空比从 ~46%(2ms)降到 <1%,与那个释放
// 几乎不再重叠。实测:2ms 崩;200ms 稳 60s/3.95亿样本;1s 稳 120s/5.6亿样本(均 dropped=0
// hwfull=0)。默认 500us*1000=500ms,夹在两个已验证安全点之间,对 vLLM 推理采样既稳又够新。
// 注:安全 cadence 与负载相关(满速 kernel 采样率更高、需更勤 drain 防 HW 缓冲溢出,但更勤=更
// 接近崩区)——对 vLLM 推理这档 500ms 落在安全窗内;其他负载用 PPING_PCS_DRAIN_US 自调。
void worker_loop() {
    if (g.ctx) cuCtxSetCurrent(g.ctx);  // drain 线程绑同一 context(Codex 补强)
    unsigned drain_us = 500000;
    if (const char* e = std::getenv("PPING_PCS_DRAIN_US")) {
        long v = std::strtol(e, nullptr, 10);
        if (v > 0) drain_us = (unsigned)v;
    }
    // plan B:module load/unload 事件后冷却 cooldown_ms 内不 GetData(避开事后异步释放)。
    // 默认 300ms;0=关闭(退回纯 cadence)。PPING_PCS_JIT_COOLDOWN_MS 可调。
    double cooldown_ms = 300.0;
    if (const char* e = std::getenv("PPING_PCS_JIT_COOLDOWN_MS")) {
        char* end = nullptr; double v = std::strtod(e, &end);
        if (end != e && v >= 0) cooldown_ms = v;
    }
    if (dbg()) std::fprintf(stderr, "[ppingcupti] worker drain=%u us, JIT 冷却=%.0f ms\n",
                            drain_us, cooldown_ms);
    while (!g.stop_flag.load(std::memory_order_relaxed)) {
        // 冷却门:若最近一次 module 事件距今 < cooldown_ms,跳过本轮 GetData(让 CUPTI 把
        // 卸载后的异步释放做完再读)。冷却期间样本继续进 HW 缓冲,过后照常 drain(不丢)。
        double since = now_ms() - g.last_module_event_ms.load();
        if (cooldown_ms > 0.0 && since < cooldown_ms) {
            g.skipped_drains.fetch_add(1);
        } else {
            // ★ 排空式 drain(cudagraph 实测教训):图回放的 PC 记录生产速度远超
            // "每周期一次×4000"的排水量 → 积压 → scratch 打满 → 之后每次 GetData 永远
            // OUT_OF_MEMORY(hwfull 每周期+1、样本全失,引擎无感)。每周期必须循环 GetData
            // 直到排空(remainingNumPcs=0);OOM 时重试(消费即腾 scratch,可恢复),连续
            // 无进展才放弃本周期。锁按次取放,给 module 回调留插队空隙。
            int oom_streak = 0;
            for (int it = 0; it < 4096; ++it) {
                size_t got;
                bool empty;
                {
                    std::lock_guard<std::recursive_mutex> lk(g.api_mu);  // 与 module load/unload 互斥
                    got = drain_sd_apilocked();
                    empty = (got == 0 && g.sd.remainingNumPcs == 0);
                }
                if (got > 0) { oom_streak = 0; continue; }
                if (empty) break;
                // got==0 且 remaining>0:OOM 或瞬时空转 → 重试,连续 16 次无进展放弃本周期
                if (++oom_streak >= 16) break;
            }
        }
        usleep(drain_us);
    }
}

void free_sd() {
    if (g.sd.pPcData) {
        for (size_t i = 0; i < g.sd.collectNumPcs; ++i)
            free(g.sd.pPcData[i].stallReason);
        free(g.sd.pPcData);
        g.sd.pPcData = nullptr;
    }
}

// 失败收尾:disable PC sampling,别把设备/容器的 profiling 状态留在 enabled(否则
// 后续进程的 start 也会挂,见设计文档 §12 容器污染)。
void disable_pcs(CUcontext ctx) {
    CUpti_PCSamplingDisableParams dp;
    std::memset(&dp, 0, sizeof dp);
    dp.size = CUpti_PCSamplingDisableParamsSize; dp.ctx = ctx;
    cuptiPCSamplingDisable(&dp);
}

}  // namespace

static CUpti_SubscriberHandle g_sub = nullptr;

// 我们关心的 module/library load/unload driver API —— 整个调用要被 api_mu 包住,
// 期间 worker 不能 GetData(否则与 CUPTI 改函数表并发崩,§11)。
static const CUpti_driver_api_trace_cbid_enum kModuleCbids[] = {
    CUPTI_DRIVER_TRACE_CBID_cuModuleLoad,
    CUPTI_DRIVER_TRACE_CBID_cuModuleLoadData,
    CUPTI_DRIVER_TRACE_CBID_cuModuleLoadDataEx,
    CUPTI_DRIVER_TRACE_CBID_cuModuleLoadFatBinary,
    CUPTI_DRIVER_TRACE_CBID_cuModuleUnload,
    CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadData,
    CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadFromFile,
    CUPTI_DRIVER_TRACE_CBID_cuLibraryUnload,
};

// P3:我们关心的 launch driver API —— 只为采 launch 栈,**不**串行化(不碰 api_mu)。
// 注:cbid 是 enum 成员不是宏,#ifdef 守不住(早期版本踩过:守了等于没编进去)。
// _ptsz(per-thread stream)变体参数布局与原版相同,一并钩。
static const CUpti_driver_api_trace_cbid_enum kLaunchCbids[] = {
    CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel,
    CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel_ptsz,
    CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx,
    CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx_ptsz,
};

static inline bool is_launch_cbid(CUpti_CallbackId cbid) {
    return cbid == (CUpti_CallbackId)CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel ||
           cbid == (CUpti_CallbackId)CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel_ptsz ||
           cbid == (CUpti_CallbackId)CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx ||
           cbid == (CUpti_CallbackId)CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx_ptsz;
}

// ★ cudagraph 串行化(§11 的延伸,真机实测):稳态图回放走 cuGraphLaunch,不触发 module
// 回调 —— GetData 与图启动在驱动里并发,在 Blackwell+CUPTI13 上表现为 EngineCore 静默死锁
// (推理挂死、无任何报错;LAUNCH_STACK 开关都复现,排除 launch 回调)。修法同 §11:把
// cuGraphLaunch 也纳入 api_mu 伞下,图启动期间绝不 GetData。FULL 模式每 decode step 仅一次
// 图启动,锁竞争代价 ~一次 GetData 时长(≈20ms)/500ms 周期,可接受。
// 注意:**不要**刷新 last_module_event_ms —— 图启动是常态高频事件,刷了冷却窗会让 GetData
// 永远饿死(HW 缓冲打满全丢样)。
static const CUpti_driver_api_trace_cbid_enum kGraphLaunchCbids[] = {
    CUPTI_DRIVER_TRACE_CBID_cuGraphLaunch,
    CUPTI_DRIVER_TRACE_CBID_cuGraphLaunch_ptsz,
};

static inline bool is_graph_launch_cbid(CUpti_CallbackId cbid) {
    return cbid == (CUpti_CallbackId)CUPTI_DRIVER_TRACE_CBID_cuGraphLaunch ||
           cbid == (CUpti_CallbackId)CUPTI_DRIVER_TRACE_CBID_cuGraphLaunch_ptsz;
}

// 从 launch 参数取 CUfunction(只读首字段,driver API 参数 ABI 稳定)。
//   cuLaunchKernel:   { CUfunction f; ... }              —— f 是首字段
//   cuLaunchKernelEx: { const CUlaunchConfig* config; CUfunction f; ... } —— f 在 config 之后
static inline CUfunction launch_func(CUpti_CallbackId cbid, const void* fp) {
    if (!fp) return nullptr;
    if (cbid == (CUpti_CallbackId)CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel)
        return *(CUfunction*)fp;
#ifdef CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx
    if (cbid == (CUpti_CallbackId)CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx) {
        struct ExHdr { const void* config; CUfunction f; };
        return ((const ExHdr*)fp)->f;
    }
#endif
    return nullptr;
}

// DRIVER_API 回调:module/library load/unload 的 ENTER 锁住 api_mu(直到 EXIT 解锁),
// 把整个 load/unload 调用与 worker 的 GetData 串行;卸载在 ENTER 后才执行,故 ENTER 时
// 先 flush 把该 module 的 PC 数据收干净,避免 functionName 被 free 后 use-after-free。
static void CUPTIAPI _cupti_cb(void*, CUpti_CallbackDomain domain,
                               CUpti_CallbackId cbid, const void* cbdata) {
    if (domain != CUPTI_CB_DOMAIN_DRIVER_API) return;
    if (!g.running.load()) return;
    const CUpti_CallbackData* cb = (const CUpti_CallbackData*)cbdata;
    // P3 launch 栈:launch 回调单独轻量处理,**绝不碰 api_mu**(否则每次 launch 与 PC drain
    // 串行);首见某 CUfunction 抓一次 backtrace(同款 kernel 启动路径稳定),之后只 count++。
    if (g.launch_stack && is_launch_cbid(cbid)) {
        if (cb->callbackSite == CUPTI_API_ENTER) {
            CUfunction f = launch_func(cbid, cb->functionParams);
            if (f) {
                std::lock_guard<std::mutex> lk(g.launch_mu);
                State::LaunchAgg& a = g.launches[(void*)f];
                if (a.nframes == 0)  // backtrace allocation-free,只首见调一次
                    a.nframes = backtrace(a.frames, State::kMaxFrames);
                a.count++;
            }
        }
        return;
    }
    // ★ cudagraph 回放串行化:图启动期间持 api_mu(挡住 worker 的 GetData),防驱动级死锁。
    // 只锁不刷新冷却(图启动高频,刷了 GetData 永远饿死)。见 kGraphLaunchCbids 注释。
    if (is_graph_launch_cbid(cbid)) {
        if (cb->callbackSite == CUPTI_API_ENTER) g.api_mu.lock();
        else g.api_mu.unlock();
        return;
    }
    g.last_module_event_ms.store(now_ms());  // plan B:刷新冷却起点(ENTER+EXIT 都刷,EXIT 即 op 之后)
    if (cb->callbackSite == CUPTI_API_ENTER) {
        g.api_mu.lock();   // 一直持到 EXIT;期间 worker GetData 阻塞
        unsigned long long cbn = g.module_cbs.fetch_add(1) + 1;
        if (dbg() && cbn == 1)
            std::fprintf(stderr, "[ppingcupti] 首个 module DRIVER_API 回调触发(cbid=%u)"
                         " —— 串行化已生效\n", (unsigned)cbid);
        if (cbid == CUPTI_DRIVER_TRACE_CBID_cuModuleUnload ||
            cbid == CUPTI_DRIVER_TRACE_CBID_cuLibraryUnload) {
            for (int k = 0; k < 512; ++k) {  // 卸载前 flush(functionName 还活着)
                size_t n = drain_sd_apilocked();
                if (n == 0 && g.sd.remainingNumPcs == 0) break;
            }
        }
    } else {  // CUPTI_API_EXIT
        g.api_mu.unlock();
    }
}

extern "C" {

// 抢占 CUPTI:在 torch/Kineto 之前 subscribe,成为最早的 CUPTI 客户。
// 这样后续 pping_pcs_start 才能在 torch 进程内成功(否则 torch 占住 CUPTI,
// 我们的 PC Sampling enable 返 0 stall reasons,见设计文档 §12)。
// 必须在 import torch / vLLM 建 CUDA context 之前调(或经 CUDA_INJECTION64_PATH 注入)。
int pping_pcs_init(void) {
    if (g_sub != nullptr) return 0;
    CUptiResult r = cuptiSubscribe(&g_sub, (CUpti_CallbackFunc)_cupti_cb, nullptr);
    if (r != CUPTI_SUCCESS) {
        g_sub = nullptr;
        set_err("subscribe", r);
        if (dbg()) {
            const char* s = "?"; cuptiGetResultString(r, &s);
            std::fprintf(stderr, "[ppingcupti] cuptiSubscribe 失败 rc=%d (%s) —— 另一个 CUPTI "
                         "subscriber(torch/Kineto?)已占住唯一槽\n", (int)r, s);
        }
        return -1;
    }
    if (dbg()) std::fprintf(stderr, "[ppingcupti] cuptiSubscribe OK g_sub=%p\n", (void*)g_sub);
    g_err[0] = 0;
    return 0;
}

// CUDA 驱动在 cuInit 时通过 CUDA_INJECTION64_PATH 调用此入口(早于应用 CUDA 初始化)。
// 返回非 0 = 成功。这让 .so 作为唯一且最先的 CUPTI 客户(1b 注入式)——torch 再来
// subscribe 就轮不到,我们的 module load/unload 回调才真的会触发(§11 串行化前提)。
int InitializeInjection(void) {
    int ok = pping_pcs_init() == 0;
    if (ok) {
        g.injected = true;
        if (dbg()) std::fprintf(stderr, "[ppingcupti] InitializeInjection: 注入式抢到 CUPTI "
                                "subscriber 槽(torch 之前)\n");
    }
    return ok ? 1 : 0;
}

int pping_pcs_available(void) {
    CUcontext c = nullptr;
    if (cuCtxGetCurrent(&c) != CUDA_SUCCESS || c == nullptr) return 0;
    return 1;
}

int pping_pcs_start(int period_log2) {
    if (g.running.load()) { set_err_str("already running"); return -1; }
    CUcontext ctx = nullptr;
    if (cuCtxGetCurrent(&ctx) != CUDA_SUCCESS || ctx == nullptr) {
        set_err_str("no current CUDA context (call after vLLM/torch init)");
        return -2;
    }
    g.ctx = ctx;
    if (period_log2 <= 0) period_log2 = 12;
    // P3:per-PC 直方图开关(默认关,避免每样本多付一次 hash)。
    if (const char* e = std::getenv("PPING_LANG_PCS_PC_HIST"))
        g.pc_hist = (*e && *e != '0');
    // P3 launch 栈(MVP):默认关,避免每次 launch 多付一次回调。
    if (const char* e = std::getenv("PPING_LANG_PCS_LAUNCH_STACK"))
        g.launch_stack = (*e && *e != '0');

    // 订阅 + 开 module/library load/unload 的 DRIVER_API 回调(§11:vLLM 推理持续 JIT,
    // 整个 load/unload 调用要与 GetData 串行防崩)。**唯一槽**:CUDA 13 只允许一个 CUPTI
    // subscriber——若 torch/Kineto 已占,这里 subscribe 会失败,回调装不上,串行化失效 →
    // 崩。正解是 CUDA_INJECTION64_PATH 在 torch 之前注入抢槽(g_sub 复用,见下分支)。
    if (g_sub == nullptr) {
        CUptiResult sr = cuptiSubscribe(&g_sub, (CUpti_CallbackFunc)_cupti_cb, nullptr);
        if (sr != CUPTI_SUCCESS) {
            g_sub = nullptr;
            if (dbg()) {
                const char* s = "?"; cuptiGetResultString(sr, &s);
                std::fprintf(stderr, "[ppingcupti] start: cuptiSubscribe 失败 rc=%d (%s) —— "
                             "module 回调装不上,持续采样有崩风险。请用 CUDA_INJECTION64_PATH 注入。\n",
                             (int)sr, s);
            }
        } else if (dbg()) {
            std::fprintf(stderr, "[ppingcupti] start: 晚订阅成功 g_sub=%p(非注入)\n", (void*)g_sub);
        }
    } else if (dbg()) {
        std::fprintf(stderr, "[ppingcupti] start: 复用已有 g_sub=%p(injected=%d)—— 串行化将生效\n",
                     (void*)g_sub, (int)g.injected);
    }
    if (g_sub != nullptr) {
        for (CUpti_driver_api_trace_cbid_enum mc : kModuleCbids)
            cuptiEnableCallback(1, g_sub, CUPTI_CB_DOMAIN_DRIVER_API, mc);
        // cudagraph 串行化:默认**关**。py-spy 实锤后真凶是带载 reprime 的 stop()(见
        // engine_pcs.py),GetData 与图回放并发本身没有出过事(1 亿样本窗与图回放共存)。
        // 且持锁跨 ENTER→EXIT 在 async-scheduling 双线程高频 launch 下有 ABBA 死锁隐患
        // (CUPTI 回调内锁 × api_mu),仅留作排查工具。
        if (const char* e = std::getenv("PPING_LANG_PCS_GRAPH_SERIALIZE"); e && *e && *e != '0') {
            for (CUpti_driver_api_trace_cbid_enum gc : kGraphLaunchCbids)
                cuptiEnableCallback(1, g_sub, CUPTI_CB_DOMAIN_DRIVER_API, gc);
            if (dbg()) std::fprintf(stderr, "[ppingcupti] graph-launch 串行化已开启(排查用)\n");
        }
        if (dbg()) std::fprintf(stderr, "[ppingcupti] module DRIVER_API 回调已开启\n");
        if (g.launch_stack) {
            for (CUpti_driver_api_trace_cbid_enum lc : kLaunchCbids)
                cuptiEnableCallback(1, g_sub, CUPTI_CB_DOMAIN_DRIVER_API, lc);
            if (dbg()) std::fprintf(stderr, "[ppingcupti] launch DRIVER_API 回调已开启(MVP 启动栈)\n");
        }
    } else if (dbg()) {
        std::fprintf(stderr, "[ppingcupti] 警告:g_sub 为空,module 回调未开启 —— 持续采样不安全\n");
    }

    CUptiResult r;
    CUpti_PCSamplingEnableParams en;
    std::memset(&en, 0, sizeof en);
    en.size = CUpti_PCSamplingEnableParamsSize; en.ctx = ctx;
    r = cuptiPCSamplingEnable(&en);
    if (r != CUPTI_SUCCESS) { set_err("enable", r); return -3; }

    // stall reason 表
    size_t num = 0;
    {
        CUpti_PCSamplingGetNumStallReasonsParams p;
        std::memset(&p, 0, sizeof p);
        p.size = CUpti_PCSamplingGetNumStallReasonsParamsSize; p.ctx = ctx;
        p.numStallReasons = &num;
        r = cuptiPCSamplingGetNumStallReasons(&p);
        if (r != CUPTI_SUCCESS) { set_err("getNumStallReasons", r); disable_pcs(ctx); return -4; }
    }
    g.numReasons = num;
    g.rIdx.assign(num, 0);
    g.rName.assign(num, std::string());
    {
        std::vector<char*> names(num);
        for (size_t i = 0; i < num; ++i) names[i] = (char*)malloc(128);
        CUpti_PCSamplingGetStallReasonsParams p;
        std::memset(&p, 0, sizeof p);
        p.size = CUpti_PCSamplingGetStallReasonsParamsSize; p.ctx = ctx;
        p.numStallReasons = num; p.stallReasonIndex = g.rIdx.data();
        p.stallReasons = names.data();
        r = cuptiPCSamplingGetStallReasons(&p);
        for (size_t i = 0; i < num; ++i) { if (r == CUPTI_SUCCESS) g.rName[i] = names[i]; free(names[i]); }
        if (r != CUPTI_SUCCESS) {
            const char* s = "?"; cuptiGetResultString(r, &s);
            std::snprintf(g_err, sizeof g_err, "getStallReasons num=%zu -> CUPTI %d (%s)", num, (int)r, s);
            disable_pcs(ctx);
            return -5;
        }
    }

    // parsed-data 缓冲。16384:cudagraph 回放下记录量大,4000 时单次 GetData 吃不动积压
    // (配合 worker 的排空式 drain;内存 ≈16K×~700B≈11MB,可接受)
    const size_t COLLECT = 16384;
    std::memset(&g.sd, 0, sizeof g.sd);
    g.sd.size = sizeof(CUpti_PCSamplingData);
    g.sd.collectNumPcs = COLLECT;
    g.sd.pPcData = (CUpti_PCSamplingPCData*)calloc(COLLECT, sizeof(CUpti_PCSamplingPCData));
    for (size_t i = 0; i < COLLECT; ++i) {
        g.sd.pPcData[i].size = sizeof(CUpti_PCSamplingPCData);
        g.sd.pPcData[i].stallReason =
            (CUpti_PCSamplingStallReason*)calloc(num ? num : 1, sizeof(CUpti_PCSamplingStallReason));
    }

    // config(M0 验证可用的一套)
    CUpti_PCSamplingConfigurationInfo cfg[9];
    std::memset(cfg, 0, sizeof cfg);
    int nc = 0;
    cfg[nc].attributeType = CUPTI_PC_SAMPLING_CONFIGURATION_ATTR_TYPE_SAMPLING_PERIOD;
    cfg[nc].attributeData.samplingPeriodData.samplingPeriod = (uint32_t)period_log2; nc++;
    cfg[nc].attributeType = CUPTI_PC_SAMPLING_CONFIGURATION_ATTR_TYPE_STALL_REASON;
    cfg[nc].attributeData.stallReasonData.stallReasonCount = num;
    cfg[nc].attributeData.stallReasonData.pStallReasonIndex = g.rIdx.data(); nc++;
    cfg[nc].attributeType = CUPTI_PC_SAMPLING_CONFIGURATION_ATTR_TYPE_SCRATCH_BUFFER_SIZE;
    cfg[nc].attributeData.scratchBufferSizeData.scratchBufferSize = (size_t)64 * 1024 * 1024; nc++;  // cudagraph 回放记录量大,16MB 会被积压打满
    cfg[nc].attributeType = CUPTI_PC_SAMPLING_CONFIGURATION_ATTR_TYPE_HARDWARE_BUFFER_SIZE;
    cfg[nc].attributeData.hardwareBufferSizeData.hardwareBufferSize = (size_t)512 * 1024 * 1024; nc++;
    cfg[nc].attributeType = CUPTI_PC_SAMPLING_CONFIGURATION_ATTR_TYPE_WORKER_THREAD_PERIODIC_SLEEP_SPAN;
    cfg[nc].attributeData.workerThreadPeriodicSleepSpanData.workerThreadPeriodicSleepSpan = 5; nc++;
    cfg[nc].attributeType = CUPTI_PC_SAMPLING_CONFIGURATION_ATTR_TYPE_COLLECTION_MODE;
    cfg[nc].attributeData.collectionModeData.collectionMode = CUPTI_PC_SAMPLING_COLLECTION_MODE_CONTINUOUS; nc++;
    cfg[nc].attributeType = CUPTI_PC_SAMPLING_CONFIGURATION_ATTR_TYPE_OUTPUT_DATA_FORMAT;
    cfg[nc].attributeData.outputDataFormatData.outputDataFormat = CUPTI_PC_SAMPLING_OUTPUT_DATA_FORMAT_PARSED; nc++;
    cfg[nc].attributeType = CUPTI_PC_SAMPLING_CONFIGURATION_ATTR_TYPE_SAMPLING_DATA_BUFFER;
    cfg[nc].attributeData.samplingDataBufferData.samplingDataBuffer = &g.sd; nc++;
    cfg[nc].attributeType = CUPTI_PC_SAMPLING_CONFIGURATION_ATTR_TYPE_ENABLE_START_STOP_CONTROL;
    cfg[nc].attributeData.enableStartStopControlData.enableStartStopControl = 1; nc++;
    {
        CUpti_PCSamplingConfigurationInfoParams p;
        std::memset(&p, 0, sizeof p);
        p.size = CUpti_PCSamplingConfigurationInfoParamsSize; p.ctx = ctx;
        p.numAttributes = nc; p.pPCSamplingConfigurationInfo = cfg;
        r = cuptiPCSamplingSetConfigurationAttribute(&p);
        if (r != CUPTI_SUCCESS) { set_err("setConfig", r); disable_pcs(ctx); free_sd(); return -6; }
    }

    {
        CUpti_PCSamplingStartParams p;
        std::memset(&p, 0, sizeof p);
        p.size = CUpti_PCSamplingStartParamsSize; p.ctx = ctx;
        r = cuptiPCSamplingStart(&p);
        if (r != CUPTI_SUCCESS) { set_err("start", r); disable_pcs(ctx); free_sd(); return -7; }
    }

    g.getdata_ms.store(0.0); g.dropped.store(0); g.hwfull.store(0);
    g.module_cbs.store(0);
    g.skipped_drains.store(0);
    g.stop_flag.store(false);
    g.running.store(true);
    g.worker = std::thread(worker_loop);
    g_err[0] = 0;
    return 0;
}

int pping_pcs_stop(void) {
    if (!g.running.load()) return 0;
    if (dbg())
        std::fprintf(stderr, "[ppingcupti] stop: module 回调=%llu 次、因 JIT 冷却跳过 drain=%llu 次、"
                     "getdata_ms=%.1f、dropped=%llu、hwfull=%llu\n",
                     g.module_cbs.load(), g.skipped_drains.load(),
                     g.getdata_ms.load(), g.dropped.load(), g.hwfull.load());
    g.stop_flag.store(true);
    if (g.worker.joinable()) g.worker.join();
    // 关 module 回调 + 标记停止(回调据此 no-op),避免收尾期间还有 module 事件进来
    if (g_sub != nullptr) {
        for (CUpti_driver_api_trace_cbid_enum mc : kModuleCbids)
            cuptiEnableCallback(0, g_sub, CUPTI_CB_DOMAIN_DRIVER_API, mc);
        for (CUpti_driver_api_trace_cbid_enum gc : kGraphLaunchCbids)
            cuptiEnableCallback(0, g_sub, CUPTI_CB_DOMAIN_DRIVER_API, gc);
        if (g.launch_stack)
            for (CUpti_driver_api_trace_cbid_enum lc : kLaunchCbids)
                cuptiEnableCallback(0, g_sub, CUPTI_CB_DOMAIN_DRIVER_API, lc);
    }
    g.running.store(false);
    CUptiResult r;
    {
        CUpti_PCSamplingStopParams p;
        std::memset(&p, 0, sizeof p);
        p.size = CUpti_PCSamplingStopParamsSize; p.ctx = g.ctx;
        r = cuptiPCSamplingStop(&p);
        if (r != CUPTI_SUCCESS) set_err("stop", r);
    }
    // stop 后再 drain 残留进 live(api_mu 保护;worker 已停、回调已关)
    {
        std::lock_guard<std::recursive_mutex> lk(g.api_mu);
        for (int k = 0; k < 64; ++k) {
            size_t n = drain_sd_apilocked();
            if (n == 0 && g.sd.remainingNumPcs == 0) break;
        }
    }
    {
        CUpti_PCSamplingDisableParams p;
        std::memset(&p, 0, sizeof p);
        p.size = CUpti_PCSamplingDisableParamsSize; p.ctx = g.ctx;
        cuptiPCSamplingDisable(&p);
    }
    free_sd();
    g.running.store(false);
    return 0;
}

int pping_pcs_drain(PpingStallRow* out, int max_rows) {
    if (out == nullptr || max_rows <= 0) return 0;
    std::unordered_map<std::string, std::unordered_map<unsigned int, unsigned long long>> snap;
    {
        std::lock_guard<std::mutex> lk(g.mu);
        snap.swap(g.live);  // snapshot-swap:换出后不再持锁
    }
    int n = 0;
    for (auto& kv : snap) {
        for (auto& rs : kv.second) {
            if (n >= max_rows) { g.dropped.fetch_add(rs.second); continue; }
            PpingStallRow& row = out[n++];
            row.stall_reason = rs.first;
            row._pad = 0;
            row.samples = rs.second;
            std::strncpy(row.kernel, kv.first.c_str(), PPING_KERNEL_NAME_LEN - 1);
            row.kernel[PPING_KERNEL_NAME_LEN - 1] = 0;
        }
    }
    return n;
}

int pping_pcs_drain_pc(PpingPcRow* out, int max_rows) {
    if (out == nullptr || max_rows <= 0) return 0;
    std::unordered_map<unsigned long long,
        std::unordered_map<unsigned long long,
            std::pair<std::string, unsigned long long>>> snap;
    {
        std::lock_guard<std::mutex> lk(g.mu);
        snap.swap(g.live_pc);  // snapshot-swap,与 stall 聚合同一把锁
    }
    int n = 0;
    for (auto& byCrc : snap) {
        for (auto& byOff : byCrc.second) {
            if (n >= max_rows) { g.dropped.fetch_add(byOff.second.second); continue; }
            PpingPcRow& row = out[n++];
            row.cubin_crc = byCrc.first;
            row.pc_offset = byOff.first;
            row.samples = byOff.second.second;
            std::strncpy(row.kernel, byOff.second.first.c_str(), PPING_KERNEL_NAME_LEN - 1);
            row.kernel[PPING_KERNEL_NAME_LEN - 1] = 0;
        }
    }
    return n;
}

int pping_pcs_drain_launches(PpingLaunchRow* out, int max_rows) {
    if (out == nullptr || max_rows <= 0) return 0;
    std::unordered_map<void*, State::LaunchAgg> snap;
    {
        std::lock_guard<std::mutex> lk(g.launch_mu);
        snap.swap(g.launches);  // snapshot-swap;launch 计数从头累(下批重新攒)
    }
    // 动态取 cuFuncGetName(cu12.3+);老驱动没有 → kernel 名退化为 func_<ptr>
    typedef CUresult (*FnGetName)(const char**, CUfunction);
    static FnGetName p_getname = (FnGetName)dlsym(RTLD_DEFAULT, "cuFuncGetName");
    int n = 0;
    for (auto& kv : snap) {
        if (n >= max_rows) break;
        PpingLaunchRow& row = out[n];
        CUfunction f = (CUfunction)kv.first;
        State::LaunchAgg& a = kv.second;
        row.launches = a.count;
        const char* knm = nullptr;
        if (p_getname && p_getname(&knm, f) == CUDA_SUCCESS && knm) {
            std::strncpy(row.kernel, knm, PPING_KERNEL_NAME_LEN - 1);
            row.kernel[PPING_KERNEL_NAME_LEN - 1] = 0;
        } else {
            std::snprintf(row.kernel, PPING_KERNEL_NAME_LEN, "func_%p", (void*)f);
        }
        // 符号化栈:跳过采集器/CUPTI/驱动自身的帧,取若干有意义帧," <- " 连接
        std::string s;
        int kept = 0;
        for (int i = 0; i < a.nframes && kept < 6; ++i) {
            Dl_info info;
            if (!dladdr(a.frames[i], &info) || !info.dli_sname) continue;
            const char* fn = info.dli_fname ? info.dli_fname : "";
            if (std::strstr(fn, "libppingcupti") || std::strstr(fn, "libcupti") ||
                std::strstr(fn, "libcuda.so"))
                continue;
            int st = 0;
            char* dm = abi::__cxa_demangle(info.dli_sname, nullptr, nullptr, &st);
            std::string nm = (st == 0 && dm) ? std::string(dm) : std::string(info.dli_sname);
            if (dm) free(dm);
            if (nm.size() > 80) nm = nm.substr(0, 77) + "...";
            if (!s.empty()) s += " <- ";
            s += nm;
            kept++;
        }
        std::strncpy(row.stack, s.c_str(), sizeof(row.stack) - 1);
        row.stack[sizeof(row.stack) - 1] = 0;
        n++;
    }
    return n;
}

int pping_pcs_stall_reason_name(unsigned int idx, char* buf, int buflen) {
    if (!buf || buflen <= 0) return -1;
    for (size_t i = 0; i < g.numReasons; ++i) {
        if (g.rIdx[i] == idx) {
            std::strncpy(buf, g.rName[i].c_str(), buflen - 1);
            buf[buflen - 1] = 0;
            return (int)strlen(buf);
        }
    }
    return -1;
}

void pping_pcs_overhead(double* getdata_ms, unsigned long long* dropped, unsigned long long* hwfull) {
    if (getdata_ms) *getdata_ms = g.getdata_ms.load();
    if (dropped) *dropped = g.dropped.load();
    if (hwfull) *hwfull = g.hwfull.load();
}

/* 诊断:worker 因 JIT 冷却跳过的 drain 次数(枯竭排查:冷却饿死 GetData 的直接证据)。 */
unsigned long long pping_pcs_skipped_drains(void) { return g.skipped_drains.load(); }

/* 诊断:module DRIVER_API 回调累计触发数(负载期持续上涨 = 持续 JIT/模块事件)。 */
unsigned long long pping_pcs_module_cbs(void) { return g.module_cbs.load(); }

const char* pping_pcs_last_error(void) { return g_err; }

}  // extern "C"
