/* libppingcupti —— 阶段 2 PC Sampling 原生数据平面的 C-ABI(给 Python ctypes 用)。
 *
 * 设计:阶段 2 采集设计文档 _design-notes/phase-2-PC-Sampling-采集设计.md §6。
 * 定位:这是 KernelActivitySource 边界下面那层"小、稳、少动"的原生件。
 *   - 在 .so 内跑独立 drain 线程持续 cuptiPCSamplingGetData(不持 GIL)。
 *   - 库内把百万样本/s 预聚合成 (kernel, stallReason)->count(守 5% 预算,绝不过单样本)。
 *   - Python 每 ~1s 调 pping_pcs_drain 拉走一批已聚合的小行。
 *
 * 所有函数返回 0/正数表成功,负数表失败(细节见 pping_pcs_last_error)。
 */
#ifndef PPINGCUPTI_H
#define PPINGCUPTI_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* 一条已聚合行:某 kernel 的某 stall reason 在本次 drain 区间的累计样本数。
 * 固定大小,便于 ctypes 镜像。kernel 名截断到 PPING_KERNEL_NAME_LEN-1。 */
#define PPING_KERNEL_NAME_LEN 256
typedef struct {
    unsigned int       stall_reason;             /* PerfWorks stall reason 索引 */
    unsigned int       _pad;
    unsigned long long samples;                  /* 本批样本数 */
    char               kernel[PPING_KERNEL_NAME_LEN];  /* kernel 函数名(截断) */
} PpingStallRow;

/* 当前进程/设备能否做 PC Sampling(有 CUDA context + CUPTI 可用)。1=可,0=否。 */
int pping_pcs_available(void);

/* 在"当前 CUDA context"上配置并启动连续 PC Sampling,起 drain 线程。
 * period_log2 ∈ [5,31](每 2^period_log2 周期采一次);传 0 用默认 12。
 * 必须在已有 CUDA context(vLLM/torch 已建)后调用。返回 0 成功。 */
int pping_pcs_start(int period_log2);

/* 停止 drain 线程 + stop + disable + flush 残留。幂等。返回 0 成功。 */
int pping_pcs_stop(void);

/* 拉走自上次 drain 以来库内聚合的所有 (kernel, reason) 行(snapshot-swap,内部清零)。
 * 最多写 max_rows 行到 out;返回写入行数(>=0),负数表错误。
 * 注:若聚合行数 > max_rows,多余的丢弃并计入 pping_pcs_overhead 的 dropped(诚实)。 */
int pping_pcs_drain(PpingStallRow* out, int max_rows);

/* stall reason 索引 -> 名字(如 smsp__pcsamp_warps_issue_stalled_long_scoreboard)。
 * 写入 buf(最多 buflen-1 + 结尾 0);返回写入长度,负数表未知索引。 */
int pping_pcs_stall_reason_name(unsigned int idx, char* buf, int buflen);

/* 自我观测(5% 预算可见性):
 *   getdata_ms = 自 start 起 cuptiPCSamplingGetData 累计墙钟(ms)
 *   dropped    = 丢弃样本数(HW 满)+ drain 容量溢出丢的行
 *   hwfull     = GetData 返回 OUT_OF_MEMORY 的次数 */
void pping_pcs_overhead(double* getdata_ms,
                        unsigned long long* dropped,
                        unsigned long long* hwfull);

/* 最近一次错误描述(诊断用)。 */
const char* pping_pcs_last_error(void);

#ifdef __cplusplus
}
#endif

#endif /* PPINGCUPTI_H */
