"""HTTP API server — Day 6 实现。

子模块：
- routes:  FastAPI app + 核心端点 (health, metrics, diagnoses, rules)
- server:  ApiServer — uvicorn 在 daemon 线程跑
- queries: DuckDB 查询辅助
"""
from pping_lang.api.routes import build_app
from pping_lang.api.server import ApiServer

__all__ = ["ApiServer", "build_app"]
