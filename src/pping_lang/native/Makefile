# libppingcupti 构建 —— 在 WSL/Linux 上用 g++ + cu13 CUPTI 头/库构建。
# 用法:  make            # 产 libppingcupti.so(自动从 venv 找 cu13)
#        make VENV=/path/to/venv
#        make clean
#
# 不需要 nvcc:PC sampling host 代码纯 C++ 链 libcupti.so.13 + libcuda。

VENV    ?= $(HOME)/pping-cupti
CU13    := $(firstword $(wildcard $(VENV)/lib/python*/site-packages/nvidia/cu13))
INC     := $(CU13)/include
LIB     := $(CU13)/lib
WSL_LIB := /usr/lib/wsl/lib

CXX      ?= g++
CXXFLAGS := -O2 -fPIC -std=c++17 -Wall -I$(INC)
# RPATH 让 .so 运行时自己找到 cu13 libcupti + WSL libcuda(免调用方设 LD_LIBRARY_PATH)
LDFLAGS  := -shared -L$(LIB) -L$(WSL_LIB) -Wl,-rpath,$(LIB) -Wl,-rpath,$(WSL_LIB)
LIBS     := -l:libcupti.so.13 -lcuda -pthread

TARGET := libppingcupti.so

$(TARGET): ppingcupti.cpp ppingcupti.h
	@test -n "$(CU13)" || { echo "FATAL: cu13 not found under $(VENV)"; exit 1; }
	@echo "cu13 = $(CU13)"
	$(CXX) $(CXXFLAGS) $(LDFLAGS) -o $@ ppingcupti.cpp $(LIBS)
	@echo "built $(TARGET)"

clean:
	rm -f $(TARGET)

.PHONY: clean
