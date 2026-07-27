import sqlite3
from datetime import timedelta

import pytest

from app.db import Database, to_iso, utcnow


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(str(tmp_path / "db.db"))


DISCLOSURE = {
    "base_amount": 100000,
    "base_currency": "AUD",
    "super_type": "on_top_legislated",
    "bonus_type": "none",
    "grants": [],
    "base_aud": 100000,
}


def test_upsert_does_not_clobber_status(db: Database):
    db.upsert_member("U1", "alice")
    db.set_member_status("U1", "ejected")

    db.upsert_member("U1", "alice-renamed")
    member = db.get_member("U1")
    assert member.status == "ejected"
    assert member.display_name == "alice-renamed"


def test_add_disclosure_does_not_reactivate_ejected(db: Database):
    db.upsert_member("U1", "alice")
    db.set_member_status("U1", "ejected")

    db.add_disclosure("U1", DISCLOSURE)
    member = db.get_member("U1")
    assert member.status == "ejected"
    assert member.last_validated_at is not None
    # Ejected members must not appear in the pinned summary source.
    assert db.list_latest_disclosures_for_active() == []


def test_mark_rejoined_resets_grace_window(db: Database):
    old = utcnow() - timedelta(days=400)
    db.upsert_member("U1", "alice", joined_at=old)
    db.set_member_status("U1", "left")
    db.mark_reminded("U1")

    db.mark_rejoined("U1")
    member = db.get_member("U1")
    assert member.status == "active"
    assert member.joined_at > old + timedelta(days=300)
    assert member.last_reminded_at is None


def test_disclosure_resets_validation_clock(db: Database):
    db.upsert_member("U1", "alice")
    db.mark_reminded("U1")
    db.add_disclosure("U1", DISCLOSURE)

    member = db.get_member("U1")
    assert member.last_validated_at is not None
    assert member.last_reminded_at is None


def test_add_disclosure_persists_multiple_concurrent_grants(db: Database):
    db.upsert_member("U1", "alice")
    data = dict(
        DISCLOSURE,
        grants=[
            {
                "rsu_type": "public",
                "rsu_ticker": "NASDAQ:TEAM",
                "rsu_shares_per_year": 500,
                "rsu_share_price": 100.0,
                "rsu_share_currency": "USD",
                "rsu_amount": None,
                "rsu_currency": None,
                "rsu_note": "new-hire grant",
                "rsu_aud": 75000.0,
            },
            {
                "rsu_type": "public",
                "rsu_ticker": "NASDAQ:TEAM",
                "rsu_shares_per_year": 200,
                "rsu_share_price": 100.0,
                "rsu_share_currency": "USD",
                "rsu_amount": None,
                "rsu_currency": None,
                "rsu_note": "2024 top-up",
                "rsu_aud": 30000.0,
            },
        ],
    )

    disclosure = db.add_disclosure("U1", data)
    assert len(disclosure.grants) == 2
    assert disclosure.grants[0].rsu_shares_per_year == 500
    assert disclosure.grants[0].rsu_note == "new-hire grant"
    assert disclosure.grants[1].rsu_shares_per_year == 200
    assert disclosure.grants[1].rsu_note == "2024 top-up"

    latest = db.get_latest_disclosure("U1")
    assert latest is not None
    assert [g.rsu_note for g in latest.grants] == ["new-hire grant", "2024 top-up"]


def test_migrates_legacy_single_grant_disclosures(tmp_path):
    """Pre-multi-grant DBs kept one rsu_* set of columns directly on
    disclosures. On upgrade that data should land in the new grants table
    (slot 1) rather than disappearing from the board."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE members (
            slack_user_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            joined_at TEXT NOT NULL,
            last_validated_at TEXT,
            last_reminded_at TEXT
        );
        CREATE TABLE disclosures (
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
            rsu_type TEXT NOT NULL,
            rsu_ticker TEXT,
            rsu_shares_per_year REAL,
            rsu_share_price REAL,
            rsu_share_currency TEXT,
            rsu_amount REAL,
            rsu_currency TEXT,
            rsu_note TEXT,
            other_text TEXT,
            base_aud REAL NOT NULL,
            bonus_aud REAL,
            rsu_aud REAL,
            fx_rate_date TEXT
        );
        """
    )
    now = to_iso(utcnow())
    conn.execute(
        "INSERT INTO members (slack_user_id, display_name, joined_at) VALUES (?, ?, ?)",
        ("U1", "alice", now),
    )
    conn.execute(
        """
        INSERT INTO disclosures (
            slack_user_id, created_at, base_amount, base_currency,
            super_type, bonus_type, rsu_type, rsu_ticker, rsu_shares_per_year,
            rsu_share_price, rsu_share_currency, rsu_amount, rsu_currency,
            rsu_note, base_aud, rsu_aud
        ) VALUES ('U1', ?, 100000, 'AUD', 'on_top_legislated', 'none',
                  'public', 'NASDAQ:TEAM', 500, 100.0, 'USD', NULL, NULL,
                  NULL, 100000, 75000)
        """,
        (now,),
    )
    conn.commit()
    conn.close()

    db = Database(path)
    latest = db.get_latest_disclosure("U1")
    assert latest is not None
    assert len(latest.grants) == 1
    assert latest.grants[0].rsu_ticker == "NASDAQ:TEAM"
    assert latest.grants[0].rsu_shares_per_year == 500
    assert latest.grants[0].rsu_aud == 75000
    # Pre-options grants are backfilled as plain RSUs with no strike price.
    assert latest.grants[0].equity_kind == "rsu"
    assert latest.grants[0].rsu_strike_price is None


def test_add_disclosure_persists_options_grant(db: Database):
    db.upsert_member("U1", "alice")
    data = dict(
        DISCLOSURE,
        grants=[
            {
                "rsu_type": "public",
                "equity_kind": "options",
                "rsu_ticker": "NASDAQ:TEAM",
                "rsu_shares_per_year": 1000,
                "rsu_share_price": 120.0,
                "rsu_share_currency": "USD",
                "rsu_strike_price": 45.0,
                "rsu_amount": None,
                "rsu_currency": None,
                "rsu_note": "options grant",
                "rsu_aud": 112500.0,
            },
        ],
    )

    disclosure = db.add_disclosure("U1", data)
    assert len(disclosure.grants) == 1
    grant = disclosure.grants[0]
    assert grant.equity_kind == "options"
    assert grant.rsu_strike_price == 45.0
    assert grant.rsu_aud == 112500.0

    latest = db.get_latest_disclosure("U1")
    assert latest.grants[0].equity_kind == "options"
    assert latest.grants[0].rsu_strike_price == 45.0


def test_add_disclosure_persists_fy_period_and_grant_year_start(db: Database):
    db.upsert_member("U1", "alice")
    data = dict(
        DISCLOSURE,
        fy_period="1 Jul 2026 – 30 Jun 2027",
        grants=[
            {
                "rsu_type": "private",
                "rsu_amount": 20000,
                "rsu_currency": "AUD",
                "rsu_aud": 20000.0,
                "grant_year_start": 2022,
            },
        ],
    )

    disclosure = db.add_disclosure("U1", data)
    assert disclosure.fy_period == "1 Jul 2026 – 30 Jun 2027"
    assert disclosure.grants[0].grant_year_start == 2022

    latest = db.get_latest_disclosure("U1")
    assert latest.fy_period == "1 Jul 2026 – 30 Jun 2027"
    assert latest.grants[0].grant_year_start == 2022


def test_migrates_grants_table_missing_options_columns(tmp_path):
    """A DB that already has the multi-grant `grants` table (from before the
    options feature existed) should get equity_kind/rsu_strike_price added
    additively, with existing rows defaulting to plain 'rsu'."""
    path = str(tmp_path / "pre_options.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE members (
            slack_user_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            joined_at TEXT NOT NULL,
            last_validated_at TEXT,
            last_reminded_at TEXT
        );
        CREATE TABLE disclosures (
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
            fx_rate_date TEXT
        );
        CREATE TABLE grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disclosure_id INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            rsu_type TEXT NOT NULL,
            rsu_ticker TEXT,
            rsu_shares_per_year REAL,
            rsu_share_price REAL,
            rsu_share_currency TEXT,
            rsu_amount REAL,
            rsu_currency TEXT,
            rsu_note TEXT,
            rsu_aud REAL
        );
        """
    )
    now = to_iso(utcnow())
    conn.execute(
        "INSERT INTO members (slack_user_id, display_name, joined_at) VALUES (?, ?, ?)",
        ("U1", "alice", now),
    )
    conn.execute(
        """
        INSERT INTO disclosures (
            slack_user_id, created_at, base_amount, base_currency,
            super_type, bonus_type, base_aud
        ) VALUES ('U1', ?, 100000, 'AUD', 'on_top_legislated', 'none', 100000)
        """,
        (now,),
    )
    disclosure_id = conn.execute("SELECT id FROM disclosures").fetchone()[0]
    conn.execute(
        """
        INSERT INTO grants (
            disclosure_id, slot, rsu_type, rsu_ticker, rsu_shares_per_year,
            rsu_share_price, rsu_share_currency, rsu_aud
        ) VALUES (?, 1, 'public', 'NASDAQ:TEAM', 500, 100.0, 'USD', 75000)
        """,
        (disclosure_id,),
    )
    conn.commit()
    conn.close()

    db = Database(path)
    latest = db.get_latest_disclosure("U1")
    assert latest is not None
    assert latest.grants[0].equity_kind == "rsu"
    assert latest.grants[0].rsu_strike_price is None
    assert latest.grants[0].rsu_aud == 75000
    # Same additive migration pass also backfills the FY/grant-year fields.
    assert latest.fy_period is None
    assert latest.grants[0].grant_year_start is None
