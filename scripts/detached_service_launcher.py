from __future__ import annotations

# ruff: noqa: E402

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.utf8 import utf8_child_environment


DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("--pid-file", required=True)
    return parser.parse_args()


def launch(args: argparse.Namespace) -> int:
    project_root = Path(args.cwd).resolve()
    stdout_path = Path(args.stdout).resolve()
    stderr_path = Path(args.stderr).resolve()
    pid_path = Path(args.pid_file).resolve()

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.parent.mkdir(parents=True, exist_ok=True)

    python_executable = Path(sys.executable)
    pythonw_executable = python_executable.with_name("pythonw.exe")

    if not pythonw_executable.exists():
        pythonw_executable = python_executable

    command = [
        str(pythonw_executable),
        "-m",
        args.module,
    ]

    primary_flags = (
        DETACHED_PROCESS
        | CREATE_NEW_PROCESS_GROUP
        | CREATE_BREAKAWAY_FROM_JOB
    )

    fallback_flags = (
        DETACHED_PROCESS
        | CREATE_NEW_PROCESS_GROUP
    )

    with (
        stdout_path.open("ab", buffering=0) as stdout_file,
        stderr_path.open("ab", buffering=0) as stderr_file,
    ):
        try:
            process = subprocess.Popen(
                command,
                cwd=str(project_root),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                env=utf8_child_environment(),
                close_fds=True,
                creationflags=primary_flags,
            )
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {5, 87}:
                raise

            process = subprocess.Popen(
                command,
                cwd=str(project_root),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                env=utf8_child_environment(),
                close_fds=True,
                creationflags=fallback_flags,
            )

    pid_path.write_text(
        str(process.pid),
        encoding="ascii",
    )

    return process.pid


def main() -> None:
    args = parse_args()
    process_id = launch(args)
    print(f"Detached process launched. PID: {process_id}")


if __name__ == "__main__":
    main()