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
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include <unistd.h>
#include <cuda.h>
#include <cupti_result.h>
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
};

State g;

double now_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

// drain 线程主体:持续 GetData,把样本累进 live。不持 GIL(纯 C++)。
void worker_loop() {
    if (g.ctx) cuCtxSetCurrent(g.ctx);  // drain 线程绑同一 context(Codex 补强)
    CUpti_PCSamplingData& sd = g.sd;
    while (!g.stop_flag.load(std::memory_order_relaxed)) {
        CUpti_PCSamplingGetDataParams gp;
        std::memset(&gp, 0, sizeof gp);
        gp.size = CUpti_PCSamplingGetDataParamsSize;
        gp.ctx = g.ctx;
        gp.pcSamplingData = (void*)&sd;
        double t0 = now_ms();
        CUptiResult r = cuptiPCSamplingGetData(&gp);
        g.getdata_ms.store(g.getdata_ms.load() + (now_ms() - t0));
        if (r == CUPTI_ERROR_OUT_OF_MEMORY) {  // HW 缓冲满,这一轮无数据
            g.hwfull.fetch_add(1);
            usleep(1000);
            continue;
        }
        if (r != CUPTI_SUCCESS) { usleep(2000); continue; }
        g.dropped.fetch_add(sd.droppedSamples);
        if (sd.totalNumPcs) {
            std::lock_guard<std::mutex> lk(g.mu);
            for (size_t i = 0; i < sd.totalNumPcs; ++i) {
                CUpti_PCSamplingPCData& pc = sd.pPcData[i];
                const char* nm = pc.functionName ? pc.functionName : "?";
                auto& byReason = g.live[nm];
                for (size_t j = 0; j < pc.stallReasonCount; ++j)
                    byReason[pc.stallReason[j].pcSamplingStallReasonIndex] +=
                        pc.stallReason[j].samples;
            }
        }
        usleep(1000);
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

}  // namespace

extern "C" {

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
        if (r != CUPTI_SUCCESS) { set_err("getNumStallReasons", r); return -4; }
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
            return -5;
        }
    }

    // parsed-data 缓冲
    const size_t COLLECT = 4000;
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
    cfg[nc].attributeData.scratchBufferSizeData.scratchBufferSize = (size_t)16 * 1024 * 1024; nc++;
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
        if (r != CUPTI_SUCCESS) { set_err("setConfig", r); free_sd(); return -6; }
    }

    {
        CUpti_PCSamplingStartParams p;
        std::memset(&p, 0, sizeof p);
        p.size = CUpti_PCSamplingStartParamsSize; p.ctx = ctx;
        r = cuptiPCSamplingStart(&p);
        if (r != CUPTI_SUCCESS) { set_err("start", r); free_sd(); return -7; }
    }

    g.getdata_ms.store(0.0); g.dropped.store(0); g.hwfull.store(0);
    g.stop_flag.store(false);
    g.running.store(true);
    g.worker = std::thread(worker_loop);
    g_err[0] = 0;
    return 0;
}

int pping_pcs_stop(void) {
    if (!g.running.load()) return 0;
    g.stop_flag.store(true);
    if (g.worker.joinable()) g.worker.join();
    CUptiResult r;
    {
        CUpti_PCSamplingStopParams p;
        std::memset(&p, 0, sizeof p);
        p.size = CUpti_PCSamplingStopParamsSize; p.ctx = g.ctx;
        r = cuptiPCSamplingStop(&p);
        if (r != CUPTI_SUCCESS) set_err("stop", r);
    }
    // stop 后再 drain 一次残留进 live(下一次 pping_pcs_drain 取走)
    {
        CUpti_PCSamplingGetDataParams gp;
        std::memset(&gp, 0, sizeof gp);
        gp.size = CUpti_PCSamplingGetDataParamsSize; gp.ctx = g.ctx; gp.pcSamplingData = (void*)&g.sd;
        for (int k = 0; k < 64; ++k) {
            if (cuptiPCSamplingGetData(&gp) != CUPTI_SUCCESS) break;
            if (g.sd.totalNumPcs) {
                std::lock_guard<std::mutex> lk(g.mu);
                for (size_t i = 0; i < g.sd.totalNumPcs; ++i) {
                    CUpti_PCSamplingPCData& pc = g.sd.pPcData[i];
                    const char* nm = pc.functionName ? pc.functionName : "?";
                    auto& byReason = g.live[nm];
                    for (size_t j = 0; j < pc.stallReasonCount; ++j)
                        byReason[pc.stallReason[j].pcSamplingStallReasonIndex] += pc.stallReason[j].samples;
                }
            }
            if (g.sd.remainingNumPcs == 0 && g.sd.totalNumPcs == 0) break;
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

const char* pping_pcs_last_error(void) { return g_err; }

}  // extern "C"
