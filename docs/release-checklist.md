# Release Checklist

发新版前走一遍。

## Pre-flight

- [ ] `pytest -q` 全绿（209+ tests）
- [ ] `python -m pping_lang.bench.microbench` 数字在预算内
  - push_metric < 5μs mean
  - collect < 100μs mean
  - record < 50μs mean
- [ ] `pytest tests/test_perf.py` 性能 regression 通过
- [ ] CHANGELOG.md 加 `[X.Y.Z]` 节，移走 `[Unreleased]` 内容
- [ ] `pyproject.toml` 和 `pping_lang/__init__.py` 的版本号一致
- [ ] 实地浏览器验：
  - [ ] dashboard 实时 tab 渲染（KPI + chart + 诊断卡片）
  - [ ] dashboard 规则 tab CRUD + test 按钮
  - [ ] /api/report 生成的 HTML 6 节齐全

## Build

```bash
rm -rf dist/
python -m build           # → dist/pping_lang-X.Y.Z-py3-none-any.whl + .tar.gz
```

验证 wheel 内容：

```bash
python -c "
import zipfile
z = zipfile.ZipFile('dist/pping_lang-X.Y.Z-py3-none-any.whl')
names = z.namelist()
assert any('ui/index.html' in n for n in names), 'UI missing'
assert any('templates/report.html.j2' in n for n in names), 'template missing'
assert any('entry_points.txt' in n for n in names), 'entry point missing'
print(f'OK: {len(names)} entries')
"
```

## Smoke test in fresh venv

```bash
python -m venv /tmp/pping-test && \
  /tmp/pping-test/bin/python -m pip install dist/pping_lang-X.Y.Z-py3-none-any.whl && \
  /tmp/pping-test/bin/python -c "
from pping_lang import PpingLangStatLogger
import importlib.metadata
eps = importlib.metadata.entry_points(group='vllm.stat_logger_plugins')
assert any(ep.name == 'pping_lang' for ep in eps)
print('install OK, plugin discoverable')
"
```

## PyPI upload

⚠️ 需要 PyPI account + token。**此步骤手动**，不要在 CI 自动跑。

```bash
# Test PyPI first (recommended)
python -m twine upload --repository testpypi dist/*
# Verify install from test
pip install --index-url https://test.pypi.org/simple/ pping-lang

# Real PyPI
python -m twine upload dist/*
```

## Git tag

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

## Post-release

- [ ] GitHub release 含 CHANGELOG 该节
- [ ] 更新 README "状态"行（去掉 pre-alpha tag）
- [ ] 写 `[Unreleased]` 节占位下一版

## v0.1 alpha-1 specific (one-time)

- [ ] 在 HN / Twitter / r/LocalLLaMA 发 launch 帖
  - 链接: case-study-1-padding.md（marquee 卖点）
  - demo gif（dashboard + 终端诊断输出）
- [ ] 在 vLLM Discussions 开 thread
