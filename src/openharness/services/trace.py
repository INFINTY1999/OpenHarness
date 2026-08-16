"""Step-by-step runtime tracing for an ``oh`` session.

The default UI is a React/Ink frontend that owns the terminal, and the Python
backend talks to it over JSON-lines on stdout.  A bare ``print()`` in the
backend would corrupt that protocol, so tracing is written to a log file
instead (optionally mirrored to stderr for ``-p``/headless runs).

Enable it with ``oh --trace``, or by exporting ``OPENHARNESS_TRACE=1``
(``OPENHARNESS_TRACE=stderr`` also mirrors to stderr).  ``--trace`` exports the
env vars, so the frontend-spawned backend subprocess traces into the same file.

Usage::

    from openharness.services.trace import trace

    trace("query.turn.start", turn=3, model="claude-sonnet-4-6")
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

ENV_ENABLED = "OPENHARNESS_TRACE"
ENV_FILE = "OPENHARNESS_TRACE_FILE"

_TRUTHY = {"1", "true", "yes", "on", "file"}
_STDERR_VALUES = {"stderr", "both", "2"}

_MAX_VALUE_CHARS = 220

_lock = threading.Lock()
_resolved = False
_enabled = False
_mirror_stderr = False
_handle: TextIO | None = None
_path: Path | None = None
_role: str = ""
_start = time.monotonic()


def _default_path() -> Path:
    from openharness.config.paths import get_logs_dir

    return get_logs_dir() / f"trace-{time.strftime('%Y%m%d')}.log"


def _detect_role() -> str:
    """Label the process so parent CLI and backend lines are distinguishable."""
    argv = sys.argv[1:]
    if "--backend-only" in argv:
        return "backend"
    if "--task-worker" in argv:
        return "worker"
    if "-p" in argv or "--print" in argv:
        return "print"
    return "cli"


def _resolve() -> None:
    global _resolved, _enabled, _mirror_stderr, _role
    if _resolved:
        return
    _resolved = True
    raw = (os.environ.get(ENV_ENABLED) or "").strip().lower()
    _enabled = raw in _TRUTHY or raw in _STDERR_VALUES
    _mirror_stderr = raw in _STDERR_VALUES
    _role = _detect_role()


def is_enabled() -> bool:
    """Return True when tracing is turned on for this process."""
    _resolve()
    return _enabled


def trace_path() -> Path | None:
    """Return the trace log path, or None when tracing is off."""
    if not is_enabled():
        return None
    return _open_handle_path()


def configure(
    *,
    enabled: bool = True,
    path: str | Path | None = None,
    mirror_stderr: bool = False,
) -> Path | None:
    """Turn tracing on for this process and export it to child processes.

    The env vars are set so the React frontend (which copies ``os.environ``)
    hands the same configuration to the backend subprocess it spawns.
    """
    global _resolved, _enabled, _mirror_stderr, _handle, _path, _role
    with _lock:
        _resolved = True
        _enabled = enabled
        _mirror_stderr = mirror_stderr
        _role = _detect_role()
        _handle = None
        if not enabled:
            os.environ.pop(ENV_ENABLED, None)
            return None
        os.environ[ENV_ENABLED] = "stderr" if mirror_stderr else "1"
        if path is not None:
            _path = Path(path).expanduser()
        elif os.environ.get(ENV_FILE):
            _path = Path(os.environ[ENV_FILE]).expanduser()
        else:
            _path = _default_path()
        os.environ[ENV_FILE] = str(_path)
    return _path


def _open_handle_path() -> Path | None:
    """Return (opening if needed) the append handle's path."""
    global _handle, _path
    if _handle is not None and _path is not None:
        return _path
    with _lock:
        if _handle is not None:
            return _path
        try:
            if _path is None:
                env_path = os.environ.get(ENV_FILE)
                _path = Path(env_path).expanduser() if env_path else _default_path()
            _path.parent.mkdir(parents=True, exist_ok=True)
            _handle = _path.open("a", encoding="utf-8", buffering=1)
        except Exception:  # pragma: no cover - tracing must never break a session
            _handle = None
            return None
    return _path


def preview(value: Any, limit: int = _MAX_VALUE_CHARS) -> str:
    """Collapse any value to a single short line safe for one trace field."""
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _format_fields(fields: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = f"{value}"
        else:
            rendered = preview(value)
            if " " in rendered or not rendered:
                rendered = f"'{rendered}'"
        parts.append(f"{key}={rendered}")
    return " ".join(parts)


def trace(step: str, message: str = "", **fields: Any) -> None:
    """Write one trace line describing a step in the run.

    No-op (and never raises) when tracing is disabled.
    """
    if not is_enabled():
        return
    try:
        stamp = time.strftime("%H:%M:%S")
        millis = int((time.time() % 1) * 1000)
        elapsed = time.monotonic() - _start
        head = f"{stamp}.{millis:03d} +{elapsed:7.3f}s [{_role}:{os.getpid()}] {step}"
        tail = " ".join(part for part in (preview(message, 400), _format_fields(fields)) if part)
        line = f"{head} {tail}\n" if tail else f"{head}\n"

        _open_handle_path()
        if _handle is not None:
            _handle.write(line)
        if _mirror_stderr:
            sys.stderr.write(line)
            sys.stderr.flush()
    except Exception:  # pragma: no cover - tracing must never break a session
        pass


class trace_span:
    """Context manager tracing ``<step>.start`` / ``<step>.done`` with a duration.

    ::

        with trace_span("tool.execute", tool="bash"):
            ...
    """

    def __init__(self, step: str, **fields: Any) -> None:
        self._step = step
        self._fields = fields
        self._t0 = 0.0

    def __enter__(self) -> trace_span:
        self._t0 = time.monotonic()
        trace(f"{self._step}.start", **self._fields)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed_ms = round((time.monotonic() - self._t0) * 1000, 1)
        if exc is None:
            trace(f"{self._step}.done", elapsed_ms=elapsed_ms, **self._fields)
        else:
            trace(
                f"{self._step}.failed",
                elapsed_ms=elapsed_ms,
                error=f"{type(exc).__name__}: {exc}",
                **self._fields,
            )
        return False


__all__ = [
    "ENV_ENABLED",
    "ENV_FILE",
    "configure",
    "is_enabled",
    "preview",
    "trace",
    "trace_path",
    "trace_span",
]
