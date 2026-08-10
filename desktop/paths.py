from __future__ import annotations

import os
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import duckdb


APP_DIRECTORY_NAME = "HermesOPC"


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _is_program_files(path: Path, env: Mapping[str, str]) -> bool:
    candidates = (
        env.get("ProgramFiles"),
        env.get("ProgramFiles(x86)"),
        env.get("ProgramW6432"),
    )
    target = str(path).casefold()
    return any(
        candidate
        and (
            target == str(_resolved(candidate)).casefold()
            or target.startswith(str(_resolved(candidate)).casefold() + os.sep)
        )
        for candidate in candidates
    )


@dataclass(frozen=True, slots=True)
class AppPaths:
    application_root: Path
    executable_dir: Path
    user_data_dir: Path
    config_dir: Path
    credentials_dir: Path
    database_dir: Path
    database_path: Path
    desktop_state_path: Path
    logs_dir: Path
    reports_dir: Path
    cache_dir: Path
    backups_dir: Path
    exports_dir: Path
    crash_reports_dir: Path
    development: bool

    @classmethod
    def resolve(
        cls,
        *,
        development: bool | None = None,
        application_root: str | Path | None = None,
        user_data_override: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> AppPaths:
        environ = dict(os.environ if env is None else env)
        frozen = bool(getattr(sys, "frozen", False))
        is_development = not frozen if development is None else development
        root = _resolved(
            application_root
            or (
                Path(__file__).resolve().parents[1]
                if is_development
                else Path(sys.executable).resolve().parent
            )
        )
        executable_dir = (
            _resolved(Path(sys.executable).parent)
            if not is_development
            else root
        )
        configured_user_dir = (
            user_data_override or environ.get("HERMES_OPC_USER_DATA_DIR")
        )
        if configured_user_dir:
            user_data = _resolved(configured_user_dir)
        elif is_development:
            user_data = root / "runtime" / "desktop"
        else:
            local_app_data = environ.get("LOCALAPPDATA")
            if not local_app_data:
                raise RuntimeError("LOCALAPPDATA is required in installed mode")
            default_user_data = _resolved(Path(local_app_data) / APP_DIRECTORY_NAME)
            pointer = default_user_data / "config" / "user-data-location.json"
            try:
                stored = json.loads(pointer.read_text(encoding="utf-8")).get(
                    "user_data_dir"
                )
            except (OSError, ValueError, json.JSONDecodeError):
                stored = None
            user_data = _resolved(stored) if stored else default_user_data
        if not is_development and _is_program_files(user_data, environ):
            raise ValueError("user data directory cannot be under Program Files")

        database_dir = root / "database" if is_development else user_data / "database"
        database_override = environ.get("HERMES_OPC_DATABASE_PATH")
        database_path = (
            _resolved(database_override)
            if database_override
            else database_dir / "hermes_opc.duckdb"
        )
        return cls(
            application_root=root,
            executable_dir=executable_dir,
            user_data_dir=user_data,
            config_dir=user_data / "config",
            credentials_dir=user_data / "credentials",
            database_dir=database_dir,
            database_path=database_path,
            desktop_state_path=user_data / "desktop_state.sqlite3",
            logs_dir=(
                root / "logs" / "hermes-opc"
                if is_development
                else user_data / "logs"
            ),
            reports_dir=root / "reports" if is_development else user_data / "reports",
            cache_dir=user_data / "cache",
            backups_dir=user_data / "backups",
            exports_dir=user_data / "exports",
            crash_reports_dir=user_data / "crash-reports",
            development=is_development,
        )

    def ensure_user_directories(self) -> tuple[Path, ...]:
        directories = (
            self.user_data_dir,
            self.config_dir,
            self.credentials_dir,
            self.database_dir,
            self.logs_dir,
            self.reports_dir,
            self.cache_dir,
            self.backups_dir,
            self.exports_dir,
            self.crash_reports_dir,
        )
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        return directories

    def persist_user_data_location(
        self, *, env: Mapping[str, str] | None = None
    ) -> Path | None:
        if self.development:
            return None
        environ = dict(os.environ if env is None else env)
        local_app_data = environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA is required in installed mode")
        pointer = (
            _resolved(Path(local_app_data) / APP_DIRECTORY_NAME)
            / "config"
            / "user-data-location.json"
        )
        pointer.parent.mkdir(parents=True, exist_ok=True)
        temporary = pointer.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"user_data_dir": str(self.user_data_dir)},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(pointer)
        return pointer

    def validate_database(self, path: str | Path | None = None) -> dict[str, object]:
        candidate = _resolved(path or self.database_path)
        if not candidate.is_file():
            return {"ok": False, "error": "DATABASE_NOT_FOUND"}
        try:
            with duckdb.connect(str(candidate), read_only=True) as connection:
                connection.execute("SELECT 1").fetchone()
            from router.integration.health import migration_checksum_state

            migration = migration_checksum_state(candidate)
        except Exception:
            return {"ok": False, "error": "DATABASE_VALIDATION_FAILED"}
        return {
            "ok": bool(migration["ok"]),
            "error": None if migration["ok"] else "MIGRATION_CHECKSUM_MISMATCH",
            "migration": migration,
        }

    def import_database_copy(self, source: str | Path) -> Path:
        candidate = _resolved(source)
        validation = self.validate_database(candidate)
        if not validation["ok"]:
            raise ValueError(str(validation["error"]))
        self.database_dir.mkdir(parents=True, exist_ok=True)
        if candidate == self.database_path.resolve():
            raise ValueError("source and destination database paths must differ")
        if self.database_path.exists():
            raise FileExistsError("destination database already exists")
        temporary = self.database_path.with_suffix(".duckdb.importing")
        shutil.copy2(candidate, temporary)
        if not self.validate_database(temporary)["ok"]:
            temporary.unlink(missing_ok=True)
            raise ValueError("copied database validation failed")
        temporary.replace(self.database_path)
        return self.database_path
