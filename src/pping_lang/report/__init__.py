"""报告生成 — 单 HTML 文件，自包含可邮件分享 (Day 10)。

子模块：
- generator: build_report(db_path, ...) → HTML 字符串
- analysis:  executive summary / config audit / roofline data 提取
- templates/report.html.j2: Jinja2 模板
"""
from pping_lang.report.generator import generate_report

__all__ = ["generate_report"]
