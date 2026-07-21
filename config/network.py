from __future__ import annotations

import os

from config.settings import settings


def _split_hosts(value: str | None) -> list[str]:
    if not value:
        return []

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def configure_network_policy() -> str:
    """Merge project direct-connect hosts into NO_PROXY."""

    candidates = [
        *_split_hosts(os.environ.get("NO_PROXY")),
        *_split_hosts(os.environ.get("no_proxy")),
        *_split_hosts(settings.opc_no_proxy),
    ]

    merged: list[str] = []
    seen: set[str] = set()

    for host in candidates:
        key = host.lower()

        if key in seen:
            continue

        seen.add(key)
        merged.append(host)

    value = ",".join(merged)

    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value

    return value