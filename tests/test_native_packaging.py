"""builder.py(.so 现编)+ cli.py(pping-vllm 包装)单测。

实际 g++ 编译要 GPU/CUPTI 环境(由 runw 端到端覆盖);这里测不依赖 GPU 的逻辑:
缓存路径、显式 .so 路径、源缺失报错、cli 设 env + exec、cli 降级。
"""
from __future__ import annotations

import os

import pytest

from pping_lang import cli
from pping_lang.native import builder
from pping_lang.native.builder import BuildError, ensure_so


def test_cache_so_respects_home(monkeypatch, tmp_path):
    monkeypatch.setenv("PPING_LANG_HOME", str(tmp_path))
    p = builder._cache_so()
    assert p == tmp_path / "lib" / "libppingcupti.so"


def test_ensure_so_explicit_path_skips_build(monkeypatch, tmp_path):
    """PPING_LANG_PCS_SO 指向已存在文件 → 直接返回,不触发 _detect/编译。"""
    so = tmp_path / "libppingcupti.so"
    so.write_bytes(b"\x7fELF stub")
    monkeypatch.setenv("PPING_LANG_PCS_SO", str(so))
    # _detect 失败也无所谓(显式路径已命中);确保不抛
    monkeypatch.setattr(builder, "_detect", lambda: (_ for _ in ()).throw(BuildError("no cupti")))
    path, libdir = ensure_so()
    assert path == str(so)
    assert libdir == ""  # _detect 失败 → libdir 空,但仍返回


def test_ensure_so_raises_when_source_missing(monkeypatch, tmp_path):
    """无显式 .so 且打包源码缺失 → BuildError(不依赖 GPU)。"""
    monkeypatch.delenv("PPING_LANG_PCS_SO", raising=False)
    monkeypatch.setattr(builder, "_CPP", tmp_path / "nonexistent.cpp")
    with pytest.raises(BuildError, match="源码缺失"):
        ensure_so()


def test_glob1_returns_first_or_none():
    assert builder._glob1(["/definitely/nope/*.xyz"]) is None
    # 当前文件目录里一定有 .py
    here = os.path.dirname(builder.__file__)
    hit = builder._glob1([os.path.join(here, "*.py")])
    assert hit and hit.endswith(".py")


def test_cli_sets_env_and_execs(monkeypatch, tmp_path):
    captured: dict = {}
    fake_vllm = tmp_path / "vllm"
    fake_vllm.write_text("#!/bin/sh\n")
    monkeypatch.setattr("pping_lang.native.builder.ensure_so",
                        lambda *a, **k: ("/fake/libppingcupti.so", "/fake/cupti/lib"))
    monkeypatch.setattr(cli.shutil, "which", lambda _name: str(fake_vllm))
    monkeypatch.setattr(cli.os, "execv", lambda path, argv: captured.update(path=path, argv=argv))
    monkeypatch.setattr(cli.sys, "argv", ["pping-vllm", "serve", "my-model", "--port", "8000"])
    # cli 用 os.environ.setdefault 真改 env;快照 + finally 还原,绝不泄漏给后续 test
    _keys = {"CUDA_INJECTION64_PATH", "PPING_LANG_PCS_SO", "PPING_LANG_ENABLE_PCS", "LD_LIBRARY_PATH"}
    _snap = dict(os.environ)
    for k in _keys:
        os.environ.pop(k, None)
    try:
        cli.pping_vllm_main()
        assert os.environ["CUDA_INJECTION64_PATH"] == "/fake/libppingcupti.so"
        assert os.environ["PPING_LANG_PCS_SO"] == "/fake/libppingcupti.so"
        assert os.environ["PPING_LANG_ENABLE_PCS"] == "1"
        assert "/fake/cupti/lib" in os.environ["LD_LIBRARY_PATH"]
        assert captured["path"] == str(fake_vllm)
        assert captured["argv"] == [str(fake_vllm), "serve", "my-model", "--port", "8000"]
    finally:
        os.environ.clear()
        os.environ.update(_snap)


def test_cli_degrades_when_build_fails(monkeypatch, tmp_path):
    """ensure_so 抛错 → 不崩,仍 exec vllm,且不设注入 env(降级)。"""
    captured: dict = {}
    fake_vllm = tmp_path / "vllm"
    fake_vllm.write_text("#!/bin/sh\n")

    def _boom(*_a, **_k):
        raise BuildError("g++ 没装")

    monkeypatch.setattr("pping_lang.native.builder.ensure_so", _boom)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: str(fake_vllm))
    monkeypatch.setattr(cli.os, "execv", lambda path, argv: captured.update(path=path, argv=argv))
    monkeypatch.setattr(cli.sys, "argv", ["pping-vllm", "serve", "m"])
    _snap = dict(os.environ)
    os.environ.pop("CUDA_INJECTION64_PATH", None)
    try:
        cli.pping_vllm_main()
        assert captured["argv"] == [str(fake_vllm), "serve", "m"]
        assert "CUDA_INJECTION64_PATH" not in os.environ  # 降级:没设注入
    finally:
        os.environ.clear()
        os.environ.update(_snap)


def test_cli_errors_without_vllm(monkeypatch, tmp_path):
    monkeypatch.setattr("pping_lang.native.builder.ensure_so",
                        lambda *a, **k: ("/fake/so", "/fake/lib"))
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    # which 返 None 时回退到 dirname(sys.executable)/vllm;指到无 vllm 的临时目录
    monkeypatch.setattr(cli.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(cli.sys, "argv", ["pping-vllm", "serve", "m"])
    _snap = dict(os.environ)  # pping_vllm_main 在 raise 前已 setdefault 改 env,还原防泄漏
    try:
        with pytest.raises(SystemExit, match="找不到 vllm"):
            cli.pping_vllm_main()
    finally:
        os.environ.clear()
        os.environ.update(_snap)
