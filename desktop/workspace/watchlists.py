from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from desktop.workspace.safety import sanitize_user_visible_text
from desktop.workspace.state import DesktopStateRepository


@dataclass(frozen=True, slots=True)
class Watchlist:
    watchlist_id: str
    name: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    item_id: str
    watchlist_id: str
    symbol: str
    tags: tuple[str, ...]
    note: str
    source_task_id: str | None
    added_at: datetime
    last_researched_at: datetime | None
    last_decision_status: str | None
    risk_flags: tuple[str, ...]
    freshness: str


class WatchlistRepository:
    def __init__(self, state: DesktopStateRepository) -> None:
        self.state = state
        with self.state._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS watchlists (
                    watchlist_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS watchlist_items (
                    item_id TEXT PRIMARY KEY,
                    watchlist_id TEXT NOT NULL
                        REFERENCES watchlists(watchlist_id) ON DELETE CASCADE,
                    symbol TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    note TEXT NOT NULL,
                    source_task_id TEXT,
                    added_at TEXT NOT NULL,
                    last_researched_at TEXT,
                    last_decision_status TEXT,
                    risk_flags_json TEXT NOT NULL,
                    freshness TEXT NOT NULL,
                    UNIQUE(watchlist_id, symbol)
                );
                """
            )

    def create(self, name: str) -> Watchlist:
        clean = sanitize_user_visible_text(name).strip()[:120]
        if not clean:
            raise ValueError("watchlist name must not be empty")
        now = datetime.now().astimezone()
        watchlist = Watchlist(
            watchlist_id="wl_" + uuid4().hex[:24],
            name=clean,
            created_at=now,
            updated_at=now,
        )
        with self.state._connect() as connection:
            connection.execute(
                """
                INSERT INTO watchlists(watchlist_id,name,created_at,updated_at)
                VALUES(?,?,?,?)
                """,
                [
                    watchlist.watchlist_id,
                    watchlist.name,
                    now.isoformat(),
                    now.isoformat(),
                ],
            )
        return watchlist

    def list_watchlists(self) -> list[Watchlist]:
        with self.state._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM watchlists ORDER BY updated_at DESC"
            ).fetchall()
        return [
            Watchlist(
                watchlist_id=row["watchlist_id"],
                name=row["name"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    def rename(self, watchlist_id: str, name: str) -> Watchlist:
        clean = sanitize_user_visible_text(name).strip()[:120]
        if not clean:
            raise ValueError("watchlist name must not be empty")
        now = datetime.now().astimezone()
        with self.state._connect() as connection:
            changed = connection.execute(
                """
                UPDATE watchlists SET name=?, updated_at=?
                WHERE watchlist_id=?
                """,
                [clean, now.isoformat(), watchlist_id],
            ).rowcount
            row = connection.execute(
                "SELECT * FROM watchlists WHERE watchlist_id=?",
                [watchlist_id],
            ).fetchone()
        if not changed or row is None:
            raise KeyError("watchlist not found")
        return Watchlist(
            watchlist_id=row["watchlist_id"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def delete(self, watchlist_id: str) -> bool:
        with self.state._connect() as connection:
            return (
                connection.execute(
                    "DELETE FROM watchlists WHERE watchlist_id=?",
                    [watchlist_id],
                ).rowcount
                > 0
            )

    def add_symbol(
        self,
        watchlist_id: str,
        symbol: str,
        *,
        tags: list[str] | None = None,
        note: str = "",
        source_task_id: str | None = None,
    ) -> WatchlistItem:
        normalized = symbol.strip().upper()
        if not __import__("re").fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", normalized):
            raise ValueError("invalid A-share symbol")
        clean_tags = tuple(
            dict.fromkeys(
                sanitize_user_visible_text(item).strip()[:40]
                for item in (tags or [])
                if item.strip()
            )
        )
        clean_note = sanitize_user_visible_text(note).strip()[:1000]
        now = datetime.now().astimezone()
        item = WatchlistItem(
            item_id="wli_" + uuid4().hex[:24],
            watchlist_id=watchlist_id,
            symbol=normalized,
            tags=clean_tags,
            note=clean_note,
            source_task_id=source_task_id,
            added_at=now,
            last_researched_at=None,
            last_decision_status=None,
            risk_flags=(),
            freshness="MISSING",
        )
        with self.state._connect() as connection:
            connection.execute(
                """
                INSERT INTO watchlist_items
                (item_id,watchlist_id,symbol,tags_json,note,source_task_id,
                 added_at,last_researched_at,last_decision_status,
                 risk_flags_json,freshness)
                VALUES(?,?,?,?,?,?,?,NULL,NULL,'[]','MISSING')
                """,
                [
                    item.item_id,
                    watchlist_id,
                    normalized,
                    json.dumps(clean_tags, ensure_ascii=False),
                    clean_note,
                    source_task_id,
                    now.isoformat(),
                ],
            )
            connection.execute(
                "UPDATE watchlists SET updated_at=? WHERE watchlist_id=?",
                [now.isoformat(), watchlist_id],
            )
        return item

    def remove_symbol(self, watchlist_id: str, symbol: str) -> bool:
        with self.state._connect() as connection:
            return (
                connection.execute(
                    "DELETE FROM watchlist_items WHERE watchlist_id=? AND symbol=?",
                    [watchlist_id, symbol.upper()],
                ).rowcount
                > 0
            )

    def update_symbol(
        self,
        watchlist_id: str,
        symbol: str,
        *,
        tags: list[str],
        note: str,
    ) -> WatchlistItem:
        clean_tags = tuple(
            dict.fromkeys(
                sanitize_user_visible_text(item).strip()[:40]
                for item in tags
                if item.strip()
            )
        )
        clean_note = sanitize_user_visible_text(note).strip()[:1000]
        with self.state._connect() as connection:
            changed = connection.execute(
                """
                UPDATE watchlist_items SET tags_json=?, note=?
                WHERE watchlist_id=? AND symbol=?
                """,
                [
                    json.dumps(clean_tags, ensure_ascii=False),
                    clean_note,
                    watchlist_id,
                    symbol.upper(),
                ],
            ).rowcount
        if not changed:
            raise KeyError("watchlist item not found")
        return next(
            item
            for item in self.items(watchlist_id)
            if item.symbol == symbol.upper()
        )

    def update_research_state(
        self,
        watchlist_id: str,
        symbol: str,
        *,
        researched_at: datetime,
        decision_status: str | None,
        risk_flags: list[str],
        freshness: str,
    ) -> None:
        if freshness not in {"FRESH", "STALE", "MISSING"}:
            raise ValueError("invalid freshness")
        with self.state._connect() as connection:
            changed = connection.execute(
                """
                UPDATE watchlist_items
                SET last_researched_at=?, last_decision_status=?,
                    risk_flags_json=?, freshness=?
                WHERE watchlist_id=? AND symbol=?
                """,
                [
                    researched_at.isoformat(),
                    decision_status,
                    json.dumps(risk_flags, ensure_ascii=False),
                    freshness,
                    watchlist_id,
                    symbol.upper(),
                ],
            ).rowcount
        if not changed:
            raise KeyError("watchlist item not found")

    def items(self, watchlist_id: str) -> list[WatchlistItem]:
        with self.state._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM watchlist_items
                WHERE watchlist_id=? ORDER BY added_at
                """,
                [watchlist_id],
            ).fetchall()
        return [
            WatchlistItem(
                item_id=row["item_id"],
                watchlist_id=row["watchlist_id"],
                symbol=row["symbol"],
                tags=tuple(json.loads(row["tags_json"])),
                note=row["note"],
                source_task_id=row["source_task_id"],
                added_at=datetime.fromisoformat(row["added_at"]),
                last_researched_at=(
                    datetime.fromisoformat(row["last_researched_at"])
                    if row["last_researched_at"]
                    else None
                ),
                last_decision_status=row["last_decision_status"],
                risk_flags=tuple(json.loads(row["risk_flags_json"])),
                freshness=row["freshness"],
            )
            for row in rows
        ]

    def research_symbols(self, watchlist_id: str) -> list[str]:
        symbols = [item.symbol for item in self.items(watchlist_id)]
        if len(symbols) > 30:
            raise ValueError("batch research accepts at most 30 symbols")
        return symbols

    def scan_symbols(self, watchlist_id: str) -> list[str]:
        return [item.symbol for item in self.items(watchlist_id)]

    def export_csv(self, watchlist_id: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "symbol",
                    "tags",
                    "note",
                    "source_task_id",
                    "added_at",
                    "last_researched_at",
                    "last_decision_status",
                    "risk_flags",
                    "freshness",
                ]
            )
            for item in self.items(watchlist_id):
                writer.writerow(
                    [
                        item.symbol,
                        ",".join(item.tags),
                        item.note,
                        item.source_task_id or "",
                        item.added_at.isoformat(),
                        (
                            item.last_researched_at.isoformat()
                            if item.last_researched_at
                            else ""
                        ),
                        item.last_decision_status or "",
                        ",".join(item.risk_flags),
                        item.freshness,
                    ]
                )
        return destination


__all__ = ["Watchlist", "WatchlistItem", "WatchlistRepository"]
