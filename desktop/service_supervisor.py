from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import time
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from desktop.api_client import ApiResult, DesktopApiClient
from config.utf8 import (
    UTF8_ENCODING,
    UTF8_ERRORS,
    utf8_child_environment,
)
from desktop.paths import AppPaths


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    name: str
    module: str
    port: int
    health_path: str


@dataclass(frozen=True, slots=True)
class ServiceRecord:
    name: str
    pid: int
    executable: str
    started_at: str
    port: int
    module: str


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    name: str
    state: str
    pid: int | None = None
    port: int | None = None
    detail: str = ""


SPECS = {
    "router": ServiceSpec("router", "scripts.run_router_api", 8765, "/health"),
    "data_hub": ServiceSpec("data_hub", "scripts.run_data_api", 8766, "/health"),
}


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_identity(pid: int) -> tuple[str, datetime] | None:
    if os.name != "nt":
        return None
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
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        ticks = (creation.dwHighDateTime << 32) + creation.dwLowDateTime
        unix_seconds = ticks / 10_000_000 - 11_644_473_600
        return buffer.value, datetime.fromtimestamp(unix_seconds, timezone.utc)
    finally:
        kernel32.CloseHandle(handle)


def port_owner_pid(port: int) -> int | None:
    if os.name != "nt":
        return None
    netstat = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "netstat.exe"
    try:
        result = subprocess.run(
            [str(netstat), "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding=UTF8_ENCODING,
            errors=UTF8_ERRORS,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pattern = re.compile(
        rf"^\s*TCP\s+127\.0\.0\.1:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$",
        re.IGNORECASE,
    )
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if match:
            return int(match.group(1))
    return None


def terminate_verified_pid(pid: int, *, timeout: float) -> bool:
    if os.name != "nt":
        return False
    try:
        os.kill(pid, signal.CTRL_BREAK_EVENT)
    except OSError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return True
        time.sleep(0.1)
    return False


class ServiceSupervisor:
    def __init__(
        self,
        paths: AppPaths,
        *,
        api_client: DesktopApiClient | None = None,
        process_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        port_probe: Callable[[int], bool] = port_is_open,
        process_probe: Callable[[int], bool] = process_exists,
        identity_probe: Callable[[int], tuple[str, datetime] | None] = process_identity,
        port_owner_probe: Callable[[int], int | None] = port_owner_pid,
        pid_terminator: Callable[..., bool] = terminate_verified_pid,
    ) -> None:
        self.paths = paths
        self.api_client = api_client or DesktopApiClient()
        self.process_factory = process_factory
        self.port_probe = port_probe
        self.process_probe = process_probe
        self.identity_probe = identity_probe
        self.port_owner_probe = port_owner_probe
        self.pid_terminator = pid_terminator
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._state_file = paths.config_dir / "service-state.json"

    def _spec(self, service: str) -> ServiceSpec:
        try:
            configured = SPECS[service]
        except KeyError as error:
            raise ValueError("unsupported service") from error
        port = (
            self.api_client.router_url.rsplit(":", 1)[-1]
            if service == "router"
            else self.api_client.data_hub_url.rsplit(":", 1)[-1]
        )
        return ServiceSpec(
            configured.name,
            configured.module,
            int(port),
            configured.health_path,
        )

    def _records(self) -> dict[str, ServiceRecord]:
        if not self._state_file.is_file():
            return {}
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            return {
                name: ServiceRecord(**record)
                for name, record in payload.items()
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {}

    def _record_is_verified(self, record: ServiceRecord) -> bool:
        managed = self._processes.get(record.name)
        if managed is not None and managed.pid == record.pid:
            return True
        if not self._record_identity_matches(record):
            return False
        return self.port_owner_probe(record.port) == record.pid

    def _record_identity_matches(self, record: ServiceRecord) -> bool:
        identity = self.identity_probe(record.pid)
        if identity is None:
            return False
        actual_path, actual_started_at = identity
        try:
            recorded_started_at = datetime.fromisoformat(record.started_at)
        except ValueError:
            return False
        executable_matches = (
            str(Path(actual_path).resolve()).casefold()
            == str(Path(record.executable).resolve()).casefold()
        )
        start_matches = (
            abs((actual_started_at - recorded_started_at).total_seconds()) <= 5
        )
        return executable_matches and start_matches

    def _terminate_stale_record(
        self,
        record: ServiceRecord,
        *,
        timeout: float,
    ) -> bool:
        managed = self._processes.get(record.name)
        if managed is not None and managed.pid == record.pid:
            managed.terminate()
            try:
                managed.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                managed.kill()
                managed.wait(timeout=5)
            self._processes.pop(record.name, None)
            return True
        if not self._record_identity_matches(record):
            return False
        return self.pid_terminator(record.pid, timeout=timeout)

    def _save_records(self, records: dict[str, ServiceRecord]) -> None:
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        payload = {name: asdict(record) for name, record in records.items()}
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        for forbidden in ("TOKEN", "SECRET", "API_KEY", "PASSWORD"):
            if forbidden in serialized.upper():
                raise ValueError("credential material must not enter service state")
        temporary = self._state_file.with_suffix(".json.tmp")
        temporary.write_text(serialized + "\n", encoding="utf-8")
        temporary.replace(self._state_file)

    def status(self, service: str) -> ServiceStatus:
        if service == "finance_mcp":
            return ServiceStatus(
                name=service,
                state="ON_DEMAND",
                detail="stdio tool server starts only for an explicit request",
            )
        spec = self._spec(service)
        record = self._records().get(service)
        managed = self._processes.get(service)
        if managed is not None and managed.poll() is not None:
            return ServiceStatus(
                service,
                "CRASHED",
                managed.pid,
                spec.port,
                "managed service process exited",
            )
        if (
            record
            and self.process_probe(record.pid)
            and self._record_is_verified(record)
        ):
            return ServiceStatus(
                service,
                "RUNNING" if self.port_probe(spec.port) else "STARTING",
                record.pid,
                spec.port,
            )
        if self.port_probe(spec.port):
            return ServiceStatus(
                service,
                "PORT_CONFLICT",
                None,
                spec.port,
                "port is occupied by an unmanaged process",
            )
        return ServiceStatus(service, "STOPPED", port=spec.port)

    def start(
        self,
        service: str,
        *,
        child_environment: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> ServiceStatus:
        if service == "finance_mcp":
            return self.status(service)
        spec = self._spec(service)
        current = self.status(service)
        if current.state in {"RUNNING", "STARTING"}:
            return ServiceStatus(
                service,
                "ALREADY_RUNNING",
                current.pid,
                spec.port,
            )
        if current.state == "PORT_CONFLICT":
            return current
        stale_record = self._records().get(service)
        if (
            stale_record is not None
            and self.process_probe(stale_record.pid)
            and not self.port_probe(stale_record.port)
        ):
            if not self._terminate_stale_record(
                stale_record,
                timeout=min(timeout, 10.0),
            ):
                return ServiceStatus(
                    service,
                    "STALE_PROCESS",
                    stale_record.pid,
                    stale_record.port,
                    "verified stale service could not be stopped",
                )
            records = self._records()
            records.pop(service, None)
            self._save_records(records)
        self.paths.ensure_user_directories()
        stdout_path = self.paths.logs_dir / f"{service}.stdout.log"
        stderr_path = self.paths.logs_dir / f"{service}.stderr.log"
        python = (
            self.paths.application_root / ".venv" / "Scripts" / "python.exe"
            if self.paths.development
            else self.paths.executable_dir / "hermes-opc-service.exe"
        )
        command = (
            [str(python), "-m", spec.module]
            if self.paths.development
            else [str(python), "--service", service]
        )
        creation_flags = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0
        )
        with (
            stdout_path.open("a", encoding="utf-8") as stdout,
            stderr_path.open("a", encoding="utf-8") as stderr,
        ):
            process = self.process_factory(
                command,
                cwd=str(self.paths.application_root),
                env=utf8_child_environment(child_environment),
                stdout=stdout,
                stderr=stderr,
                text=True,
                encoding=UTF8_ENCODING,
                errors=UTF8_ERRORS,
                shell=False,
                creationflags=creation_flags,
            )
        self._processes[service] = process
        records = self._records()
        record = ServiceRecord(
            name=service,
            pid=process.pid,
            executable=str(python.resolve()),
            started_at=datetime.now(timezone.utc).isoformat(),
            port=spec.port,
            module=spec.module,
        )
        records[service] = record
        self._save_records(records)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return ServiceStatus(
                    service,
                    "FAILED",
                    process.pid,
                    spec.port,
                    "service exited during startup",
                )
            if self.port_probe(spec.port):
                return ServiceStatus(service, "RUNNING", process.pid, spec.port)
            time.sleep(0.1)
        return ServiceStatus(
            service,
            "NOT_READY",
            process.pid,
            spec.port,
            "startup health timeout",
        )

    def stop(self, service: str, *, timeout: float = 10.0) -> ServiceStatus:
        if service == "finance_mcp":
            return self.status(service)
        record = self._records().get(service)
        if record is None:
            return ServiceStatus(service, "STOPPED")
        process = self._processes.get(service)
        if not self._record_is_verified(record):
            return ServiceStatus(
                service,
                "UNVERIFIED_PROCESS",
                record.pid,
                record.port,
                "process identity is not owned by this desktop session",
            )
        if process is not None and process.pid == record.pid:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        elif not self.pid_terminator(record.pid, timeout=timeout):
            return ServiceStatus(
                service,
                "STOP_FAILED",
                record.pid,
                record.port,
                "verified process did not stop gracefully",
            )
        records = self._records()
        records.pop(service, None)
        self._save_records(records)
        self._processes.pop(service, None)
        return ServiceStatus(service, "STOPPED", record.pid, record.port)

    def restart(
        self,
        service: str,
        *,
        child_environment: dict[str, str] | None = None,
    ) -> ServiceStatus:
        stopped = self.stop(service)
        if stopped.state not in {"STOPPED"}:
            return stopped
        return self.start(service, child_environment=child_environment)

    def health(self) -> dict[str, ApiResult]:
        return {
            "router": self.api_client.router_health(),
            "data_hub": self.api_client.data_hub_health(),
        }

    def doctor(self) -> ApiResult:
        return self.api_client.doctor()

    def open_logs(self, *, launch: bool = True) -> Path:
        return self._open_directory(self.paths.logs_dir, launch=launch)

    def open_reports(self, *, launch: bool = True) -> Path:
        return self._open_directory(self.paths.reports_dir, launch=launch)

    def _open_directory(self, path: Path, *, launch: bool) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        if launch:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                webbrowser.open(path.as_uri())
        return path
