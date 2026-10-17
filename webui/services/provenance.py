"""Provenance four-tuple attached to every data block."""
from __future__ import annotations


def provenance(
    *,
    data_source: str,
    data_time: str | None = None,
    calculation_source: str = "none",
    formal_status: str = "unavailable",
) -> dict:
    return {
        "data_source": data_source,
        "data_time": data_time,
        "calculation_source": calculation_source,
        "formal_status": formal_status,
    }


__all__ = ["provenance"]
