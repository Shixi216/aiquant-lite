from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INSTALLER = (
    PROJECT_ROOT / "dist" / "installer" / "HermesOPC-0.10.0-Setup.exe"
)
TEST_ROOT = PROJECT_ROOT / "dist" / "e2e" / "安装 升级 卸载"
RESULT_PATH = PROJECT_ROOT / "dist" / "manifests" / "installer-e2e.json"


def _remove_previous_test_root() -> None:
    resolved = TEST_ROOT.resolve()
    allowed = (PROJECT_ROOT / "dist" / "e2e").resolve()
    if resolved == allowed or allowed not in resolved.parents:
        raise RuntimeError("refusing to clean outside the installer test root")
    if resolved.exists():
        shutil.rmtree(resolved)


def _run_setup(installer: Path, install_dir: Path, log_path: Path) -> None:
    subprocess.run(
        [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/SP-",
            "/NOICONS",
            f"/DIR={install_dir}",
            f"/LOG={log_path}",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        timeout=300,
    )


def verify(installer: Path) -> dict[str, object]:
    if not installer.is_file():
        raise FileNotFoundError(installer)
    _remove_previous_test_root()
    install_dir = TEST_ROOT / "程序 目录"
    user_data = TEST_ROOT / "用户 数据"
    TEST_ROOT.mkdir(parents=True)

    _run_setup(installer, install_dir, TEST_ROOT / "install.log")
    service = install_dir / "hermes-opc-service.exe"
    gui = install_dir / "HermesOPC.exe"
    if not service.is_file() or not gui.is_file():
        raise RuntimeError("installed executables are missing")

    environment = {
        **os.environ,
        "HERMES_OPC_USER_DATA_DIR": str(user_data),
        "QT_QPA_PLATFORM": "offscreen",
    }
    diagnostic = subprocess.run(
        [str(service), "--diagnose-json"],
        cwd=install_dir,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    smoke = subprocess.run(
        [str(service), "--headless-smoke"],
        cwd=install_dir,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    diagnostic_payload = json.loads(diagnostic.stdout.strip().splitlines()[-1])
    smoke_payload = json.loads(smoke.stdout.strip().splitlines()[-1])

    sentinel = user_data / "config" / "upgrade-preservation-sentinel.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("preserve-user-state\n", encoding="utf-8")
    _run_setup(installer, install_dir, TEST_ROOT / "upgrade.log")
    upgrade_preserved = sentinel.read_text(encoding="utf-8") == (
        "preserve-user-state\n"
    )

    uninstaller = install_dir / "unins000.exe"
    if not uninstaller.is_file():
        raise RuntimeError("uninstaller is missing")
    subprocess.run(
        [
            str(uninstaller),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            f"/LOG={TEST_ROOT / 'uninstall.log'}",
        ],
        cwd=install_dir,
        check=True,
        timeout=300,
    )
    default_uninstall_preserved = sentinel.is_file()
    program_removed = not gui.exists() and not service.exists()
    if not upgrade_preserved or not default_uninstall_preserved or not program_removed:
        raise RuntimeError("installer lifecycle preservation check failed")

    _run_setup(installer, install_dir, TEST_ROOT / "reinstall.log")
    reinstall_recognized = sentinel.read_text(encoding="utf-8") == (
        "preserve-user-state\n"
    )
    reinstalled_uninstaller = install_dir / "unins000.exe"
    subprocess.run(
        [
            str(reinstalled_uninstaller),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            f"/LOG={TEST_ROOT / 'reinstall-uninstall.log'}",
        ],
        cwd=install_dir,
        check=True,
        timeout=300,
    )
    final_program_removed = not gui.exists() and not service.exists()
    if not reinstall_recognized or not sentinel.is_file() or not final_program_removed:
        raise RuntimeError("reinstall preservation check failed")

    result = {
        "installer": str(installer.relative_to(PROJECT_ROOT)),
        "install_path": str(install_dir),
        "user_data_path": str(user_data),
        "chinese_and_space_path_tested": True,
        "install_succeeded": True,
        "installed_diagnostic": diagnostic_payload,
        "installed_headless_smoke": smoke_payload,
        "upgrade_succeeded": True,
        "upgrade_preserved_user_data": upgrade_preserved,
        "uninstall_succeeded": True,
        "default_uninstall_preserved_user_data": default_uninstall_preserved,
        "program_files_removed": program_removed,
        "reinstall_succeeded": True,
        "reinstall_recognized_user_data": reinstall_recognized,
        "final_uninstall_preserved_user_data": sentinel.is_file(),
        "final_program_files_removed": final_program_removed,
        "delete_all_option_not_selected": True,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, default=DEFAULT_INSTALLER)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.installer.resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
