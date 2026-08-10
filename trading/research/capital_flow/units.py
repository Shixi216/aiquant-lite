from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trading.research.capital_flow.schemas import CapitalFlowRiskFlag


UNIT_CONVERSION_VERSION = "capital-flow-units-v1"


class VolumeUnit(StrEnum):
    SHARES = "SHARES"
    LOTS_100 = "LOTS_100"
    UNKNOWN = "UNKNOWN"


class AmountUnit(StrEnum):
    CNY = "CNY"
    THOUSAND_CNY = "THOUSAND_CNY"
    TEN_THOUSAND_CNY = "TEN_THOUSAND_CNY"
    HUNDRED_MILLION_CNY = "HUNDRED_MILLION_CNY"
    UNKNOWN = "UNKNOWN"


class RateUnit(StrEnum):
    DECIMAL = "DECIMAL"
    PERCENT = "PERCENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class UnitValue:
    value: float | None
    unit: str
    flags: tuple[CapitalFlowRiskFlag, ...] = ()
    version: str = UNIT_CONVERSION_VERSION


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def normalize_volume(value: object, unit: VolumeUnit) -> UnitValue:
    number = _number(value)
    if number is None:
        return UnitValue(None, VolumeUnit.SHARES)
    if unit == VolumeUnit.SHARES:
        return UnitValue(number, VolumeUnit.SHARES)
    if unit == VolumeUnit.LOTS_100:
        return UnitValue(number * 100, VolumeUnit.SHARES)
    return UnitValue(
        None,
        VolumeUnit.SHARES,
        (CapitalFlowRiskFlag.UNIT_UNKNOWN,),
    )


def normalize_amount(value: object, unit: AmountUnit) -> UnitValue:
    number = _number(value)
    if number is None:
        return UnitValue(None, AmountUnit.CNY)
    factors = {
        AmountUnit.CNY: 1.0,
        AmountUnit.THOUSAND_CNY: 1_000.0,
        AmountUnit.TEN_THOUSAND_CNY: 10_000.0,
        AmountUnit.HUNDRED_MILLION_CNY: 100_000_000.0,
    }
    if unit not in factors:
        return UnitValue(
            None,
            AmountUnit.CNY,
            (CapitalFlowRiskFlag.UNIT_UNKNOWN,),
        )
    return UnitValue(number * factors[unit], AmountUnit.CNY)


def normalize_rate(value: object, unit: RateUnit) -> UnitValue:
    number = _number(value)
    if number is None:
        return UnitValue(None, RateUnit.DECIMAL)
    if unit == RateUnit.DECIMAL:
        return UnitValue(number, RateUnit.DECIMAL)
    if unit == RateUnit.PERCENT:
        return UnitValue(number / 100, RateUnit.DECIMAL)
    return UnitValue(
        None,
        RateUnit.DECIMAL,
        (CapitalFlowRiskFlag.UNIT_UNKNOWN,),
    )


def resolve_unit_values(values: list[UnitValue]) -> UnitValue:
    known = [item for item in values if item.value is not None]
    flags = {flag for item in values for flag in item.flags}
    if not known:
        flags.add(CapitalFlowRiskFlag.UNIT_UNKNOWN)
        return UnitValue(None, values[0].unit if values else "UNKNOWN", tuple(sorted(flags)))
    baseline = known[0]
    tolerance = max(abs(baseline.value or 0) * 1e-6, 1e-9)
    if any(abs((item.value or 0) - (baseline.value or 0)) > tolerance for item in known[1:]):
        flags.add(CapitalFlowRiskFlag.UNIT_CONFLICT)
        return UnitValue(None, baseline.unit, tuple(sorted(flags)))
    return UnitValue(baseline.value, baseline.unit, tuple(sorted(flags)))


__all__ = [
    "AmountUnit",
    "RateUnit",
    "UNIT_CONVERSION_VERSION",
    "UnitValue",
    "VolumeUnit",
    "normalize_amount",
    "normalize_rate",
    "normalize_volume",
    "resolve_unit_values",
]
