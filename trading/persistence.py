from __future__ import annotations

import json
from datetime import date, datetime

from database.db import get_connection, initialize_database
from trading.schemas import DailyReview, DecisionTrace, OrderResult, PaperAccount


class TradingAuditStore:
    def record_trace(self, trace: DecisionTrace) -> None:
        initialize_database()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO decision_traces
                    (trace_id, symbol, as_of_date, final_action, vetoed, trace_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    trace.trace_id,
                    trace.symbol,
                    trace.as_of,
                    trace.final_action.value,
                    trace.risk_verdict.vetoed,
                    trace.model_dump_json(),
                    trace.created_at,
                ],
            )

    def record_order(self, order: OrderResult, mode: str = "paper") -> None:
        initialize_database()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO trading_orders
                    (order_id, client_order_id, symbol, side, quantity, mode, status,
                     fill_price, fee, reason, order_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    order.order_id,
                    order.client_order_id,
                    order.symbol,
                    order.side.value,
                    order.quantity,
                    mode,
                    order.status.value,
                    order.fill_price,
                    order.fee,
                    order.reason,
                    order.model_dump_json(),
                    order.created_at,
                ],
            )

    def daily_review(self, review_date: date) -> DailyReview:
        initialize_database()
        with get_connection() as connection:
            trace_rows = connection.execute(
                "SELECT trace_json FROM decision_traces WHERE CAST(created_at AS DATE) = ? "
                "ORDER BY created_at",
                [review_date],
            ).fetchall()
            order_rows = connection.execute(
                "SELECT order_json FROM trading_orders WHERE CAST(created_at AS DATE) = ? "
                "ORDER BY created_at",
                [review_date],
            ).fetchall()
        traces = [DecisionTrace.model_validate(json.loads(row[0])) for row in trace_rows]
        orders = [OrderResult.model_validate(json.loads(row[0])) for row in order_rows]
        veto_count = sum(trace.risk_verdict.vetoed for trace in traces)
        filled = sum(order.status.value == "filled" for order in orders)
        return DailyReview(
            review_date=review_date,
            traces=traces,
            orders=orders,
            veto_count=veto_count,
            summary=[
                f"{len(traces)} decision traces recorded.",
                f"{veto_count} decisions were vetoed by deterministic risk rules.",
                f"{filled} paper orders were filled.",
            ],
        )

    def record_paper_account(self, account: PaperAccount) -> None:
        initialize_database()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO paper_accounts (account_id, account_json, updated_at)
                VALUES ('default', ?, ?)
                """,
                [account.model_dump_json(), datetime.now().astimezone()],
            )

    def load_paper_account(self) -> PaperAccount | None:
        initialize_database()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT account_json FROM paper_accounts WHERE account_id = 'default'"
            ).fetchone()
        if row is None:
            return None
        return PaperAccount.model_validate(json.loads(row[0]))
