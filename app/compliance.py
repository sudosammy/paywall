"""Staleness / overdue helpers used by the scheduler and commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Config
from app.db import Member
from app.formatting import due_date


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def never_disclosed(member: Member) -> bool:
    return member.last_validated_at is None


def initial_deadline(member: Member, config: Config) -> datetime:
    """New joiners must disclose by this time or be ejected."""
    return member.joined_at + timedelta(days=config.grace_days)


def disclosure_due_at(member: Member, config: Config) -> datetime | None:
    """When a disclosed member must next validate. None if never disclosed."""
    if never_disclosed(member):
        return None
    assert member.last_validated_at is not None
    return due_date(member.last_validated_at, config.revalidate_days)


def overdue_since(member: Member, config: Config) -> datetime | None:
    """Return when the member became overdue, or None if currently compliant.

    - Never disclosed: overdue from the initial join deadline.
    - Disclosed: overdue from the re-validation due date.
    """
    if never_disclosed(member):
        deadline = initial_deadline(member, config)
        return deadline if now_utc() >= deadline else None

    due = disclosure_due_at(member, config)
    assert due is not None
    return due if now_utc() >= due else None


def eject_deadline(member: Member, config: Config) -> datetime | None:
    """When the member should be ejected.

    - Never disclosed: eject at the initial join deadline (grace_days after join).
    - Stale disclosure: eject grace_days after the re-validation due date, but
      never earlier than grace_days after (re)joining — so someone who rejoins
      with an old disclosure gets a fresh window rather than instant ejection.
    """
    if never_disclosed(member):
        return initial_deadline(member, config)

    due = disclosure_due_at(member, config)
    assert due is not None
    return max(
        due + timedelta(days=config.grace_days),
        initial_deadline(member, config),
    )


def should_eject(member: Member, config: Config) -> bool:
    deadline = eject_deadline(member, config)
    if deadline is None:
        return False
    return now_utc() >= deadline


def should_nag(member: Member, config: Config) -> bool:
    """Whether to send a reminder DM.

    New joiners are nagged during their initial grace window.
    Stale members are nagged from the due date until ejection.
    """
    if should_eject(member, config):
        return False

    if never_disclosed(member):
        # Nag throughout the join grace window until they disclose.
        pass
    elif overdue_since(member, config) is None:
        return False

    if member.last_reminded_at is None:
        return True
    return now_utc() >= member.last_reminded_at + timedelta(
        days=config.nag_interval_days
    )


def is_overdue(member: Member, config: Config) -> bool:
    return overdue_since(member, config) is not None
