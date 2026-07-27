from datetime import datetime, timedelta, timezone

from app.compliance import (
    eject_deadline,
    is_overdue,
    should_eject,
    should_nag,
)
from app.config import Config
from app.db import Member


def _config(**kwargs) -> Config:
    defaults = dict(
        slack_bot_token="x",
        slack_app_token="x",
        salary_channel_id="C1",
        admin_user_id="UADMIN",
        db_path=":memory:",
        revalidate_days=180,
        grace_days=14,
        nag_interval_days=3,
        au_super_pct=12.0,
        board_repost_days=80,
    )
    defaults.update(kwargs)
    return Config(**defaults)


def _member(**kwargs) -> Member:
    now = datetime.now(timezone.utc)
    defaults = dict(
        slack_user_id="U1",
        display_name="tester",
        status="active",
        joined_at=now - timedelta(days=30),
        last_validated_at=now - timedelta(days=30),
        last_reminded_at=None,
    )
    defaults.update(kwargs)
    return Member(**defaults)


def test_fresh_member_not_overdue():
    config = _config()
    member = _member(last_validated_at=datetime.now(timezone.utc) - timedelta(days=10))
    assert not is_overdue(member, config)
    assert not should_nag(member, config)
    assert not should_eject(member, config)


def test_stale_member_should_nag():
    config = _config()
    member = _member(
        last_validated_at=datetime.now(timezone.utc) - timedelta(days=181),
        last_reminded_at=None,
    )
    assert is_overdue(member, config)
    assert should_nag(member, config)
    assert not should_eject(member, config)


def test_nag_suppressed_within_interval():
    config = _config()
    member = _member(
        last_validated_at=datetime.now(timezone.utc) - timedelta(days=181),
        last_reminded_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert is_overdue(member, config)
    assert not should_nag(member, config)


def test_eject_after_grace():
    config = _config()
    # Due 20 days ago → past 14-day grace
    member = _member(
        last_validated_at=datetime.now(timezone.utc) - timedelta(days=200),
        last_reminded_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert should_eject(member, config)
    assert not should_nag(member, config)


def test_new_joiner_nagged_during_grace():
    config = _config()
    now = datetime.now(timezone.utc)
    member = _member(
        joined_at=now - timedelta(days=5),
        last_validated_at=None,
        last_reminded_at=None,
    )
    assert not is_overdue(member, config)
    assert should_nag(member, config)
    assert not should_eject(member, config)


def test_rejoiner_with_stale_disclosure_gets_fresh_grace():
    """A member who rejoins with an old disclosure must not be instantly
    ejected; they get grace_days from their (re)join date."""
    config = _config()
    now = datetime.now(timezone.utc)
    rejoiner = _member(
        joined_at=now - timedelta(days=2),  # rejoined 2 days ago
        last_validated_at=now - timedelta(days=300),  # very stale
        last_reminded_at=None,
    )
    assert is_overdue(rejoiner, config)
    assert should_nag(rejoiner, config)
    assert not should_eject(rejoiner, config)

    # 15 days after rejoining, still stale → eject
    stale_rejoiner = _member(
        joined_at=now - timedelta(days=15),
        last_validated_at=now - timedelta(days=300),
        last_reminded_at=now - timedelta(days=1),
    )
    assert should_eject(stale_rejoiner, config)


def test_new_joiner_ejected_at_grace_deadline():
    config = _config()
    now = datetime.now(timezone.utc)

    # Day 13: still inside join window
    almost = _member(
        joined_at=now - timedelta(days=13),
        last_validated_at=None,
        last_reminded_at=now - timedelta(days=1),
    )
    assert not should_eject(almost, config)
    assert eject_deadline(almost, config) == almost.joined_at + timedelta(days=14)

    # Day 15: past join deadline → eject (no extra grace for never-disclosed)
    late = _member(
        joined_at=now - timedelta(days=15),
        last_validated_at=None,
        last_reminded_at=now - timedelta(days=1),
    )
    assert is_overdue(late, config)
    assert should_eject(late, config)
    assert not should_nag(late, config)
