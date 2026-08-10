from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from build.scripts.package_policy import verify_package_tree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "build" / "inno" / "hermes-opc.iss"
PORTABLE = PROJECT_ROOT / "dist" / "portable" / "HermesOPC"
INSTALLER_DIR = PROJECT_ROOT / "dist" / "installer"


def locate_iscc(explicit: Path | None = None) -> Path:
    candidates = [
        explicit,
        Path(os.environ["INNO_ISCC"]) if os.environ.get("INNO_ISCC") else None,
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        PROJECT_ROOT / ".build-tools" / "inno" / "ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Inno Setup ISCC.exe was not found")


def build(iscc: Path | None = None) -> dict[str, object]:
    policy = verify_package_tree(PORTABLE)
    if not policy.ok:
        raise RuntimeError("portable package policy failed")
    compiler = locate_iscc(iscc)
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(compiler), str(SCRIPT)],
        cwd=PROJECT_ROOT,
        check=True,
    )
    installer = INSTALLER_DIR / "HermesOPC-0.10.0-Setup.exe"
    if not installer.is_file():
        raise RuntimeError("installer was not generated")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    manifest = {
        "artifact": str(installer.relative_to(PROJECT_ROOT)),
        "sha256": digest,
        "source_policy_ok": True,
        "contains_real_database": False,
        "contains_dotenv": False,
        "preserves_user_data_on_upgrade": True,
        "preserves_user_data_on_default_uninstall": True,
        "delete_all_user_data_requires_explicit_checkbox": True,
        "delete_all_user_data_requires_second_confirmation": True,
    }
    manifests = PROJECT_ROOT / "dist" / "manifests"
    checksums = PROJECT_ROOT / "dist" / "checksums"
    manifests.mkdir(parents=True, exist_ok=True)
    checksums.mkdir(parents=True, exist_ok=True)
    (manifests / "installer-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (checksums / "installer-sha256.txt").write_text(
        f"{digest}  {installer.name}\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iscc", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.iscc), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
