import pytest

from app.db import Database
from app.fx import FxClient
from app.revalue import revalue_active_disclosures
from app.stocks import StockClient

DISCLOSURE = {
    "fy_period": "1 Jul 2026 – 30 Jun 2027",
    "base_amount": 250000,
    "base_currency": "AUD",
    "super_type": "on_top_legislated",
    "bonus_type": "none",
    "grants": [
        {
            "rsu_type": "private",
            "equity_kind": "rsu",
            "rsu_amount": 120000,
            "rsu_currency": "AUD",
            "rsu_aud": 120000,
        }
    ],
    "base_aud": 250000,
}


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(str(tmp_path / "db.db"))


@pytest.fixture
def fx(db: Database) -> FxClient:
    return FxClient(db)


@pytest.fixture
def stocks(db: Database) -> StockClient:
    return StockClient(db)


def _corrupt_valuation(db: Database, disclosure_id: int, grant_id: int) -> None:
    """Simulate a disclosure whose stored AUD figures are stale relative to
    what the (unchanged) underlying inputs would price to today."""
    with db.connect() as conn:
        conn.execute(
            "UPDATE disclosures SET base_aud = 1 WHERE id = ?", (disclosure_id,)
        )
        conn.execute("UPDATE grants SET rsu_aud = 1 WHERE id = ?", (grant_id,))


def test_revalue_corrects_stale_aud_figures(db: Database, fx: FxClient, stocks: StockClient):
    db.upsert_member("U1", "alice")
    disclosure = db.add_disclosure("U1", DISCLOSURE)
    _corrupt_valuation(db, disclosure.id, disclosure.grants[0].id)

    revalue_active_disclosures(db, fx, stocks)

    refreshed = db.get_latest_disclosure("U1")
    assert refreshed.base_aud == 250000
    assert refreshed.grants[0].rsu_aud == 120000


def test_revalue_does_not_touch_validation_or_fy_period(
    db: Database, fx: FxClient, stocks: StockClient
):
    db.upsert_member("U1", "alice")
    disclosure = db.add_disclosure("U1", DISCLOSURE)
    member_before = db.get_member("U1")

    revalue_active_disclosures(db, fx, stocks)

    refreshed = db.get_latest_disclosure("U1")
    member_after = db.get_member("U1")
    assert refreshed.created_at == disclosure.created_at
    assert refreshed.fy_period == disclosure.fy_period
    assert member_after.last_validated_at == member_before.last_validated_at


def test_revalue_skips_inactive_members(db: Database, fx: FxClient, stocks: StockClient):
    db.upsert_member("U1", "alice")
    disclosure = db.add_disclosure("U1", DISCLOSURE)
    db.set_member_status("U1", "left")
    _corrupt_valuation(db, disclosure.id, disclosure.grants[0].id)

    revalue_active_disclosures(db, fx, stocks)

    stale = db.get_latest_disclosure("U1")
    assert stale.base_aud == 1
