"""Slack event handlers."""

from __future__ import annotations

import logging

from slack_bolt import App

from app import copy
from app.config import Config
from app.db import Database
from app.pinned import rebuild_pinned_message
from app.sync import resolve_user

logger = logging.getLogger(__name__)


def register(app: App) -> None:
    @app.event("member_joined_channel")
    def on_member_joined(event, client, context):
        config: Config = context["config"]
        db: Database = context["db"]

        if event.get("channel") != config.salary_channel_id:
            return

        user_id = event.get("user")
        if not user_id or user_id == context.get("bot_user_id"):
            return

        display_name, is_bot = resolve_user(client, user_id)
        if is_bot:
            return

        existing = db.get_member(user_id)
        db.upsert_member(user_id, display_name)
        if existing and existing.status != "active":
            # Returning member: fresh grace window, back on the pinned list.
            db.mark_rejoined(user_id)
            try:
                rebuild_pinned_message(client, db, config)
            except Exception:
                logger.exception("Failed to rebuild pinned after rejoin")

        text = copy.welcome_dm(config.grace_days, config.revalidate_days)
        try:
            client.chat_postMessage(
                channel=user_id,
                text=text,
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": text},
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": copy.BTN_DISCLOSE,
                                },
                                "style": "primary",
                                "action_id": "open_disclosure_modal",
                            }
                        ],
                    },
                ],
            )
            # The welcome DM counts as the first reminder so the daily job
            # doesn't nag again immediately.
            db.mark_reminded(user_id)
        except Exception:
            logger.exception("Failed to DM welcome to %s", user_id)

    @app.event("member_left_channel")
    def on_member_left(event, client, context):
        config: Config = context["config"]
        db: Database = context["db"]

        if event.get("channel") != config.salary_channel_id:
            return

        user_id = event.get("user")
        if not user_id:
            return

        member = db.get_member(user_id)
        if member and member.status == "active":
            db.set_member_status(user_id, "left")
            try:
                rebuild_pinned_message(client, db, config)
            except Exception:
                logger.exception("Failed to rebuild pinned message after leave")
