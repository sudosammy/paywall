"""SQLite persistence for members, disclosures, FX rates, and settings."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class Member:
    slack_user_id: str
    display_name: str
    status: str
    joined_at: datetime
    last_validated_at: datetime | None
    last_reminded_at: datetime | None


@dataclass
class Grant:
    id: int
    disclosure_id: int
    rsu_type: str
    equity_kind: str
    rsu_ticker: str | None
    rsu_shares_per_year: float | None
    rsu_share_price: float | None
    rsu_share_currency: str | None
    rsu_strike_price: float | None
    rsu_amount: float | None
    rsu_currency: str | None
    rsu_note: str | None
    rsu_aud: float | None


@dataclass
class Disclosure:
    id: int
    slack_user_id: str
    created_at: datetime
    base_amount: float
    base_currency: str
    super_type: str
    super_pct: float | None
    bonus_type: str
    bonus_value: float | None
    bonus_currency: str | None
    bonus_note: str | None
    grants: list[Grant]
    other_text: str | None
    base_aud: float
    bonus_aud: float | None
    fx_rate_date: str | None


@dataclass
class FxRate:
    source: str
    rate: float
    fetched_at: datetime


@dataclass
class StockPrice:
    symbol: str
    price: float
    currency: str
    fetched_at: datetime


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            had_grants_table = bool(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='grants'"
                ).fetchone()
            )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS members (
                    slack_user_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    joined_at TEXT NOT NULL,
                    last_validated_at TEXT,
                    last_reminded_at TEXT
                );

                CREATE TABLE IF NOT EXISTS disclosures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slack_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    base_amount REAL NOT NULL,
                    base_currency TEXT NOT NULL,
                    super_type TEXT NOT NULL,
                    super_pct REAL,
                    bonus_type TEXT NOT NULL,
                    bonus_value REAL,
                    bonus_currency TEXT,
                    bonus_note TEXT,
                    other_text TEXT,
                    base_aud REAL NOT NULL,
                    bonus_aud REAL,
                    fx_rate_date TEXT,
                    FOREIGN KEY (slack_user_id) REFERENCES members(slack_user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_disclosures_user_created
                    ON disclosures(slack_user_id, created_at DESC);

                -- A disclosure can carry multiple concurrent equity grants
                -- (e.g. a new-hire grant plus yearly top-ups), each valued
                -- independently since tickers/vesting/quantities can differ.
                CREATE TABLE IF NOT EXISTS grants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    disclosure_id INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    rsu_type TEXT NOT NULL,
                    equity_kind TEXT NOT NULL DEFAULT 'rsu',
                    rsu_ticker TEXT,
                    rsu_shares_per_year REAL,
                    rsu_share_price REAL,
                    rsu_share_currency TEXT,
                    rsu_strike_price REAL,
                    rsu_amount REAL,
                    rsu_currency TEXT,
                    rsu_note TEXT,
                    rsu_aud REAL,
                    FOREIGN KEY (disclosure_id) REFERENCES disclosures(id)
                );

                CREATE INDEX IF NOT EXISTS idx_grants_disclosure
                    ON grants(disclosure_id, slot);

                CREATE TABLE IF NOT EXISTS fx_rates (
                    source TEXT PRIMARY KEY,
                    rate REAL NOT NULL,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS stock_prices (
                    symbol TEXT PRIMARY KEY,
                    price REAL NOT NULL,
                    currency TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            if not had_grants_table:
                self._migrate_legacy_single_grant(conn)
            self._migrate_grants_options_columns(conn)

    @staticmethod
    def _migrate_legacy_single_grant(conn: sqlite3.Connection) -> None:
        """One-time backfill: pre-multi-grant DBs kept a single rsu_* set of
        columns directly on `disclosures`. Copy any such data into the new
        `grants` table (slot 1) so it isn't lost from the board. No-op on a
        fresh DB or one already past this migration (grants table pre-existed).
        """
        legacy_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(disclosures)").fetchall()
        }
        if "rsu_type" not in legacy_cols:
            return
        rows = conn.execute(
            """
            SELECT id, rsu_type, rsu_ticker, rsu_shares_per_year, rsu_share_price,
                   rsu_share_currency, rsu_amount, rsu_currency, rsu_note, rsu_aud
            FROM disclosures
            WHERE rsu_type IS NOT NULL AND rsu_type != 'none'
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT INTO grants (
                    disclosure_id, slot, rsu_type, rsu_ticker, rsu_shares_per_year,
                    rsu_share_price, rsu_share_currency, rsu_amount, rsu_currency,
                    rsu_note, rsu_aud
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["rsu_type"],
                    row["rsu_ticker"],
                    row["rsu_shares_per_year"],
                    row["rsu_share_price"],
                    row["rsu_share_currency"],
                    row["rsu_amount"],
                    row["rsu_currency"],
                    row["rsu_note"],
                    row["rsu_aud"],
                ),
            )

    @staticmethod
    def _migrate_grants_options_columns(conn: sqlite3.Connection) -> None:
        """Additive migration: pre-options grants only ever meant RSUs. Adds
        equity_kind (existing rows default to 'rsu' via the column default)
        and rsu_strike_price (used only for public options). No-op once both
        columns exist.
        """
        cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(grants)").fetchall()
        }
        if "equity_kind" not in cols:
            conn.execute(
                "ALTER TABLE grants ADD COLUMN equity_kind TEXT NOT NULL DEFAULT 'rsu'"
            )
        if "rsu_strike_price" not in cols:
            conn.execute("ALTER TABLE grants ADD COLUMN rsu_strike_price REAL")

    # --- settings ---

    def get_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    # --- members ---

    def upsert_member(
        self,
        slack_user_id: str,
        display_name: str,
        *,
        joined_at: datetime | None = None,
    ) -> Member:
        """Insert a new active member, or refresh an existing member's name.

        Status of existing members is intentionally left untouched; it is
        managed by join/leave events, ejection, and mark_rejoined.
        """
        now = joined_at or utcnow()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM members WHERE slack_user_id = ?",
                (slack_user_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE members SET display_name = ? WHERE slack_user_id = ?",
                    (display_name, slack_user_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO members (
                        slack_user_id, display_name, status, joined_at
                    ) VALUES (?, ?, 'active', ?)
                    """,
                    (slack_user_id, display_name, to_iso(now)),
                )
        member = self.get_member(slack_user_id)
        assert member is not None
        return member

    def get_member(self, slack_user_id: str) -> Member | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM members WHERE slack_user_id = ?",
                (slack_user_id,),
            ).fetchone()
            return self._row_to_member(row) if row else None

    def list_active_members(self) -> list[Member]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM members WHERE status = 'active' ORDER BY display_name COLLATE NOCASE"
            ).fetchall()
            return [self._row_to_member(r) for r in rows]

    def list_members(self) -> list[Member]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM members ORDER BY display_name COLLATE NOCASE"
            ).fetchall()
            return [self._row_to_member(r) for r in rows]

    def mark_rejoined(self, slack_user_id: str, at: datetime | None = None) -> None:
        """Reactivate a member who rejoined the channel.

        Resets joined_at so they get a fresh grace window instead of being
        instantly re-ejected over a stale disclosure.
        """
        when = at or utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE members
                SET status = 'active', joined_at = ?, last_reminded_at = NULL
                WHERE slack_user_id = ?
                """,
                (to_iso(when), slack_user_id),
            )

    def set_member_status(self, slack_user_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE members SET status = ? WHERE slack_user_id = ?",
                (status, slack_user_id),
            )

    def mark_validated(
        self, slack_user_id: str, at: datetime | None = None
    ) -> None:
        when = at or utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE members
                SET last_validated_at = ?, last_reminded_at = NULL
                WHERE slack_user_id = ?
                """,
                (to_iso(when), slack_user_id),
            )

    def mark_reminded(
        self, slack_user_id: str, at: datetime | None = None
    ) -> None:
        when = at or utcnow()
        with self.connect() as conn:
            conn.execute(
                "UPDATE members SET last_reminded_at = ? WHERE slack_user_id = ?",
                (to_iso(when), slack_user_id),
            )

    # --- disclosures ---

    def add_disclosure(self, slack_user_id: str, data: dict[str, Any]) -> Disclosure:
        created_at = utcnow()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO disclosures (
                    slack_user_id, created_at,
                    base_amount, base_currency,
                    super_type, super_pct,
                    bonus_type, bonus_value, bonus_currency, bonus_note,
                    other_text, base_aud, bonus_aud, fx_rate_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slack_user_id,
                    to_iso(created_at),
                    data["base_amount"],
                    data["base_currency"],
                    data["super_type"],
                    data.get("super_pct"),
                    data["bonus_type"],
                    data.get("bonus_value"),
                    data.get("bonus_currency"),
                    data.get("bonus_note"),
                    data.get("other_text"),
                    data["base_aud"],
                    data.get("bonus_aud"),
                    data.get("fx_rate_date"),
                ),
            )
            disclosure_id = cur.lastrowid
            for slot, grant in enumerate(data.get("grants") or [], start=1):
                conn.execute(
                    """
                    INSERT INTO grants (
                        disclosure_id, slot, rsu_type, equity_kind, rsu_ticker,
                        rsu_shares_per_year, rsu_share_price, rsu_share_currency,
                        rsu_strike_price, rsu_amount, rsu_currency, rsu_note, rsu_aud
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        disclosure_id,
                        slot,
                        grant["rsu_type"],
                        grant.get("equity_kind", "rsu"),
                        grant.get("rsu_ticker"),
                        grant.get("rsu_shares_per_year"),
                        grant.get("rsu_share_price"),
                        grant.get("rsu_share_currency"),
                        grant.get("rsu_strike_price"),
                        grant.get("rsu_amount"),
                        grant.get("rsu_currency"),
                        grant.get("rsu_note"),
                        grant.get("rsu_aud"),
                    ),
                )
            conn.execute(
                """
                UPDATE members
                SET last_validated_at = ?, last_reminded_at = NULL
                WHERE slack_user_id = ?
                """,
                (to_iso(created_at), slack_user_id),
            )
        disclosure = self.get_disclosure(disclosure_id)
        assert disclosure is not None
        return disclosure

    def get_disclosure(self, disclosure_id: int) -> Disclosure | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM disclosures WHERE id = ?", (disclosure_id,)
            ).fetchone()
            if not row:
                return None
            grants = self._load_grants(conn, disclosure_id)
            return self._row_to_disclosure(row, grants)

    def get_latest_disclosure(self, slack_user_id: str) -> Disclosure | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM disclosures
                WHERE slack_user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (slack_user_id,),
            ).fetchone()
            if not row:
                return None
            grants = self._load_grants(conn, row["id"])
            return self._row_to_disclosure(row, grants)

    @staticmethod
    def _load_grants(conn: sqlite3.Connection, disclosure_id: int) -> list[Grant]:
        rows = conn.execute(
            "SELECT * FROM grants WHERE disclosure_id = ? ORDER BY slot",
            (disclosure_id,),
        ).fetchall()
        return [Database._row_to_grant(r) for r in rows]

    def list_latest_disclosures_for_active(self) -> list[tuple[Member, Disclosure]]:
        members = self.list_active_members()
        result: list[tuple[Member, Disclosure]] = []
        for member in members:
            disclosure = self.get_latest_disclosure(member.slack_user_id)
            if disclosure:
                result.append((member, disclosure))
        return result

    # --- fx rates ---

    def get_fx_rate(self, source: str) -> FxRate | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM fx_rates WHERE source = ?", (source.upper(),)
            ).fetchone()
            if not row:
                return None
            return FxRate(
                source=row["source"],
                rate=row["rate"],
                fetched_at=from_iso(row["fetched_at"]),  # type: ignore[arg-type]
            )

    def set_fx_rate(
        self, source: str, rate: float, fetched_at: datetime | None = None
    ) -> None:
        when = fetched_at or utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO fx_rates(source, rate, fetched_at) VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    rate = excluded.rate,
                    fetched_at = excluded.fetched_at
                """,
                (source.upper(), rate, to_iso(when)),
            )

    # --- stock prices ---

    def get_stock_price(self, symbol: str) -> StockPrice | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM stock_prices WHERE symbol = ?", (symbol.upper(),)
            ).fetchone()
            if not row:
                return None
            return StockPrice(
                symbol=row["symbol"],
                price=row["price"],
                currency=row["currency"],
                fetched_at=from_iso(row["fetched_at"]),  # type: ignore[arg-type]
            )

    def set_stock_price(
        self,
        symbol: str,
        price: float,
        currency: str,
        fetched_at: datetime | None = None,
    ) -> None:
        when = fetched_at or utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO stock_prices(symbol, price, currency, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    price = excluded.price,
                    currency = excluded.currency,
                    fetched_at = excluded.fetched_at
                """,
                (symbol.upper(), price, currency.upper(), to_iso(when)),
            )

    # --- row helpers ---

    @staticmethod
    def _row_to_member(row: sqlite3.Row) -> Member:
        return Member(
            slack_user_id=row["slack_user_id"],
            display_name=row["display_name"],
            status=row["status"],
            joined_at=from_iso(row["joined_at"]),  # type: ignore[arg-type]
            last_validated_at=from_iso(row["last_validated_at"]),
            last_reminded_at=from_iso(row["last_reminded_at"]),
        )

    @staticmethod
    def _row_to_disclosure(row: sqlite3.Row, grants: list[Grant]) -> Disclosure:
        return Disclosure(
            id=row["id"],
            slack_user_id=row["slack_user_id"],
            created_at=from_iso(row["created_at"]),  # type: ignore[arg-type]
            base_amount=row["base_amount"],
            base_currency=row["base_currency"],
            super_type=row["super_type"],
            super_pct=row["super_pct"],
            bonus_type=row["bonus_type"],
            bonus_value=row["bonus_value"],
            bonus_currency=row["bonus_currency"],
            bonus_note=row["bonus_note"],
            grants=grants,
            other_text=row["other_text"],
            base_aud=row["base_aud"],
            bonus_aud=row["bonus_aud"],
            fx_rate_date=row["fx_rate_date"],
        )

    @staticmethod
    def _row_to_grant(row: sqlite3.Row) -> Grant:
        return Grant(
            id=row["id"],
            disclosure_id=row["disclosure_id"],
            rsu_type=row["rsu_type"],
            equity_kind=row["equity_kind"],
            rsu_ticker=row["rsu_ticker"],
            rsu_shares_per_year=row["rsu_shares_per_year"],
            rsu_share_price=row["rsu_share_price"],
            rsu_share_currency=row["rsu_share_currency"],
            rsu_strike_price=row["rsu_strike_price"],
            rsu_amount=row["rsu_amount"],
            rsu_currency=row["rsu_currency"],
            rsu_note=row["rsu_note"],
            rsu_aud=row["rsu_aud"],
        )
