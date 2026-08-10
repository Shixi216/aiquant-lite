from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from desktop.credentials import (
    WindowsCredentialStore,
    child_process_environment,
)
from desktop.paths import AppPaths
from desktop.runtime import activate_runtime_paths
from desktop.service_supervisor import ServiceSupervisor
from desktop.settings import DesktopSettings
from desktop.workspace.model_research import RouterResearchClient
from desktop.workspace.service import TaskCenterService
from desktop.workspace.state import DesktopStateRepository
from router.integration.sanitization import sanitize_error


ROUTER_URL = "http://127.0.0.1:8765"


def installed_paths(install_dir: Path, user_data_dir: Path) -> AppPaths:
    resolved = AppPaths.resolve(
        development=False,
        application_root=install_dir,
        user_data_override=user_data_dir,
    )
    return replace(resolved, executable_dir=install_dir.resolve())


def configured_environment(paths: AppPaths) -> dict[str, str]:
    settings = DesktopSettings.load(
        paths.config_dir / "desktop-settings.json"
    )
    providers = [
        metadata.credential_key
        for metadata in settings.credentials.values()
        if metadata.configured
    ]
    base = {
        **os.environ,
        "HERMES_OPC_USER_DATA_DIR": str(paths.user_data_dir),
        "OPC_DATABASE_PATH": str(paths.database_path),
    }
    return child_process_environment(
        WindowsCredentialStore(),
        providers,
        base=base,
    )


def _research(paths: AppPaths) -> dict[str, Any]:
    service = TaskCenterService(
        DesktopStateRepository(paths.desktop_state_path),
        research_model_client=RouterResearchClient(router_url=ROUTER_URL),
    )
    conversation = service.new_conversation()
    task = service.submit_message(
        conversation.conversation_id,
        "分析宁德时代",
    )
    fields = task.result_card.fields if task.result_card is not None else {}
    model_research = fields.get("model_research")
    structured = (
        isinstance(model_research, list)
        and bool(model_research)
        and isinstance(model_research[0], dict)
    )
    return {
        "task_status": task.status.value,
        "model_call_count": int(fields.get("model_call_count") or 0),
        "structured_result": structured,
        "error_code": task.error_code,
    }


def _announcement_and_audit() -> dict[str, Any]:
    request = {
        "symbol": "600172.SH",
        "announcement_id": "1225422790",
        "claims": [
            "公司预计2026年半年度归属于母公司所有者的净利润为22,000万元。",
        ],
        "max_tokens": 5000,
    }
    with httpx.Client(
        base_url=ROUTER_URL,
        timeout=300,
        trust_env=False,
    ) as client:
        announcement = client.post(
            "/v1/pipelines/announcement-verification",
            json=request,
        )
        try:
            announcement_payload = announcement.json()
        except ValueError:
            announcement_payload = {}
        task_id = str(announcement_payload.get("task_id") or "")
        task_list = client.get("/v1/audit/tasks", params={"limit": 50})
        try:
            list_payload = task_list.json()
        except ValueError:
            list_payload = {}
    return {
        "announcement_http_status": announcement.status_code,
        "announcement_validated": (
            announcement_payload.get("validated") is True
        ),
        "audit_task_id_present": bool(task_id),
        "announcement_error_code": (
            None
            if announcement.status_code == 200
            else str(
                announcement_payload.get("detail")
                or f"HTTP_{announcement.status_code}"
            )[:100]
        ),
        "audit_list_http_status": task_list.status_code,
        "audit_list_count": int(list_payload.get("count") or 0),
        "audit_list_invalid_record_count": int(
            list_payload.get("invalid_record_count") or 0
        ),
    }


def verify(install_dir: Path, user_data_dir: Path) -> dict[str, Any]:
    paths = installed_paths(install_dir, user_data_dir)
    activate_runtime_paths(paths)
    supervisor = ServiceSupervisor(paths)
    environment = configured_environment(paths)
    started: list[str] = []
    result: dict[str, Any] = {
        "install_dir": str(install_dir),
        "user_data_dir": str(user_data_dir),
    }
    try:
        for service in ("data_hub", "router"):
            status = supervisor.start(
                service,
                child_environment=environment,
                timeout=60,
            )
            result[f"{service}_state"] = status.state
            if status.state not in {"RUNNING", "ALREADY_RUNNING"}:
                raise RuntimeError(f"{service}_START_FAILED")
            started.append(service)
        result["research"] = _research(paths)
        result.update(_announcement_and_audit())
    except Exception as exc:
        result["verification_error"] = sanitize_error(exc)
    finally:
        for service in reversed(started):
            status = supervisor.stop(service)
            result[f"{service}_stop_state"] = status.state
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--user-data-dir", type=Path, required=True)
    args = parser.parse_args()
    result = verify(
        args.install_dir.resolve(),
        args.user_data_dir.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False))
    research = result.get("research") or {}
    passed = (
        research.get("task_status") == "COMPLETED"
        and research.get("model_call_count", 0) >= 1
        and research.get("structured_result") is True
        and result.get("announcement_http_status") == 200
        and result.get("announcement_validated") is True
        and result.get("audit_task_id_present") is True
        and result.get("audit_list_http_status") == 200
        and result.get("data_hub_stop_state") == "STOPPED"
        and result.get("router_stop_state") == "STOPPED"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
