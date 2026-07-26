"""Create and update the bot-owned pinned summary message."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import Config
from app.db import Database
from app.formatting import build_pinned_message

logger = logging.getLogger(__name__)

PINNED_TS_KEY = "pinned_message_ts"
SECONDS_PER_DAY = 86_400


def rebuild_pinned_message(
    client: Any,
    db: Database,
    config: Config,
) -> str | None:
    """Rebuild the pinned summary. Returns the message ts."""
    rows = db.list_latest_disclosures_for_active()
    text = build_pinned_message(rows, au_super_pct=config.au_super_pct)
    channel = config.salary_channel_id
    existing_ts = db.get_setting(PINNED_TS_KEY)

    if existing_ts and not _is_stale(existing_ts, config.board_repost_days):
        try:
            client.chat_update(channel=channel, ts=existing_ts, text=text)
            _ensure_pinned(client, channel, existing_ts)
            return existing_ts
        except Exception:
            logger.exception("Failed to update pinned message; posting a new one")

    if existing_ts and _is_stale(existing_ts, config.board_repost_days):
        logger.info(
            "Pinned board %s is older than %s days; reposting for free-plan retention",
            existing_ts,
            config.board_repost_days,
        )

    resp = client.chat_postMessage(channel=channel, text=text)
    ts = resp["ts"]
    db.set_setting(PINNED_TS_KEY, ts)
    _ensure_pinned(client, channel, ts)

    if existing_ts and existing_ts != ts:
        _cleanup_old_board(client, channel, existing_ts)

    return ts


def _is_stale(ts: str, board_repost_days: int) -> bool:
    try:
        posted_at = float(ts)
    except (TypeError, ValueError):
        return True
    age_seconds = time.time() - posted_at
    return age_seconds >= board_repost_days * SECONDS_PER_DAY


def _cleanup_old_board(client: Any, channel: str, ts: str) -> None:
    try:
        client.pins_remove(channel=channel, timestamp=ts)
    except Exception:
        logger.exception("Failed to unpin old board message %s", ts)
    try:
        client.chat_delete(channel=channel, ts=ts)
    except Exception:
        logger.exception("Failed to delete old board message %s", ts)


def _ensure_pinned(client: Any, channel: str, ts: str) -> None:
    try:
        pins = client.pins_list(channel=channel)
        already = any(
            item.get("message", {}).get("ts") == ts for item in pins.get("items", [])
        )
        if not already:
            client.pins_add(channel=channel, timestamp=ts)
    except Exception:
        logger.exception("Failed to pin message %s in %s", ts, channel)
