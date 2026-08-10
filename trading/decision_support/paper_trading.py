"""Paper 账户模拟交易（阶段2.5）— local_user_id 隔离

支持：模拟建仓/加仓/减仓/清仓/止损，计算手续费、收益、回撤和持仓。
Paper 交易必须明确标注"模拟交易"，不得与真实持仓混淆。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

TZ = timezone(timedelta(hours=8))

COMMISSION_RATE = 0.0003   # 万三佣金
STAMP_TAX_RATE = 0.0005    # 卖出印花税（0.05%）


@dataclass
class PaperAccount:
    local_user_id: str
    cash: float = 1_000_000.0   # 初始资金
    positions: dict = field(default_factory=dict)  # symbol -> {quantity, avg_cost}
    initial_cash: float = 1_000_000.0
    realized_pnl: float = 0.0


@dataclass
class PaperOrder:
    order_id: str
    local_user_id: str
    symbol: str
    side: str               # BUY / SELL
    quantity: float
    price: float
    fee: float
    created_at: datetime
    status: str = "FILLED"


class PaperTradingService:
    """Paper 模拟交易（每用户独立账户）"""

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
            CREATE TABLE IF NOT EXISTS paper_accounts_v2 (
                local_user_id VARCHAR PRIMARY KEY,
                cash DOUBLE DEFAULT 1000000,
                initial_cash DOUBLE DEFAULT 1000000,
                realized_pnl DOUBLE DEFAULT 0,
                positions_json VARCHAR,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS paper_orders (
                order_id VARCHAR PRIMARY KEY,
                local_user_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                quantity DOUBLE NOT NULL,
                price DOUBLE NOT NULL,
                fee DOUBLE DEFAULT 0,
                status VARCHAR DEFAULT 'FILLED',
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        return con

    # ------------------------------------------------------------------
    def get_account(self, local_user_id: str) -> PaperAccount:
        import json
        con = self._connect()
        try:
            row = con.execute(
                "SELECT cash, initial_cash, realized_pnl, positions_json "
                "FROM paper_accounts_v2 WHERE local_user_id=?",
                [local_user_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return PaperAccount(local_user_id=local_user_id)
        positions = json.loads(row[3]) if row[3] else {}
        return PaperAccount(
            local_user_id=local_user_id, cash=row[0], positions=positions,
            initial_cash=row[1], realized_pnl=row[2],
        )

    def _save(self, account: PaperAccount) -> None:
        import json
        con = self._connect()
        try:
            con.execute(
                """
                INSERT OR REPLACE INTO paper_accounts_v2
                (local_user_id, cash, initial_cash, realized_pnl, positions_json, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                [account.local_user_id, account.cash, account.initial_cash,
                 account.realized_pnl, json.dumps(account.positions, ensure_ascii=False)],
            )
        finally:
            con.close()

    # ------------------------------------------------------------------
    # 模拟交易
    # ------------------------------------------------------------------
    def buy(self, local_user_id: str, symbol: str, quantity: float,
            price: float) -> PaperOrder:
        """模拟建仓/加仓"""
        account = self.get_account(local_user_id)
        amount = quantity * price
        fee = amount * COMMISSION_RATE
        total_cost = amount + fee
        if total_cost > account.cash:
            raise ValueError("现金不足（Paper账户）")

        pos = account.positions.get(symbol, {"quantity": 0, "avg_cost": 0.0})
        old_qty, old_cost = pos["quantity"], pos["avg_cost"]
        new_qty = old_qty + quantity
        new_cost = (old_qty * old_cost + amount) / new_qty if new_qty > 0 else price
        account.positions[symbol] = {"quantity": new_qty, "avg_cost": new_cost}
        account.cash -= total_cost
        self._save(account)

        order = PaperOrder(
            order_id=f"po_{uuid.uuid4().hex[:16]}", local_user_id=local_user_id,
            symbol=symbol, side="BUY", quantity=quantity, price=price,
            fee=fee, created_at=datetime.now(tz=TZ),
        )
        self._record_order(order)
        return order

    def sell(self, local_user_id: str, symbol: str, quantity: float,
             price: float) -> PaperOrder:
        """模拟减仓/清仓/止损"""
        account = self.get_account(local_user_id)
        pos = account.positions.get(symbol)
        if pos is None or pos["quantity"] < quantity:
            raise ValueError("持仓不足（Paper账户）")

        amount = quantity * price
        fee = amount * COMMISSION_RATE + amount * STAMP_TAX_RATE
        avg_cost = pos["avg_cost"]
        pnl = (price - avg_cost) * quantity - fee

        new_qty = pos["quantity"] - quantity
        if new_qty <= 0:
            del account.positions[symbol]
        else:
            account.positions[symbol] = {"quantity": new_qty, "avg_cost": avg_cost}
        account.cash += amount - fee
        account.realized_pnl += pnl
        self._save(account)

        order = PaperOrder(
            order_id=f"po_{uuid.uuid4().hex[:16]}", local_user_id=local_user_id,
            symbol=symbol, side="SELL", quantity=quantity, price=price,
            fee=fee, created_at=datetime.now(tz=TZ),
        )
        self._record_order(order)
        return order

    def close_all(self, local_user_id: str, symbol: str, price: float) -> list[PaperOrder]:
        """清仓某股票"""
        account = self.get_account(local_user_id)
        pos = account.positions.get(symbol)
        if pos is None or pos["quantity"] <= 0:
            return []
        qty = pos["quantity"]
        order = self.sell(local_user_id, symbol, qty, price)
        return [order]

    def _record_order(self, order: PaperOrder) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO paper_orders (order_id, local_user_id, symbol, side, quantity, price, fee, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [order.order_id, order.local_user_id, order.symbol, order.side,
                 order.quantity, order.price, order.fee, order.status],
            )
        finally:
            con.close()

    def portfolio_value(self, local_user_id: str, prices: dict[str, float]) -> float:
        """组合市值 = 现金 + 持仓市值"""
        account = self.get_account(local_user_id)
        mv = account.cash
        for symbol, pos in account.positions.items():
            p = prices.get(symbol, pos["avg_cost"])
            mv += pos["quantity"] * p
        return mv

    def list_orders(self, local_user_id: str) -> list[PaperOrder]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT order_id, local_user_id, symbol, side, quantity, price, fee, status, created_at "
                "FROM paper_orders WHERE local_user_id=? ORDER BY created_at DESC",
                [local_user_id],
            ).fetchall()
        finally:
            con.close()
        return [
            PaperOrder(order_id=r[0], local_user_id=r[1], symbol=r[2], side=r[3],
                       quantity=r[4], price=r[5], fee=r[6], status=r[7], created_at=r[8])
            for r in rows
        ]
