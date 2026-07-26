from datetime import timedelta

import pytest

from app.db import Database, utcnow


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(str(tmp_path / "db.db"))


DISCLOSURE = {
    "base_amount": 100000,
    "base_currency": "AUD",
    "super_type": "on_top_legislated",
    "bonus_type": "none",
    "rsu_type": "none",
    "rsu_ticker": None,
    "rsu_shares_per_year": None,
    "rsu_share_price": None,
    "rsu_share_currency": None,
    "rsu_amount": None,
    "rsu_currency": None,
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
