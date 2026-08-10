from __future__ import annotations


SAFETY_CONTRACT = {
    "live_trading_supported": False,
    "broker_adapter_supported": False,
    "qmt_supported": False,
    "xtquant_supported": False,
    "real_order_supported": False,
    "formal_strategy_weights": {
        "TECHNICAL": 0.6,
        "FUNDAMENTAL": 0.4,
    },
    "shadow_composite_formal_weight": 0,
    "hard_risk_veto_mutable": False,
}


def packaged_no_live_check() -> bool:
    return all(
        SAFETY_CONTRACT[key] is False
        for key in (
            "live_trading_supported",
            "broker_adapter_supported",
            "qmt_supported",
            "xtquant_supported",
            "real_order_supported",
        )
    )


__all__ = ["SAFETY_CONTRACT", "packaged_no_live_check"]
