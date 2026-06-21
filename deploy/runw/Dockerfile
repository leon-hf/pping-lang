# pping-lang 烤进官方 vLLM 镜像 —— 按**用户安装方式**:把项目 pip install 成包(非 editable
# mount)。docker 与 k3s 两条部署路共用这个镜像。
#
#   docker build --build-arg BASE=vllm/vllm-openai:v0.21.0 -t pping-lang:dev -f deploy/runw/Dockerfile .
#
# 运行时 entrypoint 用 `pping-vllm serve ...` 覆盖(镜像默认 entrypoint 是 vllm serve)。
ARG BASE=vllm/vllm-openai:v0.21.0
FROM ${BASE}

# 只 COPY 打包必需文件(层缓存友好:改 src 才重装,不动则命中缓存)。
COPY pyproject.toml README.md LICENSE /opt/pping-build/
COPY src /opt/pping-build/src

# pip install 成包(hatchling 在构建期出 wheel 再装进 site-packages)→ 删源码:镜像里就是
# 一个装好的 `pping_lang` 包 + `pping-vllm` 控制台脚本 + `vllm.stat_logger_plugins` entry point。
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple /opt/pping-build \
 && rm -rf /opt/pping-build \
 && python3 -c "import importlib.metadata as m; eps=[e.name for e in m.entry_points(group='vllm.stat_logger_plugins')]; assert 'pping_lang' in eps, eps; print('[image] plugin entry point OK:', eps)" \
 && which pping-vllm
