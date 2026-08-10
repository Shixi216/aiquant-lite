from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PORTABLE_ROOT = PROJECT_ROOT / "dist" / "portable" / "HermesOPC"
TEST_ROOT = PROJECT_ROOT / "dist" / "e2e" / "服务与扫描"
RESULT_PATH = PROJECT_ROOT / "dist" / "manifests" / "packaged-services-e2e.json"


def _environment(user_data: Path, database_path: Path) -> dict[str, str]:
    blocked = ("token", "api_key", "secret", "password")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.casefold() for marker in blocked)
    }
    environment.update(
        {
            "HERMES_OPC_USER_DATA_DIR": str(user_data),
            "HERMES_OPC_DATABASE_PATH": str(database_path),
            "QT_QPA_PLATFORM": "offscreen",
        }
    )
    return environment


def _get_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_health(url: str, process: subprocess.Popen[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    last_error = "service did not start"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"service exited early with code {process.returncode}")
        try:
            payload = _get_json(url)
            if payload.get("status") == "ok":
                return payload
            last_error = f"health status: {payload.get('status')}"
        except Exception as exc:
            last_error = type(exc).__name__
        time.sleep(0.5)
    raise TimeoutError(last_error)


def _stop_owned_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def verify() -> dict[str, Any]:
    executable = PORTABLE_ROOT / "hermes-opc-service.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    user_data = TEST_ROOT / "用户 数据"
    database_path = user_data / "database" / "hermes_opc.duckdb"
    environment = _environment(user_data, database_path)
    processes: list[subprocess.Popen[str]] = []
    logs = []
    try:
        for service, port in (("data_hub", 8766), ("router", 8765)):
            log_path = TEST_ROOT / f"{service}.log"
            log_handle = log_path.open("w", encoding="utf-8")
            logs.append(log_handle)
            process = subprocess.Popen(
                [str(executable), "--service", service],
                cwd=PORTABLE_ROOT,
                env=environment,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            processes.append(process)
            health = _wait_for_health(f"http://127.0.0.1:{port}/health", process)
            dependency = "database" if service == "data_hub" else "data_hub"
            if health.get(dependency, {}).get("status") != "ok":
                raise RuntimeError(f"{service} dependency health failed")

        scanner_error_status = None
        scanner_error_code = None
        try:
            _post_json(
                "http://127.0.0.1:8765/v1/scanner/scan",
                {
                    "query": "全A股前20只",
                    "top_n": 20,
                    "allow_parser_model": False,
                    "persist_run": False,
                },
            )
        except urllib.error.HTTPError as exc:
            scanner_error_status = exc.code
            payload = json.loads(exc.read().decode("utf-8"))
            scanner_error_code = payload.get("detail")
        if (
            scanner_error_status != 409
            or scanner_error_code != "SCANNER_EMPTY_UNIVERSE"
        ):
            raise RuntimeError("empty packaged database scanner gate failed")
    finally:
        for process in reversed(processes):
            _stop_owned_process(process)
        for log_handle in logs:
            log_handle.close()

    if not database_path.is_file():
        raise RuntimeError("packaged services did not create the user database")
    with duckdb.connect(str(database_path), read_only=True) as connection:
        table_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'main'
            """
        ).fetchone()[0]
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
    program_database = PORTABLE_ROOT / "database" / "hermes_opc.duckdb"
    result = {
        "portable_root": str(PORTABLE_ROOT),
        "user_data_path": str(user_data),
        "database_path": str(database_path),
        "database_in_program_directory": program_database.exists(),
        "data_hub_started": True,
        "router_started": True,
        "both_services_stopped": all(process.poll() is not None for process in processes),
        "table_count": table_count,
        "migration_count": migration_count,
        "scanner_request_ok": False,
        "scanner_empty_universe_handled": True,
        "scanner_error_status": scanner_error_status,
        "scanner_error_code": scanner_error_code,
        "scanner_persist_requested": False,
        "scanner_false_success": False,
        "credentials_inherited": False,
    }
    if result["database_in_program_directory"]:
        raise RuntimeError("packaged services wrote a database into the program tree")
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    print(json.dumps(verify(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
