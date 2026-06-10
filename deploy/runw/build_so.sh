#!/usr/bin/env bash
# 在 vLLM 容器里编 libppingcupti.so —— 自动探测 cu12/cu13 布局(换镜像免改)。
# 产物 /tmp/libppingcupti.so + 把运行期库路径写进 /tmp/pping_env.sh(launch 时 source)。
set -u
NAT=/work/native/ppingcupti

# cupti 动态库(libcupti.so.12 或 .13)+ 其所在目录
CUPTI_LIB=$(find /usr/local/lib/python3*/dist-packages/nvidia -name 'libcupti.so.1*' 2>/dev/null | head -1)
[ -z "$CUPTI_LIB" ] && CUPTI_LIB=$(find /usr/local/cuda* -name 'libcupti.so.1*' 2>/dev/null | head -1)
[ -z "$CUPTI_LIB" ] && { echo "FATAL: 找不到 libcupti.so.1*"; exit 2; }
CUPTI_LIBDIR=$(dirname "$CUPTI_LIB"); CUPTI_SONAME=$(basename "$CUPTI_LIB")

# cupti 头(含 cupti_pcsampling.h)
CUPTI_H=$(find /usr/local/lib/python3*/dist-packages/nvidia /usr/local/cuda* -name cupti_pcsampling.h 2>/dev/null | head -1)
[ -z "$CUPTI_H" ] && { echo "FATAL: 找不到 cupti_pcsampling.h"; exit 2; }
CUPTI_INC=$(dirname "$CUPTI_H")

# cuda.h(优先 cu13/cuda_runtime/cuda_cupti/triton 自带)
CUDA_H=$(find /usr/local/lib/python3*/dist-packages /usr/local/cuda* -name cuda.h 2>/dev/null \
         | grep -iE "cu13/include|cuda_runtime|cuda_cupti|triton/backends/nvidia/include|cuda/include" | head -1)
[ -z "$CUDA_H" ] && CUDA_H=$(find / -name cuda.h 2>/dev/null | head -1)
[ -z "$CUDA_H" ] && { echo "FATAL: 找不到 cuda.h"; exit 2; }
CUDA_INC=$(dirname "$CUDA_H")

# libcuda(链接用):优先 stub,否则真 .so.1
STUB=$(find / -name libcuda.so 2>/dev/null | grep -i stub | head -1)
if [ -n "$STUB" ]; then LCUDA="-L$(dirname "$STUB") -lcuda";
else REAL=$(find / -name 'libcuda.so.1' 2>/dev/null | head -1); LCUDA="-L$(dirname "$REAL") -l:libcuda.so.1"; fi

echo "[build] cupti=$CUPTI_SONAME @ $CUPTI_LIBDIR"
echo "[build] cupti_inc=$CUPTI_INC | cuda.h=$CUDA_INC | $LCUDA"
g++ -O2 -fPIC -std=c++17 -I"$CUPTI_INC" -I"$CUDA_INC" -I"$NAT" -shared \
    -L"$CUPTI_LIBDIR" -Wl,-rpath,"$CUPTI_LIBDIR" \
    -o /tmp/libppingcupti.so "$NAT/ppingcupti.cpp" -l:"$CUPTI_SONAME" $LCUDA -pthread
RC=$?
if [ $RC -ne 0 ]; then echo "[build] FAILED rc=$RC"; exit $RC; fi
# 运行期库路径(launch 时 source,确保注入的 .so 找得到 libcupti)
echo "export LD_LIBRARY_PATH=$CUPTI_LIBDIR:\${LD_LIBRARY_PATH:-}" > /tmp/pping_env.sh
echo "[build] OK -> $(ls -la /tmp/libppingcupti.so | awk '{print $5" bytes"}')"
