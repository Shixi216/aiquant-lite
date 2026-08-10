from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import duckdb

from desktop.paths import AppPaths


class ServiceStateReader(Protocol):
    def status(self, service: str): ...


@dataclass(frozen=True, slots=True)
class DatabaseBackup:
    database_path: str
    metadata_path: str
    sha256: str
    size_bytes: int
    created_at: str
    read_only_open_ok: bool
    migration_checksums_match: bool
    retention_policy: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class DatabaseBackupService:
    def __init__(
        self,
        paths: AppPaths,
        service_state: ServiceStateReader,
    ) -> None:
        self.paths = paths
        self.service_state = service_state

    def _require_services_stopped(self) -> None:
        states = {
            name: self.service_state.status(name).state
            for name in ("router", "data_hub")
        }
        if any(state != "STOPPED" for state in states.values()):
            raise RuntimeError("BACKUP_REQUIRES_STOPPED_SERVICES")

    def create(self) -> DatabaseBackup:
        self._require_services_stopped()
        source = self.paths.database_path
        if not source.is_file():
            raise FileNotFoundError("DATABASE_NOT_FOUND")
        self.paths.backups_dir.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now().astimezone()
        stem = f"hermes_opc-{created_at:%Y%m%d-%H%M%S-%f}"
        target = self.paths.backups_dir / f"{stem}.duckdb"
        metadata_path = self.paths.backups_dir / f"{stem}.json"
        source_hash = _sha256(source)
        shutil.copy2(source, target)
        copied_hash = _sha256(target)
        if source_hash != copied_hash:
            target.unlink(missing_ok=True)
            raise RuntimeError("BACKUP_SHA256_MISMATCH")
        try:
            with duckdb.connect(str(target), read_only=True) as connection:
                connection.execute("SELECT 1").fetchone()
            from router.integration.health import migration_checksum_state

            migration_ok = bool(migration_checksum_state(target)["ok"])
        except Exception:
            target.unlink(missing_ok=True)
            raise RuntimeError("BACKUP_READ_ONLY_VALIDATION_FAILED") from None
        if not migration_ok:
            target.unlink(missing_ok=True)
            raise RuntimeError("BACKUP_MIGRATION_CHECKSUM_MISMATCH")
        result = DatabaseBackup(
            database_path=str(target),
            metadata_path=str(metadata_path),
            sha256=copied_hash,
            size_bytes=target.stat().st_size,
            created_at=created_at.isoformat(),
            read_only_open_ok=True,
            migration_checksums_match=True,
            retention_policy="MANUAL_KEEP_ALL",
        )
        metadata_path.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result

    def list(self) -> tuple[DatabaseBackup, ...]:
        items: list[DatabaseBackup] = []
        if not self.paths.backups_dir.is_dir():
            return ()
        for metadata_path in sorted(
            self.paths.backups_dir.glob("hermes_opc-*.json"),
            reverse=True,
        ):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                items.append(DatabaseBackup(**payload))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return tuple(items)


__all__ = ["DatabaseBackup", "DatabaseBackupService"]
