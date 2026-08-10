"""人工持仓管理器（阶段2.4）— local_user_id 隔离 + 成交账本

支持：
- 记录人工买入/卖出（数量、价格、日期）
- 更新持仓成本和数量
- 清仓
- 修改成本
- 按用户隔离
- 审计记录
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

TZ = timezone(timedelta(hours=8))


@dataclass
class Holding:
    local_user_id: str
    symbol: str
    stock_name: str = ""
    quantity: float = 0.0          # 持仓数量（股）
    average_cost: float = 0.0      # 平均成本
    current_price: float = 0.0
    position_ratio: float = 0.0    # 占总资产比例
    total_assets: float = 0.0


@dataclass
class TradeRecord:
    trade_id: str
    local_user_id: str
    symbol: str
    side: str                      # BUY / SELL
    quantity: float
    price: float
    traded_at: datetime
    created_at: datetime


class ManualPositionManager:
    """人工持仓账本（每用户独立持仓）"""

    def __init__(self, db_path: Path | None = None):
        from config.settings import settings
        if db_path is None:
            db_path = Path(settings.opc_database_path)
            if not db_path.is_absolute():
                db_path = Path(__file__).resolve().parents[2] / db_path
        self.db_path = db_path

    def _connect(self):
        from database.db import open_database
        con = open_database(self.db_path)
        con.execute("""
            CREATE TABLE IF NOT EXISTS manual_holdings (
                local_user_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                stock_name VARCHAR,
                quantity DOUBLE DEFAULT 0,
                average_cost DOUBLE DEFAULT 0,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (local_user_id, symbol)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS manual_trade_ledger (
                trade_id VARCHAR PRIMARY KEY,
                local_user_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                quantity DOUBLE NOT NULL,
                price DOUBLE NOT NULL,
                traded_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        return con

    # ------------------------------------------------------------------
    # 持仓查询（显式 local_user_id）
    # ------------------------------------------------------------------
    def get_holding(self, local_user_id: str, symbol: str) -> Optional[Holding]:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT local_user_id, symbol, stock_name, quantity, average_cost "
                "FROM manual_holdings WHERE local_user_id=? AND symbol=?",
                [local_user_id, symbol],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return Holding(
            local_user_id=row[0], symbol=row[1], stock_name=row[2],
            quantity=row[3], average_cost=row[4],
        )

    def list_holdings(self, local_user_id: str) -> list[Holding]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT local_user_id, symbol, stock_name, quantity, average_cost "
                "FROM manual_holdings WHERE local_user_id=? AND quantity > 0",
                [local_user_id],
            ).fetchall()
        finally:
            con.close()
        return [
            Holding(local_user_id=r[0], symbol=r[1], stock_name=r[2],
                    quantity=r[3], average_cost=r[4])
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 成交记录（需用户确认后调用）
    # ------------------------------------------------------------------
    def record_trade(
        self, local_user_id: str, symbol: str, side: str,
        quantity: float, price: float, stock_name: str = "",
        traded_at: datetime | None = None,
    ) -> TradeRecord:
        """记录一笔人工成交并更新持仓（BUY增加/SELL减少）"""
        trade = TradeRecord(
            trade_id=f"trade_{uuid.uuid4().hex[:16]}",
            local_user_id=local_user_id, symbol=symbol, side=side.upper(),
            quantity=quantity, price=price,
            traded_at=traded_at or datetime.now(tz=TZ),
            created_at=datetime.now(tz=TZ),
        )
        con = self._connect()
        try:
            # 写成交账本
            con.execute(
                """
                INSERT INTO manual_trade_ledger
                (trade_id, local_user_id, symbol, side, quantity, price, traded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [trade.trade_id, local_user_id, symbol, trade.side,
                 quantity, price, trade.traded_at],
            )
            # 更新持仓
            holding = con.execute(
                "SELECT quantity, average_cost FROM manual_holdings "
                "WHERE local_user_id=? AND symbol=?",
                [local_user_id, symbol],
            ).fetchone()
            if holding is None:
                if trade.side == "BUY":
                    con.execute(
                        "INSERT INTO manual_holdings (local_user_id, symbol, stock_name, quantity, average_cost) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [local_user_id, symbol, stock_name, quantity, price],
                    )
            else:
                old_qty, old_cost = holding[0], holding[1]
                if trade.side == "BUY":
                    new_qty = old_qty + quantity
                    new_cost = (old_qty * old_cost + quantity * price) / new_qty if new_qty > 0 else 0
                else:  # SELL
                    new_qty = max(0.0, old_qty - quantity)
                    new_cost = old_cost  # 成本不变（卖出实现盈亏不摊薄成本）
                con.execute(
                    "UPDATE manual_holdings SET quantity=?, average_cost=?, stock_name=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE local_user_id=? AND symbol=?",
                    [new_qty, new_cost, stock_name, local_user_id, symbol],
                )
        finally:
            con.close()
        return trade

    def close_position(self, local_user_id: str, symbol: str) -> None:
        """清仓"""
        con = self._connect()
        try:
            con.execute(
                "UPDATE manual_holdings SET quantity=0, updated_at=CURRENT_TIMESTAMP "
                "WHERE local_user_id=? AND symbol=?",
                [local_user_id, symbol],
            )
        finally:
            con.close()

    def adjust_cost(self, local_user_id: str, symbol: str, new_cost: float) -> None:
        """修改成本"""
        con = self._connect()
        try:
            con.execute(
                "UPDATE manual_holdings SET average_cost=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE local_user_id=? AND symbol=?",
                [new_cost, local_user_id, symbol],
            )
        finally:
            con.close()

    def list_trades(self, local_user_id: str, symbol: str | None = None) -> list[TradeRecord]:
        """成交历史（按用户隔离）"""
        con = self._connect()
        try:
            if symbol:
                rows = con.execute(
                    "SELECT trade_id, local_user_id, symbol, side, quantity, price, traded_at, created_at "
                    "FROM manual_trade_ledger WHERE local_user_id=? AND symbol=? ORDER BY traded_at DESC",
                    [local_user_id, symbol],
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT trade_id, local_user_id, symbol, side, quantity, price, traded_at, created_at "
                    "FROM manual_trade_ledger WHERE local_user_id=? ORDER BY traded_at DESC",
                    [local_user_id],
                ).fetchall()
        finally:
            con.close()
        return [
            TradeRecord(trade_id=r[0], local_user_id=r[1], symbol=r[2], side=r[3],
                        quantity=r[4], price=r[5], traded_at=r[6], created_at=r[7])
            for r in rows
        ]
