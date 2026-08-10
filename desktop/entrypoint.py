from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence

from config.version import PROJECT_VERSION
from desktop.paths import AppPaths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="HermesOPC")
    parser.add_argument("--service", choices=("router", "data_hub"))
    parser.add_argument("--diagnose-json", action="store_true")
    parser.add_argument("--headless-smoke", action="store_true")
    parser.add_argument("--version", action="store_true")
    return parser


def _diagnose() -> int:
    import pymupdf

    from desktop.paths import AppPaths

    paths = AppPaths.resolve()
    paths.ensure_user_directories()
    payload = {
        "application_version": PROJECT_VERSION,
        "application_root_exists": paths.application_root.is_dir(),
        "user_data_dir_exists": paths.user_data_dir.is_dir(),
        "database_in_program_directory": (
            paths.database_path.parent == paths.application_root
            and not paths.development
        ),
        "development": paths.development,
        "python_required_for_user": False,
        "uv_required_for_user": False,
        "live_trading_supported": False,
        "pymupdf_ready": bool(
            callable(getattr(pymupdf, "open", None))
        ),
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


def _isolated_smoke_paths(paths: AppPaths, root: Path) -> AppPaths:
    """Keep package smoke tests away from configured user and business data."""

    user_data = root / "user-data"
    return replace(
        paths,
        user_data_dir=user_data,
        config_dir=user_data / "config",
        credentials_dir=user_data / "credentials",
        database_dir=user_data / "database",
        database_path=user_data / "database" / "hermes_opc.duckdb",
        desktop_state_path=user_data / "desktop_state.sqlite3",
        logs_dir=user_data / "logs",
        reports_dir=user_data / "reports",
        cache_dir=user_data / "cache",
        backups_dir=user_data / "backups",
        exports_dir=user_data / "exports",
        crash_reports_dir=user_data / "crash-reports",
    )


def _headless_smoke() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from desktop.runtime import prepare_runtime

    app = QApplication.instance() or QApplication([])
    configured_paths = AppPaths.resolve()
    with TemporaryDirectory(prefix="hermes-opc-smoke-") as temporary:
        paths = prepare_runtime(
            _isolated_smoke_paths(configured_paths, Path(temporary))
        )
        from desktop.application import MainWindow

        window = MainWindow(paths)
        page_count = window.navigation.count()
        window.close()
        app.processEvents()
    print(
        json.dumps(
            {
                "desktop_created": True,
                "page_count": page_count,
                "user_data_dir": str(configured_paths.user_data_dir),
                "isolated_smoke_data": True,
                "live_trading_supported": False,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def _run_service(service: str) -> int:
    from desktop.runtime import prepare_runtime

    prepare_runtime()
    if service == "router":
        from scripts.run_router_api import main
    else:
        from scripts.run_data_api import main
    main()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(list(argv) if argv is not None else None)
    if arguments.version:
        print(PROJECT_VERSION)
        return 0
    if arguments.diagnose_json:
        return _diagnose()
    if arguments.headless_smoke:
        return _headless_smoke()
    if arguments.service:
        return _run_service(arguments.service)
    from desktop.runtime import prepare_runtime

    paths = prepare_runtime()
    from desktop.application import main as desktop_main

    return desktop_main(paths=paths)


if __name__ == "__main__":
    raise SystemExit(main())
