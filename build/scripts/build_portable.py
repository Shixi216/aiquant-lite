from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from build.scripts.generate_manifest import generate_manifest
from build.scripts.package_policy import verify_package_tree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIST_ROOT = PROJECT_ROOT / "dist"
PORTABLE_ROOT = DIST_ROOT / "portable"
PORTABLE_APP = PORTABLE_ROOT / "HermesOPC"
WORK_ROOT = PROJECT_ROOT / ".build-cache" / "pyinstaller"
SPEC = PROJECT_ROOT / "build" / "pyinstaller" / "hermes_opc.spec"


def _safe_remove_tree(path: Path, allowed_parent: Path) -> None:
    resolved = path.resolve()
    parent = allowed_parent.resolve()
    if resolved.parent != parent:
        raise RuntimeError("refusing to remove path outside expected build root")
    if resolved.exists():
        shutil.rmtree(resolved)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(*, clean: bool = True) -> dict[str, object]:
    generate_manifest()
    PORTABLE_ROOT.mkdir(parents=True, exist_ok=True)
    WORK_ROOT.parent.mkdir(parents=True, exist_ok=True)
    if clean:
        _safe_remove_tree(PORTABLE_APP, PORTABLE_ROOT)
        _safe_remove_tree(WORK_ROOT, WORK_ROOT.parent)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--distpath",
        str(PORTABLE_ROOT),
        "--workpath",
        str(WORK_ROOT),
        str(SPEC),
    ]
    if clean:
        command.insert(4, "--clean")
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    shutil.copy2(PROJECT_ROOT / ".env.example", PORTABLE_APP / ".env.example")
    shutil.copy2(
        PROJECT_ROOT / "build" / "manifests" / "build-manifest.json",
        PORTABLE_APP / "build-manifest.json",
    )
    result = verify_package_tree(PORTABLE_APP)
    if not result.ok:
        raise RuntimeError(
            "package policy failed: "
            f"forbidden={len(result.forbidden_paths)} "
            f"secrets={len(result.secret_assignment_files)}"
        )
    executables = [
        PORTABLE_APP / "HermesOPC.exe",
        PORTABLE_APP / "hermes-opc-service.exe",
    ]
    if not all(path.is_file() for path in executables):
        raise RuntimeError("expected portable executables are missing")
    checksums = {path.name: _sha256(path) for path in executables}
    checksums_dir = DIST_ROOT / "checksums"
    manifests_dir = DIST_ROOT / "manifests"
    checksums_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    checksum_file = checksums_dir / "portable-sha256.json"
    checksum_file.write_text(
        json.dumps(checksums, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "artifact": str(PORTABLE_APP.relative_to(PROJECT_ROOT)),
        "file_count": result.scanned_files,
        "forbidden_path_count": len(result.forbidden_paths),
        "secret_assignment_file_count": len(result.secret_assignment_files),
        "contains_python_runtime": True,
        "requires_user_python": False,
        "requires_user_uv": False,
        "contains_real_database": False,
        "contains_dotenv": False,
        "checksums": checksums,
    }
    (manifests_dir / "portable-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-clean", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(build(clean=not arguments.no_clean), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
