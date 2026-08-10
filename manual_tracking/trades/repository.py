from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import duckdb

from database.db import get_connection, initialize_database
from trading.schemas import (
    ManualLedgerEventType,
    ManualPosition,
    ManualPreviewStatus,
    ManualTrade,
    ManualTradeConfirmation,
    ManualTradePreview,
    ManualTradePreviewPayload,
)


class ManualTradeRepositoryError(RuntimeError):
    pass


class ManualTradeNotFoundError(ManualTradeRepositoryError):
    pass


class ManualPreviewNotFoundError(ManualTradeRepositoryError):
    pass


class ManualTradeConflictError(ManualTradeRepositoryError):
    pass


class ManualTradeIdentityError(ManualTradeRepositoryError):
    pass


class ManualPreviewExpiredError(ManualTradeRepositoryError):
    pass


class ManualPreviewIntegrityError(ManualTradeRepositoryError):
    pass


class ManualPreviewStateError(ManualTradeRepositoryError):
    pass


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class ManualTradeRepository:
    """Append-only manual-trade facts plus a read-time position projection."""

    def __init__(self) -> None:
        initialize_database()

    @staticmethod
    def _row_to_preview(row: tuple[Any, ...]) -> ManualTradePreview:
        payload = ManualTradePreviewPayload.model_validate(_json_load(row[3]))
        return ManualTradePreview(
            confirmation_id=row[0],
            requested_by=row[1],
            channel=row[2],
            payload=payload,
            payload_hash=row[4],
            expires_at=row[5],
            status=row[6],
            created_at=row[7],
            confirmed_at=row[8],
        )

    @staticmethod
    def _row_to_trade(row: tuple[Any, ...]) -> ManualTrade:
        return ManualTrade(
            trade_id=row[0],
            portfolio_id=row[1],
            client_trade_id=row[2],
            symbol=row[3],
            side=row[4],
            quantity=int(row[5]),
            price=float(row[6]),
            fees=float(row[7]),
            taxes=float(row[8]),
            traded_at=row[9],
            decision_id=row[10],
            source=row[11],
            verification_status=row[12],
            user_confirmed=bool(row[13]),
            created_at=row[14],
            created_by=row[15],
            correction_of_trade_id=row[16],
            notes=row[17],
        )

    @staticmethod
    def _trade_select() -> str:
        return """
            SELECT trade_id, portfolio_id, client_trade_id, symbol, side,
                   quantity, price, fees, taxes, traded_at, decision_id,
                   source, verification_status, user_confirmed, created_at,
                   created_by, correction_of_trade_id, notes
            FROM manual_trades
        """

    def _load_preview(
        self,
        connection: duckdb.DuckDBPyConnection,
        confirmation_id: str,
    ) -> ManualTradePreview:
        row = connection.execute(
            """
            SELECT confirmation_id, requested_by, channel, payload_json,
                   payload_hash, expires_at, status, created_at, confirmed_at
            FROM manual_trade_previews
            WHERE confirmation_id = ?
            """,
            [confirmation_id],
        ).fetchone()
        if row is None:
            raise ManualPreviewNotFoundError(
                f"manual trade preview not found: {confirmation_id}"
            )
        try:
            return self._row_to_preview(row)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ManualPreviewIntegrityError(
                f"manual trade preview cannot be decoded: {exc}"
            ) from exc

    def _load_trade(
        self,
        connection: duckdb.DuckDBPyConnection,
        trade_id: str,
    ) -> ManualTrade:
        row = connection.execute(
            self._trade_select() + " WHERE trade_id = ?",
            [trade_id],
        ).fetchone()
        if row is None:
            raise ManualTradeNotFoundError(f"manual trade not found: {trade_id}")
        return self._row_to_trade(row)

    def create_preview(
        self,
        *,
        payload: ManualTradePreviewPayload,
        requested_by: str,
        channel: str,
        expires_at: datetime,
        created_at: datetime,
    ) -> ManualTradePreview:
        requested_by = requested_by.strip()
        channel = channel.strip()
        if not requested_by or not channel:
            raise ManualTradeIdentityError(
                "authenticated user and channel are required"
            )
        if expires_at <= created_at:
            raise ManualPreviewExpiredError("preview expiry must be in the future")
        confirmation_id = uuid4().hex
        payload_hash = payload.calculate_hash()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO manual_trade_previews (
                    confirmation_id, requested_by, channel, payload_json,
                    payload_hash, expires_at, status, created_at, confirmed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, NULL)
                """,
                [
                    confirmation_id,
                    requested_by,
                    channel,
                    payload.canonical_json(),
                    payload_hash,
                    expires_at,
                    created_at,
                ],
            )
        return ManualTradePreview(
            confirmation_id=confirmation_id,
            requested_by=requested_by,
            channel=channel,
            payload=payload,
            payload_hash=payload_hash,
            expires_at=expires_at,
            status=ManualPreviewStatus.PENDING,
            created_at=created_at,
        )

    def get_preview(
        self,
        confirmation_id: str,
        *,
        now: datetime,
    ) -> ManualTradePreview:
        with get_connection() as connection:
            preview = self._load_preview(connection, confirmation_id)
            if (
                preview.status == ManualPreviewStatus.PENDING
                and preview.expires_at <= now
            ):
                connection.execute(
                    """
                    UPDATE manual_trade_previews
                    SET status = 'EXPIRED'
                    WHERE confirmation_id = ? AND status = 'PENDING'
                    """,
                    [confirmation_id],
                )
                preview = preview.model_copy(
                    update={"status": ManualPreviewStatus.EXPIRED}
                )
        return preview

    @staticmethod
    def _validate_actor(
        preview: ManualTradePreview,
        *,
        actor_id: str,
        channel: str,
    ) -> None:
        if preview.requested_by != actor_id or preview.channel != channel:
            raise ManualTradeIdentityError(
                "only the authenticated preview creator may perform this action"
            )

    def cancel_preview(
        self,
        confirmation_id: str,
        *,
        actor_id: str,
        channel: str,
        now: datetime,
    ) -> ManualTradePreview:
        with get_connection() as connection:
            preview = self._load_preview(connection, confirmation_id)
            self._validate_actor(preview, actor_id=actor_id, channel=channel)
            if preview.status == ManualPreviewStatus.CANCELLED:
                return preview
            if preview.status == ManualPreviewStatus.CONFIRMED:
                raise ManualPreviewStateError("confirmed preview cannot be cancelled")
            if preview.status == ManualPreviewStatus.EXPIRED or preview.expires_at <= now:
                if preview.status == ManualPreviewStatus.PENDING:
                    connection.execute(
                        """
                        UPDATE manual_trade_previews
                        SET status = 'EXPIRED'
                        WHERE confirmation_id = ? AND status = 'PENDING'
                        """,
                        [confirmation_id],
                    )
                raise ManualPreviewExpiredError("manual trade preview has expired")
            connection.execute(
                """
                UPDATE manual_trade_previews
                SET status = 'CANCELLED'
                WHERE confirmation_id = ? AND status = 'PENDING'
                """,
                [confirmation_id],
            )
        return preview.model_copy(update={"status": ManualPreviewStatus.CANCELLED})

    def _confirmed_trade_for_preview(
        self,
        connection: duckdb.DuckDBPyConnection,
        confirmation_id: str,
    ) -> ManualTrade:
        row = connection.execute(
            """
            SELECT trade_id
            FROM manual_trade_reviews
            WHERE confirmation_id = ? AND review_type = 'CONFIRMATION'
            ORDER BY created_at
            LIMIT 1
            """,
            [confirmation_id],
        ).fetchone()
        if row is None:
            raise ManualPreviewIntegrityError(
                "confirmed preview has no confirmation audit record"
            )
        return self._load_trade(connection, row[0])

    def _insert_confirmed_trade(
        self,
        connection: duckdb.DuckDBPyConnection,
        *,
        preview: ManualTradePreview,
        actor_id: str,
        now: datetime,
    ) -> ManualTrade:
        payload = preview.payload
        trade_input = payload.trade
        existing = connection.execute(
            """
            SELECT trade_id
            FROM manual_trades
            WHERE portfolio_id = ? AND client_trade_id = ?
            """,
            [trade_input.portfolio_id, trade_input.client_trade_id],
        ).fetchone()
        if existing is not None:
            raise ManualTradeConflictError(
                "client_trade_id already exists in this portfolio"
            )

        if payload.event_type != ManualLedgerEventType.TRADE:
            original = self._load_trade(
                connection,
                payload.correction_of_trade_id or "",
            )
            if (
                original.portfolio_id != trade_input.portfolio_id
                or original.symbol != trade_input.symbol
            ):
                raise ManualPreviewIntegrityError(
                    "correction preview no longer matches its original trade"
                )
            previous_correction = connection.execute(
                """
                SELECT correction_id
                FROM manual_trade_corrections
                WHERE original_trade_id = ?
                """,
                [original.trade_id],
            ).fetchone()
            if previous_correction is not None:
                raise ManualTradeConflictError(
                    "the referenced trade already has a correction or reversal"
                )

        trade = ManualTrade(
            trade_id=uuid4().hex,
            portfolio_id=trade_input.portfolio_id,
            client_trade_id=trade_input.client_trade_id,
            symbol=trade_input.symbol,
            side=trade_input.side,
            quantity=trade_input.quantity,
            price=trade_input.price,
            fees=trade_input.fees,
            taxes=trade_input.taxes,
            traded_at=trade_input.traded_at,
            decision_id=trade_input.decision_id,
            source=trade_input.source,
            user_confirmed=True,
            created_at=now,
            created_by=actor_id,
            correction_of_trade_id=payload.correction_of_trade_id,
            notes=trade_input.notes,
        )
        connection.execute(
            """
            INSERT INTO manual_trades (
                trade_id, portfolio_id, client_trade_id, symbol, side,
                quantity, price, fees, taxes, traded_at, decision_id,
                source, verification_status, user_confirmed, created_at,
                created_by, correction_of_trade_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                trade.trade_id,
                trade.portfolio_id,
                trade.client_trade_id,
                trade.symbol,
                trade.side.value,
                trade.quantity,
                trade.price,
                trade.fees,
                trade.taxes,
                trade.traded_at,
                trade.decision_id,
                trade.source.value,
                trade.verification_status.value,
                trade.user_confirmed,
                trade.created_at,
                trade.created_by,
                trade.correction_of_trade_id,
                trade.notes,
            ],
        )
        if payload.event_type != ManualLedgerEventType.TRADE:
            connection.execute(
                """
                INSERT INTO manual_trade_corrections (
                    correction_id, original_trade_id, correction_trade_id,
                    correction_type, reason, created_at, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    uuid4().hex,
                    payload.correction_of_trade_id,
                    trade.trade_id,
                    payload.event_type.value,
                    payload.correction_reason,
                    now,
                    actor_id,
                ],
            )
        return trade

    def confirm_preview(
        self,
        confirmation_id: str,
        *,
        actor_id: str,
        channel: str,
        now: datetime,
    ) -> ManualTradeConfirmation:
        committed = False
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                preview = self._load_preview(connection, confirmation_id)
                self._validate_actor(preview, actor_id=actor_id, channel=channel)
                if preview.payload.calculate_hash() != preview.payload_hash:
                    raise ManualPreviewIntegrityError(
                        "manual trade preview payload hash mismatch"
                    )
                if preview.status == ManualPreviewStatus.CONFIRMED:
                    trade = self._confirmed_trade_for_preview(
                        connection,
                        confirmation_id,
                    )
                    connection.execute("COMMIT")
                    committed = True
                    return ManualTradeConfirmation(
                        preview=preview,
                        trade=trade,
                        idempotent_replay=True,
                    )
                if preview.status == ManualPreviewStatus.CANCELLED:
                    raise ManualPreviewStateError("cancelled preview cannot be confirmed")
                if preview.status == ManualPreviewStatus.EXPIRED or preview.expires_at <= now:
                    if preview.status == ManualPreviewStatus.PENDING:
                        connection.execute(
                            """
                            UPDATE manual_trade_previews
                            SET status = 'EXPIRED'
                            WHERE confirmation_id = ? AND status = 'PENDING'
                            """,
                            [confirmation_id],
                        )
                    connection.execute("COMMIT")
                    committed = True
                    raise ManualPreviewExpiredError("manual trade preview has expired")
                if preview.status != ManualPreviewStatus.PENDING:
                    raise ManualPreviewStateError(
                        f"preview status is not confirmable: {preview.status}"
                    )
                trade = self._insert_confirmed_trade(
                    connection,
                    preview=preview,
                    actor_id=actor_id,
                    now=now,
                )
                updated_count = connection.execute(
                    """
                    UPDATE manual_trade_previews
                    SET status = 'CONFIRMED', confirmed_at = ?
                    WHERE confirmation_id = ? AND status = 'PENDING'
                    RETURNING confirmation_id
                    """,
                    [now, confirmation_id],
                ).fetchall()
                if len(updated_count) != 1:
                    raise ManualPreviewStateError(
                        "preview was changed before confirmation completed"
                    )
                connection.execute(
                    """
                    INSERT INTO manual_trade_reviews (
                        review_id, confirmation_id, trade_id, review_type,
                        review_json, created_at, created_by
                    )
                    VALUES (?, ?, ?, 'CONFIRMATION', ?, ?, ?)
                    """,
                    [
                        uuid4().hex,
                        confirmation_id,
                        trade.trade_id,
                        _json_dump(
                            {
                                "actor_id": actor_id,
                                "channel": channel,
                                "event_type": preview.payload.event_type.value,
                                "payload_hash": preview.payload_hash,
                            }
                        ),
                        now,
                        actor_id,
                    ],
                )
                connection.execute("COMMIT")
                committed = True
            except Exception:
                if not committed:
                    connection.execute("ROLLBACK")
                raise
        confirmed_preview = preview.model_copy(
            update={
                "status": ManualPreviewStatus.CONFIRMED,
                "confirmed_at": now,
            }
        )
        return ManualTradeConfirmation(
            preview=confirmed_preview,
            trade=trade,
        )

    def get_trade(self, trade_id: str) -> ManualTrade:
        with get_connection() as connection:
            return self._load_trade(connection, trade_id)

    def list_trades(
        self,
        *,
        portfolio_id: str | None = None,
        symbol: str | None = None,
    ) -> list[ManualTrade]:
        clauses: list[str] = []
        parameters: list[str] = []
        if portfolio_id:
            clauses.append("portfolio_id = ?")
            parameters.append(portfolio_id)
        if symbol:
            clauses.append("symbol = ?")
            parameters.append(symbol)
        query = self._trade_select()
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY traded_at, created_at, trade_id"
        with get_connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_trade(row) for row in rows]

    def _inactive_trade_ids(self) -> set[str]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT original_trade_id, correction_trade_id, correction_type
                FROM manual_trade_corrections
                """
            ).fetchall()
        inactive: set[str] = set()
        for original_trade_id, correction_trade_id, correction_type in rows:
            inactive.add(original_trade_id)
            if correction_type == ManualLedgerEventType.REVERSAL.value:
                inactive.add(correction_trade_id)
        return inactive

    @staticmethod
    def _position_id(portfolio_id: str, symbol: str) -> str:
        return uuid5(
            NAMESPACE_URL,
            f"manual-position:{portfolio_id}:{symbol}",
        ).hex

    @staticmethod
    def _project_group(
        *,
        portfolio_id: str,
        symbol: str,
        history: list[ManualTrade],
        inactive_trade_ids: set[str],
    ) -> ManualPosition:
        effective = [
            trade for trade in history if trade.trade_id not in inactive_trade_ids
        ]
        quantity = 0
        average_cost = 0.0
        realized_pnl = 0.0
        total_fees = 0.0
        total_taxes = 0.0
        for trade in effective:
            charges = trade.fees + trade.taxes
            total_fees += trade.fees
            total_taxes += trade.taxes
            if trade.side.value == "BUY":
                if quantity >= 0:
                    new_quantity = quantity + trade.quantity
                    average_cost = (
                        (average_cost * quantity)
                        + (trade.price * trade.quantity)
                        + charges
                    ) / new_quantity
                    quantity = new_quantity
                    continue
                closing = min(trade.quantity, -quantity)
                realized_pnl += closing * (average_cost - trade.price) - charges
                quantity += trade.quantity
                if quantity > 0:
                    average_cost = trade.price
                elif quantity == 0:
                    average_cost = 0.0
                continue

            if quantity <= 0:
                previous_short = -quantity
                new_short = previous_short + trade.quantity
                average_cost = (
                    (average_cost * previous_short)
                    + (trade.price * trade.quantity)
                    - charges
                ) / new_short
                quantity -= trade.quantity
                continue
            closing = min(trade.quantity, quantity)
            realized_pnl += closing * (trade.price - average_cost) - charges
            quantity -= trade.quantity
            if quantity < 0:
                average_cost = trade.price
            elif quantity == 0:
                average_cost = 0.0

        return ManualPosition(
            position_id=ManualTradeRepository._position_id(portfolio_id, symbol),
            portfolio_id=portfolio_id,
            symbol=symbol,
            quantity=quantity,
            average_cost=max(0.0, average_cost),
            realized_pnl=realized_pnl,
            total_fees=total_fees,
            total_taxes=total_taxes,
            effective_trade_count=len(effective),
            source_trade_ids=[trade.trade_id for trade in history],
            last_traded_at=max(trade.traded_at for trade in history),
        )

    def list_positions(
        self,
        *,
        portfolio_id: str | None = None,
        symbol: str | None = None,
    ) -> list[ManualPosition]:
        trades = self.list_trades(portfolio_id=portfolio_id, symbol=symbol)
        groups: dict[tuple[str, str], list[ManualTrade]] = {}
        for trade in trades:
            groups.setdefault((trade.portfolio_id, trade.symbol), []).append(trade)
        inactive_trade_ids = self._inactive_trade_ids()
        return [
            self._project_group(
                portfolio_id=key[0],
                symbol=key[1],
                history=history,
                inactive_trade_ids=inactive_trade_ids,
            )
            for key, history in sorted(groups.items())
        ]

    def get_position(self, position_id: str) -> ManualPosition:
        for position in self.list_positions():
            if position.position_id == position_id:
                return position
        raise ManualTradeNotFoundError(
            f"manual position not found: {position_id}"
        )
