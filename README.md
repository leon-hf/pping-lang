# pping-lang

> vLLM 性能诊断插件——把 vLLM 指标 + GPU 物理层数据喂给规则引擎，自动告诉你为什么慢。

**状态**：Pre-alpha (`v0.0.1.dev0`)，目标 v0.1 在 3 周内发布。

## 它能做什么（v0.1）

开 vLLM 不改任何参数，3 秒后浏览器告诉你 GPU 在补 0、MFU 只有 18%、改 `max_num_seqs` 应该到 384。

- 实时 dashboard：GPU/KV cache/TTFT/MFU/CUDA graph padding ratio
- 11 条内置诊断规则，触发即给可执行建议
- 单 HTML 报告（含基础 Roofline 图），邮件可分享
- OTel 输出，兼容 Langfuse / Jaeger / Datadog

## 装

```bash
pip install pping-lang
vllm serve <your-model>
# 启动日志会打印 dashboard URL（默认 http://localhost:8765）
```

## 文档

- [设计文档 v0.2.1](docs/pping-lang-design-v0.2.md)
- [Pre-implementation RFC](docs/pping-lang-pre-impl-rfc.md)

## 开发

```bash
pip install -e ".[dev]"
pytest
```

## License

Apache 2.0
