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

/* 抢占 CUPTI(在 torch/Kineto 之前 subscribe,成为最早的客户)。
 * 必须在 import torch / 建 CUDA context 之前调,否则 torch 进程内 PC Sampling 起不来。
 * 返回 0 成功。也可经 CUDA_INJECTION64_PATH 注入(驱动自动调 InitializeInjection)。 */
int pping_pcs_init(void);

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

/* P3 源码行级:一条 per-PC 聚合行 —— 某 kernel 内某指令地址(cubinCrc+pcOffset)的累计
 * stall 样本(已扣 issued)。配合 cubin 反汇编的 offset→源码行表,可归因到 .py 行 / SASS
 * 偏移。固定大小,便于 ctypes 镜像。默认不开,PPING_LANG_PCS_PC_HIST=1 启用。 */
typedef struct {
    unsigned long long cubin_crc;                /* CUpti cubinCrc,关联具体 cubin */
    unsigned long long pc_offset;                /* 函数内指令偏移 */
    unsigned long long samples;                  /* 该 PC 的累计 stall 样本 */
    char               kernel[PPING_KERNEL_NAME_LEN];  /* kernel 函数名(截断) */
} PpingPcRow;

/* 拉走 per-PC 直方图(snapshot-swap,内部清零)。仅当 PPING_LANG_PCS_PC_HIST=1 时有数据。
 * 最多写 max_rows 行;返回写入行数(>=0),负数表错误。超出容量的并入 dropped。 */
int pping_pcs_drain_pc(PpingPcRow* out, int max_rows);

/* P3 launch 栈(MVP):一条 per-kernel 的 native 启动栈 —— 这个 kernel 从哪段 host 代码
 * (libtorch/cuBLAS 调用路径)launch 的。即便闭源 GEMM 进不去,也能"向外归因"到调用它的
 * 代码路径(对标 zymtrace 的核心能力,轻量版:到 C++ 帧 / Python 解释器边界)。
 * 仅 PPING_LANG_PCS_LAUNCH_STACK=1 时采集;首次见到某 kernel 抓一次栈,之后只累加计数。 */
typedef struct {
    unsigned long long launches;                 /* 本批该 kernel 的 launch 次数 */
    char               kernel[PPING_KERNEL_NAME_LEN];  /* kernel 名(cuFuncGetName,截断) */
    char               stack[768];               /* 符号化后的 native 栈(top 帧," <- " 连接) */
} PpingLaunchRow;

/* 拉走 per-kernel launch 栈(snapshot-swap)。仅 PPING_LANG_PCS_LAUNCH_STACK=1 时有数据。
 * 最多写 max_rows 行;返回写入行数(>=0),负数表错误。 */
int pping_pcs_drain_launches(PpingLaunchRow* out, int max_rows);

/* #1 launch 配置:一条 per-kernel 行 —— 最近一次 launch 的 grid/block/动态 smem(launch
 * 参数,记 latest,逐 launch 可能不同)+ 首见查一次的函数属性(regs/静态 smem/local/
 * maxThreads;-1 = 未取到)。
 * 默认常驻采集(launch 回调增量成本 ~ns 级内存读),PPING_LANG_PCS_LAUNCH_CONFIG=0 关。
 * cudagraph 语义:稳态图回放走 cuGraphLaunch,不触发 per-kernel launch 回调;图 capture
 * 期间会真实调 launch API,故配置来自 capture 期(稳态回放即按此执行),此时本窗
 * launches 可能为 0,但配置仍有效。 */
typedef struct {
    unsigned long long launches;                 /* 本批该 kernel 的 launch 次数(与上次 drain 的 delta) */
    unsigned int       grid_x, grid_y, grid_z;   /* 最近一次 launch 的 grid dim */
    unsigned int       block_x, block_y, block_z;/* 最近一次 launch 的 block dim */
    unsigned int       dyn_smem;                 /* 动态 shared mem 字节(launch 参数,latest) */
    int                regs;                     /* 每线程寄存器数(函数属性,首见查一次) */
    int                static_smem;              /* 静态 shared mem 字节 */
    int                local_mem;                /* local memory 字节 */
    int                max_threads_per_block;
    char               kernel[PPING_KERNEL_NAME_LEN];  /* kernel 名(cuFuncGetName/cuKernelGetName,截断) */
} PpingLaunchCfgRow;

/* 只开 launch 采集(配置 + 可选栈),不起 PC Sampling —— 与 PCS 会话互不依赖(PCS 被
 * 其他进程独占时也能用)。幂等。返回 0 成功。 */
int pping_pcs_launch_cfg_start(void);

/* 拉 launch 配置表(非 swap:条目常驻 —— cudagraph 稳态回放期间没有新 launch 回调,
 * swap 会让 capture 期拿到的配置第二窗消失;函数属性也借此只查一次)。launches 报与上次
 * drain 的 delta;无新 launch 的 kernel 也会出行(launches=0,配置为 capture/历史值)。
 * 最多写 max_rows 行;返回写入行数(>=0),负数表错误。 */
int pping_pcs_drain_launch_cfg(PpingLaunchCfgRow* out, int max_rows);

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
