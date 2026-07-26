"""Channel membership sync and lookup helpers.

Keeps the members table aligned with actual channel membership so the bot
works for channels that existed before it was installed, and self-heals if
join/leave events are missed.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Config
from app.db import Database

logger = logging.getLogger(__name__)


def fetch_channel_member_ids(client: Any, channel_id: str) -> set[str]:
    ids: set[str] = set()
    cursor: str | None = None
    while True:
        resp = client.conversations_members(
            channel=channel_id, cursor=cursor, limit=200
        )
        ids.update(resp.get("members", []))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return ids


def is_channel_member(client: Any, channel_id: str, user_id: str) -> bool:
    return user_id in fetch_channel_member_ids(client, channel_id)


def resolve_user(client: Any, user_id: str) -> tuple[str, bool]:
    """Return (display_name, is_bot) for a Slack user."""
    try:
        info = client.users_info(user=user_id)
        user = info.get("user", {})
        profile = user.get("profile", {})
        name = (
            profile.get("display_name")
            or profile.get("real_name")
            or user.get("name")
            or user_id
        )
        return name, bool(user.get("is_bot")) or user_id == "USLACKBOT"
    except Exception:
        logger.exception("Failed to resolve user %s", user_id)
        return user_id, False


def sync_channel_members(client: Any, db: Database, config: Config) -> bool:
    """Reconcile the members table with actual channel membership.

    Returns True if anything changed.
    """
    try:
        channel_ids = fetch_channel_member_ids(client, config.salary_channel_id)
    except Exception:
        logger.exception("Failed to list channel members; skipping sync")
        return False

    bot_user_id = None
    try:
        bot_user_id = client.auth_test().get("user_id")
    except Exception:
        logger.exception("auth_test failed during sync")

    known = {m.slack_user_id: m for m in db.list_members()}
    changed = False

    for user_id in channel_ids:
        if user_id == bot_user_id:
            continue
        member = known.get(user_id)
        if member is None:
            display_name, is_bot = resolve_user(client, user_id)
            if is_bot:
                continue
            db.upsert_member(user_id, display_name)
            logger.info("Registered channel member %s (%s)", display_name, user_id)
            changed = True
        elif member.status != "active":
            db.mark_rejoined(user_id)
            logger.info("Reactivated member %s", user_id)
            changed = True

    for user_id, member in known.items():
        if member.status == "active" and user_id not in channel_ids:
            db.set_member_status(user_id, "left")
            logger.info("Marked %s as left (no longer in channel)", user_id)
            changed = True

    return changed
