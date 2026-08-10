from __future__ import annotations

import inspect
import json
import os
import random
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb


LOCK_RETRY_DELAYS_SECONDS = (0.15, 0.40, 1.00)
LOCK_ERROR_MARKERS = (
    "could not set lock",
    "conflicting lock",
    "database is locked",
    "database lock",
    "file is already open in",
    "unique file handle conflict",
    "another process is using this file",
    "\u53e6\u4e00\u4e2a\u7a0b\u5e8f\u6b63\u5728\u4f7f\u7528\u6b64\u6587\u4ef6",
)
_CONNECT_GATES_LOCK = threading.Lock()
_CONNECT_GATES: dict[str, threading.Lock] = {}
_LOG_WRITE_LOCK = threading.Lock()


def _connect_gate(path: Path) -> threading.Lock:
    key = str(path.resolve()).casefold()
    with _CONNECT_GATES_LOCK:
        return _CONNECT_GATES.setdefault(key, threading.Lock())




class DatabaseOwnershipError(RuntimeError):
    pass


def _main_module() -> str:
    module = sys.modules.get("__main__")
    spec = getattr(module, "__spec__", None)
    return str(getattr(spec, "name", None) or Path(sys.argv[0]).name)


def _caller() -> str:
    for frame in inspect.stack()[2:]:
        filename = Path(frame.filename)
        if filename.name not in {"db.py", "connection_manager.py"}:
            return f"{filename.as_posix()}:{frame.lineno}:{frame.function}"
    return "unknown"


def _is_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def _is_main_database(path: Path, configured_path: Path) -> bool:
    try:
        return path.resolve() == configured_path.resolve()
    except OSError:
        return path.absolute() == configured_path.absolute()


def _assert_owner(path: Path, configured_path: Path) -> None:
    if not _is_main_database(path, configured_path) or _is_pytest():
        return
    if os.environ.get("HERMES_DB_ENFORCE_OWNER", "1") != "1":
        return
    if os.environ.get("HERMES_DB_OWNER_PROCESS") == "router":
        return
    if os.environ.get("HERMES_DB_MAINTENANCE") == "1":
        return
    raise DatabaseOwnershipError(
        "主DuckDB仅允许Hermes-OPC Router进程直接访问；"
        "在线调用请使用本地Router API。离线维护必须先停止Router，"
        "并显式设置HERMES_DB_MAINTENANCE=1。"
    )


def _diagnostics_enabled() -> bool:
    value = os.environ.get("HERMES_DB_DIAGNOSTICS")
    if value is not None:
        return value.strip().lower() not in {"0", "false", "off", "no"}
    return not _is_pytest()


def _log_path(configured_path: Path) -> Path:
    return configured_path.resolve().parents[1] / "logs" / "database-access.jsonl"


def _write_log(configured_path: Path, payload: dict[str, Any]) -> None:
    if not _diagnostics_enabled():
        return
    target = _log_path(configured_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, default=str)
    with _LOG_WRITE_LOCK:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _is_retryable_lock_error(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in LOCK_ERROR_MARKERS)


def _lock_holder_pid(exc: BaseException) -> int | None:
    message = str(exc)
    match = re.search(r"\bPID\s*[:=]?\s*(\d+)\b", message, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _process_image(pid: int | None) -> str | None:
    if not pid:
        return None
    if os.name != "nt":
        try:
            return os.readlink(f"/proc/{pid}/exe")
        except OSError:
            return None
    try:
        import ctypes
        from ctypes import wintypes

        query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(query_limited_information, False, pid)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        return None
    return None


class ManagedDuckDBConnection:
    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        *,
        database_path: Path,
        configured_path: Path,
        read_only: bool,
        caller: str,
        opened_at: datetime,
        started: float,
    ) -> None:
        self._connection = connection
        self._database_path = database_path
        self._configured_path = configured_path
        self._read_only = read_only
        self._caller = caller
        self._opened_at = opened_at
        self._started = started
        self._closed = False
        self._operation_types: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __enter__(self) -> ManagedDuckDBConnection:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc is not None:
            try:
                self._connection.rollback()
            except Exception:
                pass
        self.close()

    def execute(self, query: str, parameters: Any = None) -> ManagedDuckDBConnection:
        operation = query.lstrip().split(None, 1)[0].upper() if query.strip() else "UNKNOWN"
        self._operation_types.add(operation)
        if parameters is None:
            self._connection.execute(query)
        else:
            self._connection.execute(query, parameters)
        return self

    def executemany(self, query: str, parameters: Any) -> ManagedDuckDBConnection:
        operation = query.lstrip().split(None, 1)[0].upper() if query.strip() else "UNKNOWN"
        self._operation_types.add(operation)
        self._connection.executemany(query, parameters)
        return self

    def close(self) -> None:
        if self._closed:
            return
        # DuckDB may detach the last file handle while another worker thread is
        # opening the same database. Serialize only attach/detach operations;
        # query execution and transactions remain concurrent.
        with _connect_gate(self._database_path):
            if self._closed:
                return
            self._connection.close()
            self._closed = True
        closed_at = datetime.now().astimezone()
        _write_log(
            self._configured_path,
            {
                "event": "database_close",
                "timestamp": closed_at.isoformat(),
                "pid": os.getpid(),
                "process_name": _main_module(),
                "thread_id": threading.get_ident(),
                "profile": os.environ.get("HERMES_PROFILE", "default"),
                "caller": self._caller,
                "database_path": str(self._database_path.resolve()),
                "access_mode": "READ_ONLY" if self._read_only else "READ_WRITE",
                "operation_type": ",".join(sorted(self._operation_types)) or "NONE",
                "opened_at": self._opened_at.isoformat(),
                "closed_at": closed_at.isoformat(),
                "elapsed_ms": round((time.perf_counter() - self._started) * 1000, 3),
            },
        )


def connect_database(
    database_path: Path,
    *,
    configured_path: Path,
    read_only: bool = False,
) -> ManagedDuckDBConnection:
    path = Path(database_path)
    _assert_owner(path, configured_path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    caller = _caller()
    opened_at = datetime.now().astimezone()
    started = time.perf_counter()
    retries = 0
    gate_wait_ms = 0.0
    duckdb_connect_ms = 0.0
    while True:
        try:
            gate_started = time.perf_counter()
            with _connect_gate(path):
                gate_wait_ms += (time.perf_counter() - gate_started) * 1000
                connect_started = time.perf_counter()
                try:
                    connection = duckdb.connect(str(path), read_only=read_only)
                finally:
                    duckdb_connect_ms += (
                        time.perf_counter() - connect_started
                    ) * 1000
            break
        except Exception as exc:
            holder_pid = _lock_holder_pid(exc)
            holder_process = _process_image(holder_pid)
            if not _is_retryable_lock_error(exc) or retries >= len(LOCK_RETRY_DELAYS_SECONDS):
                if _is_retryable_lock_error(exc):
                    _write_log(
                        configured_path,
                        {
                            "event": "database_lock_failure",
                            "timestamp": datetime.now().astimezone().isoformat(),
                            "pid": os.getpid(),
                            "process_name": _main_module(),
                            "thread_id": threading.get_ident(),
                            "profile": os.environ.get("HERMES_PROFILE", "default"),
                            "caller": caller,
                            "database_path": str(path.resolve()),
                            "access_mode": "READ_ONLY" if read_only else "READ_WRITE",
                            "operation_type": "OPEN",
                            "retry_count": retries,
                            "waited_ms": round(
                                (time.perf_counter() - started) * 1000, 3
                            ),
                            "gate_wait_ms": round(gate_wait_ms, 3),
                            "duckdb_connect_ms": round(duckdb_connect_ms, 3),
                            "lock_wait_location": caller,
                            "lock_holder_pid": holder_pid,
                            "lock_holder_process": holder_process,
                            "lock_error": str(exc),
                        },
                    )
                    raise RuntimeError(
                        "数据库暂时繁忙，已自动重试3次仍未恢复。"
                    ) from exc
                raise
            delay = LOCK_RETRY_DELAYS_SECONDS[retries]
            retries += 1
            _write_log(
                configured_path,
                {
                    "event": "database_lock_retry",
                    "timestamp": datetime.now().astimezone().isoformat(),
                    "pid": os.getpid(),
                    "process_name": _main_module(),
                    "thread_id": threading.get_ident(),
                    "profile": os.environ.get("HERMES_PROFILE", "default"),
                    "caller": caller,
                    "database_path": str(path.resolve()),
                    "access_mode": "READ_ONLY" if read_only else "READ_WRITE",
                    "operation_type": "OPEN",
                    "retry_count": retries,
                    "waited_ms": round(
                        (time.perf_counter() - started) * 1000, 3
                    ),
                    "gate_wait_ms": round(gate_wait_ms, 3),
                    "duckdb_connect_ms": round(duckdb_connect_ms, 3),
                    "retry_delay_seconds": round(delay, 3),
                    "lock_wait_location": caller,
                    "lock_holder_pid": holder_pid,
                    "lock_holder_process": holder_process,
                    "lock_error": str(exc),
                },
            )
            time.sleep(delay + random.uniform(0.0, delay * 0.20))
    _write_log(
        configured_path,
        {
            "event": "database_open",
            "timestamp": opened_at.isoformat(),
            "pid": os.getpid(),
            "process_name": _main_module(),
            "thread_id": threading.get_ident(),
            "profile": os.environ.get("HERMES_PROFILE", "default"),
            "caller": caller,
            "database_path": str(path.resolve()),
            "access_mode": "READ_ONLY" if read_only else "READ_WRITE",
            "operation_type": "OPEN",
            "opened_at": opened_at.isoformat(),
            "closed_at": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "retry_count": retries,
            "waited_ms": round((time.perf_counter() - started) * 1000, 3),
            "gate_wait_ms": round(gate_wait_ms, 3),
            "duckdb_connect_ms": round(duckdb_connect_ms, 3),
            "lock_wait_location": caller,
        },
    )
    return ManagedDuckDBConnection(
        connection,
        database_path=path,
        configured_path=configured_path,
        read_only=read_only,
        caller=caller,
        opened_at=opened_at,
        started=started,
    )


__all__ = [
    "DatabaseOwnershipError",
    "ManagedDuckDBConnection",
    "connect_database",
]
