"""Tests for step-by-step run tracing."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.services import trace as trace_module


@pytest.fixture(autouse=True)
def _isolated_trace_state(monkeypatch: pytest.MonkeyPatch):
    """Reset the module-level trace state around each test."""
    monkeypatch.delenv(trace_module.ENV_ENABLED, raising=False)
    monkeypatch.delenv(trace_module.ENV_FILE, raising=False)
    monkeypatch.setattr(trace_module, "_resolved", False)
    monkeypatch.setattr(trace_module, "_enabled", False)
    monkeypatch.setattr(trace_module, "_mirror_stderr", False)
    monkeypatch.setattr(trace_module, "_handle", None)
    monkeypatch.setattr(trace_module, "_path", None)
    yield
    handle = trace_module._handle
    if handle is not None:
        handle.close()
    trace_module._handle = None
    trace_module._path = None
    trace_module._resolved = False
    trace_module._enabled = False


def test_disabled_by_default_writes_nothing(tmp_path: Path) -> None:
    log = tmp_path / "trace.log"
    trace_module.trace("step.one", "hello", path=str(log))
    assert trace_module.is_enabled() is False
    assert trace_module.trace_path() is None
    assert not log.exists()


def test_configure_enables_and_exports_env(tmp_path: Path) -> None:
    log = tmp_path / "trace.log"
    resolved = trace_module.configure(path=log)

    assert resolved == log
    assert trace_module.is_enabled() is True
    # Child processes (the frontend-spawned backend) inherit these.
    import os

    assert os.environ[trace_module.ENV_ENABLED] == "1"
    assert os.environ[trace_module.ENV_FILE] == str(log)


def test_trace_writes_step_message_and_fields(tmp_path: Path) -> None:
    log = tmp_path / "trace.log"
    trace_module.configure(path=log)

    trace_module.trace("query.turn.start", "entering the loop", turn=2, tool="bash")

    line = log.read_text(encoding="utf-8").strip()
    assert "query.turn.start" in line
    assert "entering the loop" in line
    assert "turn=2" in line
    assert "tool=bash" in line


def test_trace_collapses_multiline_values_to_one_line(tmp_path: Path) -> None:
    log = tmp_path / "trace.log"
    trace_module.configure(path=log)

    trace_module.trace("tool.done", output="first line\nsecond line")

    contents = log.read_text(encoding="utf-8")
    assert contents.count("\n") == 1
    assert "first line second line" in contents


def test_trace_skips_none_fields_and_renders_bools(tmp_path: Path) -> None:
    log = tmp_path / "trace.log"
    trace_module.configure(path=log)

    trace_module.trace("tool.permission", allowed=False, path=None)

    line = log.read_text(encoding="utf-8")
    assert "allowed=false" in line
    assert "path=" not in line


def test_preview_truncates_long_values() -> None:
    assert trace_module.preview("x" * 500, limit=10).endswith("…")
    assert len(trace_module.preview("x" * 500, limit=10)) == 10


def test_trace_span_records_elapsed(tmp_path: Path) -> None:
    log = tmp_path / "trace.log"
    trace_module.configure(path=log)

    with trace_module.trace_span("tool.execute", tool="bash"):
        pass

    contents = log.read_text(encoding="utf-8")
    assert "tool.execute.start" in contents
    assert "tool.execute.done" in contents
    assert "elapsed_ms=" in contents


def test_trace_span_records_failure_and_reraises(tmp_path: Path) -> None:
    log = tmp_path / "trace.log"
    trace_module.configure(path=log)

    with pytest.raises(ValueError), trace_module.trace_span("tool.execute", tool="bash"):
        raise ValueError("boom")

    contents = log.read_text(encoding="utf-8")
    assert "tool.execute.failed" in contents
    assert "ValueError: boom" in contents


def test_trace_never_raises_on_bad_destination(tmp_path: Path) -> None:
    unwritable = tmp_path / "a-file"
    unwritable.write_text("not a directory", encoding="utf-8")
    trace_module.configure(path=unwritable / "nested" / "trace.log")

    # Must not raise even though the path cannot be created.
    trace_module.trace("step.one", "hello")


def test_env_var_alone_enables_tracing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "trace.log"
    monkeypatch.setenv(trace_module.ENV_ENABLED, "stderr")
    monkeypatch.setenv(trace_module.ENV_FILE, str(log))

    assert trace_module.is_enabled() is True
    trace_module.trace("backend.start", "booting")
    assert "backend.start" in log.read_text(encoding="utf-8")
