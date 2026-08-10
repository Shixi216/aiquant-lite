from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from build.scripts.package_policy import verify_package_tree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = PROJECT_ROOT / "dist" / "portable" / "HermesOPC"


def verify(root: Path) -> dict[str, object]:
    executable = root / "hermes-opc-service.exe"
    gui = root / "HermesOPC.exe"
    if not executable.is_file() or not gui.is_file():
        raise FileNotFoundError("portable executables are missing")
    policy = verify_package_tree(root)
    if not policy.ok:
        raise RuntimeError("portable package policy failed")
    with tempfile.TemporaryDirectory(prefix="Hermes OPC 验收 ") as temporary:
        user_data = Path(temporary) / "用户 数据"
        environment = {
            **os.environ,
            "HERMES_OPC_USER_DATA_DIR": str(user_data),
            "QT_QPA_PLATFORM": "offscreen",
        }
        diagnostic = subprocess.run(
            [str(executable), "--diagnose-json"],
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        smoke = subprocess.run(
            [str(executable), "--headless-smoke"],
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        diagnostic_payload = json.loads(diagnostic.stdout.strip().splitlines()[-1])
        smoke_payload = json.loads(smoke.stdout.strip().splitlines()[-1])
        if diagnostic_payload.get("pymupdf_ready") is not True:
            raise RuntimeError("portable PyMuPDF runtime is incomplete")
        if not user_data.is_dir():
            raise RuntimeError("portable package did not create user data directory")
    return {
        "portable_root": str(root),
        "policy_ok": policy.ok,
        "scanned_files": policy.scanned_files,
        "diagnostic": diagnostic_payload,
        "headless_smoke": smoke_payload,
        "chinese_and_space_path_tested": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.root.resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
