"""WealthDecisionPacket：财富管理独立研究决策包（不可原地覆盖，版本化，内容哈希）。

独立持久化于 wealth_lab.duckdb 的 wealth_decision_packets 表；
绝不写入股票系统 decision_packets 正式表；research_only，无任何执行通道。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import duckdb

from trading.wealth_lab.config import WEALTH_LAB_DB_PATH

_TZ = timezone(timedelta(hours=8))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wealth_decision_packets (
    packet_id VARCHAR PRIMARY KEY,
    portfolio_id VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    supersedes_version INTEGER,
    content_hash VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    packet_json VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS wealth_decision_packet_versions (
    portfolio_id VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    packet_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (portfolio_id, version)
);
"""


def _hash_content(packet: dict) -> str:
    canonical = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WealthDecisionPacketStore:
    def create(self, request: dict, *, metrics: dict, risk_check: dict,
               ai_research: dict | None, ai_risk_review: dict | None,
               data_sources: list[str], backtest_window: dict) -> dict:
        portfolio_id = request.get("portfolio_id") or "wealth-lab-default"
        with duckdb.connect(str(WEALTH_LAB_DB_PATH)) as con:
            con.execute(_SCHEMA)
            last = con.execute(
                "SELECT MAX(version) FROM wealth_decision_packet_versions WHERE portfolio_id=?",
                [portfolio_id],
            ).fetchone()[0]
        version = int(last or 0) + 1
        packet_id = "wldec_" + uuid.uuid4().hex[:12]
        packet = {
            "packet_id": packet_id,
            "portfolio_id": portfolio_id,
            "version": version,
            "supersedes_version": version - 1 if version > 1 else None,
            "strategy_id": request["strategy_id"],
            "strategy_version": request.get("strategy_version") or "1.0",
            "weights": request["weights"],
            "backtest_window": backtest_window,
            "data_cutoff": datetime.now(_TZ).isoformat(),
            "data_sources": data_sources,
            "metrics": metrics,
            "cost_assumptions": request.get("cost_assumptions") or {
                "cost_bps": 5, "model": "sum|delta_w|*bps/1e4", "currency": "CNY",
            },
            "risk_check": risk_check,
            "ai_research": (ai_research or {}),
            "ai_risk_review": (ai_risk_review or {}),
            "model_call_ids": [
                *(ai_research or {}).get("model_call_ids", []),
                *(ai_risk_review or {}).get("model_call_ids", []),
            ],
            "evidence_ids": [request["backtest_id"]] if request.get("backtest_id") else [],
            "created_at": datetime.now(_TZ).isoformat(),
            "research_only": True,
        }
        packet["content_hash"] = _hash_content(packet)
        with duckdb.connect(str(WEALTH_LAB_DB_PATH)) as con:
            con.execute(_SCHEMA)
            con.execute(
                "INSERT INTO wealth_decision_packets VALUES (?,?,?,?,?,?,?)",
                [packet_id, portfolio_id, version, packet["supersedes_version"],
                 packet["content_hash"], datetime.now(_TZ),
                 json.dumps(packet, ensure_ascii=False)],
            )
            con.execute(
                "INSERT INTO wealth_decision_packet_versions VALUES (?,?,?,?)",
                [portfolio_id, version, packet_id, datetime.now(_TZ)],
            )
        return packet

    def get(self, packet_id: str) -> dict | None:
        with duckdb.connect(str(WEALTH_LAB_DB_PATH)) as con:
            con.execute(_SCHEMA)
            row = con.execute(
                "SELECT packet_json FROM wealth_decision_packets WHERE packet_id=?",
                [packet_id],
            ).fetchone()
        return json.loads(row[0]) if row else None
