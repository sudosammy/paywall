from datetime import datetime, timedelta, timezone

from app.copy import join_reminder, revalidate_reminder


def test_join_reminder_counts_down_individually():
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)

    # Regression: this used to always print the static grace_days config
    # value, so everyone saw "14 days" forever regardless of how close their
    # actual per-member deadline was.
    far = join_reminder(now + timedelta(days=14), now=now)
    close = join_reminder(now + timedelta(days=2), now=now)

    assert "14 days" in far
    assert "2 days" in close
    assert far != close


def test_join_reminder_past_deadline_does_not_show_negative_days():
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    text = join_reminder(now - timedelta(days=3), now=now)
    assert "days left" not in text
    assert "passed" in text


def test_join_reminder_singular_day():
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    text = join_reminder(now + timedelta(hours=1), now=now)
    assert "1 day " in text or text.count("1 day") == 1
    assert "1 days" not in text


def test_revalidate_reminder_counts_down_to_eject_deadline():
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    due = now - timedelta(days=5)

    far = revalidate_reminder(due, now + timedelta(days=14), now=now)
    close = revalidate_reminder(due, now + timedelta(days=1), now=now)

    assert "14 days" in far
    assert "1 day " in close or close.count("1 day") == 1
    assert far != close


def test_revalidate_reminder_past_grace_deadline():
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    due = now - timedelta(days=20)
    text = revalidate_reminder(due, now - timedelta(days=1), now=now)
    assert "days left" not in text
    assert "grace deadline" in text
