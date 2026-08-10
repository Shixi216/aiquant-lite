from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CredentialMetadata:
    credential_key: str
    provider: str
    configured: bool = False
    last_test_status: str = "NOT_TESTED"
    last_test_time: str | None = None


@dataclass(slots=True)
class DesktopSettings:
    router_port: int = 8765
    data_hub_port: int = 8766
    keep_services_running: bool = False
    compatibility_env_mode: bool = False
    first_run_completed: bool = False
    credentials: dict[str, CredentialMetadata] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> DesktopSettings:
        if not path.is_file():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            credentials = {
                name: CredentialMetadata(**metadata)
                for name, metadata in payload.pop("credentials", {}).items()
            }
            return cls(credentials=credentials, **payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return cls()

    def save(self, path: Path) -> None:
        payload = asdict(self)
        forbidden = ("token", "secret", "api_key", "password", "credential_value")
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        lowered = serialized.casefold()
        for key in forbidden:
            if f'"{key}"' in lowered:
                raise ValueError("plaintext credential fields are forbidden")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(serialized + "\n", encoding="utf-8")
        temporary.replace(path)

    def mark_credential(
        self,
        provider: str,
        credential_key: str,
        *,
        configured: bool,
        test_status: str = "NOT_TESTED",
        tested_at: datetime | None = None,
    ) -> None:
        self.credentials[provider] = CredentialMetadata(
            credential_key=credential_key,
            provider=provider,
            configured=configured,
            last_test_status=test_status,
            last_test_time=tested_at.isoformat() if tested_at else None,
        )

    def public_payload(self) -> dict[str, Any]:
        return asdict(self)
