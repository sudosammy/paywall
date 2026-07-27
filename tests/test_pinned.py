import time
from unittest.mock import MagicMock, patch

import pytest

from app.config import Config
from app.db import Database
from app.pinned import PINNED_TS_KEY, rebuild_pinned_message
from app.scheduler import run_compliance_job


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


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(str(tmp_path / "db.db"))


def _client(*, update_ok: bool = True) -> MagicMock:
    client = MagicMock()
    if update_ok:
        client.chat_update.return_value = {"ok": True}
    else:
        client.chat_update.side_effect = Exception("message_not_found")
    client.chat_postMessage.return_value = {"ts": "9999999999.000100"}
    client.pins_list.return_value = {"items": []}
    return client


def test_fresh_board_updates_in_place(db: Database):
    config = _config()
    fresh_ts = f"{time.time():.6f}"
    db.set_setting(PINNED_TS_KEY, fresh_ts)
    client = _client()

    result = rebuild_pinned_message(client, db, config)

    assert result == fresh_ts
    client.chat_update.assert_called_once()
    client.chat_postMessage.assert_not_called()
    client.pins_remove.assert_not_called()
    client.chat_delete.assert_not_called()
    assert db.get_setting(PINNED_TS_KEY) == fresh_ts


def test_stale_board_is_reposted_and_old_cleaned_up(db: Database):
    config = _config(board_repost_days=80)
    stale_ts = f"{time.time() - 81 * 86_400:.6f}"
    db.set_setting(PINNED_TS_KEY, stale_ts)
    client = _client()

    result = rebuild_pinned_message(client, db, config)

    assert result == "9999999999.000100"
    client.chat_update.assert_not_called()
    client.chat_postMessage.assert_called_once()
    client.pins_add.assert_called_once_with(
        channel="C1", timestamp="9999999999.000100"
    )
    client.pins_remove.assert_called_once_with(channel="C1", timestamp=stale_ts)
    client.chat_delete.assert_called_once_with(channel="C1", ts=stale_ts)
    assert db.get_setting(PINNED_TS_KEY) == "9999999999.000100"


def test_failed_update_falls_back_to_repost(db: Database):
    config = _config()
    fresh_ts = f"{time.time():.6f}"
    db.set_setting(PINNED_TS_KEY, fresh_ts)
    client = _client(update_ok=False)

    result = rebuild_pinned_message(client, db, config)

    assert result == "9999999999.000100"
    client.chat_update.assert_called_once()
    client.chat_postMessage.assert_called_once()
    client.pins_remove.assert_called_once_with(channel="C1", timestamp=fresh_ts)
    client.chat_delete.assert_called_once_with(channel="C1", ts=fresh_ts)
    assert db.get_setting(PINNED_TS_KEY) == "9999999999.000100"


def test_cleanup_failures_do_not_propagate(db: Database):
    config = _config(board_repost_days=80)
    stale_ts = f"{time.time() - 81 * 86_400:.6f}"
    db.set_setting(PINNED_TS_KEY, stale_ts)
    client = _client()
    client.pins_remove.side_effect = Exception("not_pinned")
    client.chat_delete.side_effect = Exception("message_not_found")

    result = rebuild_pinned_message(client, db, config)

    assert result == "9999999999.000100"
    assert db.get_setting(PINNED_TS_KEY) == "9999999999.000100"


def test_scheduler_rebuilds_board_when_membership_unchanged(db: Database):
    config = _config(db_path=db.path)
    client = MagicMock()

    with (
        patch("app.scheduler.sync_channel_members", return_value=False) as sync,
        patch("app.scheduler.rebuild_pinned_message") as rebuild,
    ):
        run_compliance_job(client, db, config)

    sync.assert_called_once()
    rebuild.assert_called_once_with(client, db, config)
