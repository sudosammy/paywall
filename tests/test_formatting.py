from datetime import datetime, timezone

from app.db import Disclosure, Grant, Member
from app.formatting import (
    build_pinned_message,
    format_disclosure_line,
    format_grant,
    format_grants,
    format_money,
    format_money_with_aud,
    total_comp_aud,
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


def _grant(**kwargs) -> Grant:
    defaults = dict(
        id=1,
        disclosure_id=1,
        rsu_type="private",
        rsu_ticker=None,
        rsu_shares_per_year=None,
        rsu_share_price=None,
        rsu_share_currency=None,
        rsu_amount=120000,
        rsu_currency="AUD",
        rsu_note=None,
        rsu_aud=120000,
    )
    defaults.update(kwargs)
    return Grant(**defaults)


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
        grants=[_grant()],
        other_text=None,
        base_aud=250000,
        bonus_aud=50000,
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
    # 250k base + 30k super (12%) + 50k bonus + 120k equity
    assert "~A$450k TC" in line
    assert "updated 2026-07-01" in line


def test_total_comp_aud_on_top_super():
    d = _disclosure()  # base 250k, 12% on-top super, 50k bonus, 120k equity
    assert total_comp_aud(d, au_super_pct=12.0) == 450000


def test_total_comp_aud_included_super_adds_nothing_extra():
    d = _disclosure(
        super_type="included",
        bonus_type="none",
        bonus_value=None,
        bonus_aud=None,
        grants=[],
    )
    assert total_comp_aud(d, au_super_pct=12.0) == 250000


def test_total_comp_aud_custom_super_pct():
    d = _disclosure(
        base_amount=200000,
        base_aud=200000,
        super_type="custom_pct",
        super_pct=15,
        bonus_type="none",
        bonus_value=None,
        bonus_aud=None,
        grants=[],
    )
    assert total_comp_aud(d, au_super_pct=12.0) == 230000


def test_total_comp_aud_multiple_grants_summed():
    d = _disclosure(
        super_type="none",
        bonus_type="none",
        bonus_value=None,
        bonus_aud=None,
        grants=[
            _grant(id=1, rsu_aud=65000),
            _grant(id=2, rsu_aud=26000),
        ],
    )
    assert total_comp_aud(d, au_super_pct=12.0) == 250000 + 65000 + 26000


def test_format_no_grants():
    d = _disclosure(grants=[])
    assert format_grants(d.grants) == "$0 stock"


def test_format_public_grant():
    g = _grant(
        rsu_type="public",
        rsu_ticker="NASDAQ:TEAM",
        rsu_shares_per_year=500,
        rsu_share_price=100.0,
        rsu_share_currency="USD",
        rsu_amount=None,
        rsu_currency=None,
        rsu_aud=65000,
    )
    assert format_grant(g) == "500 NASDAQ:TEAM sh/yr (~A$65k/yr)"


def test_format_multiple_concurrent_grants():
    grants = [
        _grant(
            id=1,
            rsu_type="public",
            rsu_ticker="NASDAQ:TEAM",
            rsu_shares_per_year=500,
            rsu_share_price=100.0,
            rsu_share_currency="USD",
            rsu_amount=None,
            rsu_currency=None,
            rsu_aud=65000,
            rsu_note="new-hire grant",
        ),
        _grant(
            id=2,
            rsu_type="public",
            rsu_ticker="NASDAQ:TEAM",
            rsu_shares_per_year=200,
            rsu_share_price=100.0,
            rsu_share_currency="USD",
            rsu_amount=None,
            rsu_currency=None,
            rsu_aud=26000,
            rsu_note="2024 top-up",
        ),
    ]
    text = format_grants(grants)
    assert text == (
        "500 NASDAQ:TEAM sh/yr (~A$65k/yr) (new-hire grant) + "
        "200 NASDAQ:TEAM sh/yr (~A$26k/yr) (2024 top-up)"
    )


def test_format_usd_base_with_public_rsu():
    d = _disclosure(
        base_amount=289000,
        base_currency="USD",
        base_aud=437000,
        bonus_type="none",
        bonus_value=None,
        grants=[
            _grant(
                rsu_type="public",
                rsu_ticker="NASDAQ:TEAM",
                rsu_shares_per_year=500,
                rsu_share_price=86.88,
                rsu_share_currency="USD",
                rsu_amount=None,
                rsu_currency=None,
                rsu_aud=65000,
            )
        ],
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


def test_build_pinned_message_channel_value():
    plain = dict(super_type="none", bonus_type="none", bonus_value=None, bonus_aud=None, grants=[])
    d1 = _disclosure(base_amount=200000, base_aud=200000, **plain)
    d2 = _disclosure(id=2, slack_user_id="U2", base_amount=300000, base_aud=300000, **plain)
    msg = build_pinned_message(
        [
            (_member(), d1),
            (_member(slack_user_id="U2", display_name="bob"), d2),
        ],
        au_super_pct=12.0,
        generated_at=datetime(2026, 7, 26, 4, 0, tzinfo=timezone.utc),
    )
    assert "Channel value: ~A$500k TC across 2 disclosures" in msg
    assert "hoard responsibly" in msg


def test_build_pinned_message_empty_has_no_channel_value():
    msg = build_pinned_message(
        [],
        au_super_pct=12.0,
        generated_at=datetime(2026, 7, 26, 4, 0, tzinfo=timezone.utc),
    )
    assert "Crickets" in msg
    assert "Channel value" not in msg


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
