from datetime import datetime, timezone

from app.db import Disclosure, Member
from app.formatting import (
    build_pinned_message,
    format_disclosure_line,
    format_money,
    format_money_with_aud,
    format_rsu,
)


def test_format_money_aud_thousands():
    assert format_money(250000, "AUD") == "$250k"
    assert format_money(232900, "AUD") == "$232.9k"
    assert format_money(500, "AUD") == "$500"


def test_format_money_millions():
    assert format_money(1500000, "AUD") == "$1.5m"
    assert format_money(1960000, "AUD") == "$1.96m"
    assert format_money(12000000, "JPY") == "JPY 12m"


def test_format_money_foreign_with_aud():
    assert format_money(289000, "USD") == "USD 289k"
    assert (
        format_money_with_aud(289000, "USD", 437000)
        == "USD 289k (~A$437k)"
    )
    assert format_money_with_aud(250000, "AUD", 250000) == "$250k"


def _member(**kwargs) -> Member:
    defaults = dict(
        slack_user_id="U1",
        display_name="sudosammy",
        status="active",
        joined_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_validated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        last_reminded_at=None,
    )
    defaults.update(kwargs)
    return Member(**defaults)


def _disclosure(**kwargs) -> Disclosure:
    defaults = dict(
        id=1,
        slack_user_id="U1",
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        base_amount=250000,
        base_currency="AUD",
        super_type="on_top_legislated",
        super_pct=None,
        bonus_type="pct_of_base",
        bonus_value=20,
        bonus_currency="AUD",
        bonus_note=None,
        rsu_type="private",
        rsu_ticker=None,
        rsu_shares_per_year=None,
        rsu_share_price=None,
        rsu_share_currency=None,
        rsu_amount=120000,
        rsu_currency="AUD",
        rsu_note=None,
        other_text=None,
        base_aud=250000,
        bonus_aud=50000,
        rsu_aud=120000,
        fx_rate_date="2026-07-01",
    )
    defaults.update(kwargs)
    return Disclosure(**defaults)


def test_format_disclosure_line():
    line = format_disclosure_line(_member(), _disclosure(), au_super_pct=12.0)
    assert "*sudosammy*:" in line
    assert "$250k base" in line
    assert "12% super" in line
    assert "20% bonus" in line
    assert "$120k/yr equity (private)" in line
    assert "updated 2026-07-01" in line


def test_format_public_rsu():
    d = _disclosure(
        rsu_type="public",
        rsu_ticker="NASDAQ:TEAM",
        rsu_shares_per_year=500,
        rsu_share_price=100.0,
        rsu_share_currency="USD",
        rsu_amount=None,
        rsu_currency=None,
        rsu_aud=65000,
    )
    assert format_rsu(d) == "500 NASDAQ:TEAM sh/yr (~A$65k/yr)"


def test_format_usd_base_with_public_rsu():
    d = _disclosure(
        base_amount=289000,
        base_currency="USD",
        base_aud=437000,
        bonus_type="none",
        bonus_value=None,
        rsu_type="public",
        rsu_ticker="NASDAQ:TEAM",
        rsu_shares_per_year=500,
        rsu_share_price=86.88,
        rsu_share_currency="USD",
        rsu_amount=None,
        rsu_currency=None,
        rsu_aud=65000,
    )
    line = format_disclosure_line(_member(display_name="stitch"), d, au_super_pct=12.0)
    assert "USD 289k (~A$437k) base" in line
    assert "500 NASDAQ:TEAM sh/yr (~A$65k/yr)" in line


def test_build_pinned_message_footer():
    msg = build_pinned_message(
        [(_member(), _disclosure())],
        au_super_pct=12.0,
        generated_at=datetime(2026, 7, 26, 4, 0, tzinfo=timezone.utc),
    )
    assert msg.startswith("*The board*")
    assert "Last rebuilt 2026-07-26 04:00 UTC" in msg


def test_build_pinned_message_orders_by_base_aud_desc():
    low = (
        _member(slack_user_id="U1", display_name="alice"),
        _disclosure(id=1, slack_user_id="U1", base_aud=100000, base_amount=100000),
    )
    high = (
        _member(slack_user_id="U2", display_name="bob"),
        _disclosure(
            id=2,
            slack_user_id="U2",
            base_amount=289000,
            base_currency="USD",
            base_aud=437000,
        ),
    )
    mid = (
        _member(slack_user_id="U3", display_name="carol"),
        _disclosure(id=3, slack_user_id="U3", base_aud=250000, base_amount=250000),
    )
    msg = build_pinned_message(
        [low, high, mid],
        au_super_pct=12.0,
        generated_at=datetime(2026, 7, 26, 4, 0, tzinfo=timezone.utc),
    )
    bob = msg.index("*bob*:")
    carol = msg.index("*carol*:")
    alice = msg.index("*alice*:")
    assert bob < carol < alice
