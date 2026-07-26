"""Daily compliance job: reminders every 3 days, eject after 14 days overdue."""

from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from slack_sdk.errors import SlackApiError

from app import copy
from app.compliance import should_eject, should_nag
from app.config import Config
from app.db import Database
from app.pinned import rebuild_pinned_message
from app.sync import sync_channel_members

logger = logging.getLogger(__name__)


def start_scheduler(
    client: Any,
    db: Database,
    config: Config,
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_compliance_job,
        trigger="cron",
        hour=1,
        minute=0,
        args=[client, db, config],
        id="compliance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Compliance scheduler started")
    return scheduler


def run_compliance_job(
    client: Any, db: Database, config: Config, *, sync: bool = True
) -> None:
    logger.info("Running compliance job")
    if sync:
        try:
            sync_channel_members(client, db, config)
        except Exception:
            logger.exception("Membership sync failed")

    try:
        rebuild_pinned_message(client, db, config)
    except Exception:
        logger.exception("Failed to rebuild pinned message")

    members = db.list_active_members()
    for member in members:
        try:
            if should_eject(member, config):
                _eject_member(client, db, config, member.slack_user_id, member.display_name)
            elif should_nag(member, config):
                _send_reminder(client, db, config, member.slack_user_id)
        except Exception:
            logger.exception("Compliance action failed for %s", member.slack_user_id)


def _send_reminder(client: Any, db: Database, config: Config, user_id: str) -> None:
    member = db.get_member(user_id)
    if not member:
        return

    if not member.last_validated_at:
        text = copy.join_reminder(config.grace_days)
        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": copy.BTN_DISCLOSE},
                        "style": "primary",
                        "action_id": "open_disclosure_modal",
                    }
                ],
            },
        ]
    else:
        text = copy.revalidate_reminder(config.revalidate_days, config.grace_days)
        blocks = [
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
                            "text": copy.BTN_STILL_ACCURATE,
                        },
                        "style": "primary",
                        "action_id": "confirm_disclosure",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": copy.BTN_UPDATE},
                        "action_id": "open_disclosure_modal",
                    },
                ],
            },
        ]

    client.chat_postMessage(channel=user_id, text=text, blocks=blocks)
    db.mark_reminded(user_id)
    logger.info("Sent reminder to %s", user_id)


def _eject_member(
    client: Any,
    db: Database,
    config: Config,
    user_id: str,
    display_name: str,
) -> None:
    kicked = False
    try:
        client.conversations_kick(channel=config.salary_channel_id, user=user_id)
        kicked = True
    except SlackApiError as e:
        error = e.response.get("error", "") if e.response else ""
        if error in ("not_in_channel", "user_not_found"):
            # Already gone; nothing to kick.
            logger.info("%s already left the channel", user_id)
            db.set_member_status(user_id, "left")
            try:
                rebuild_pinned_message(client, db, config)
            except Exception:
                logger.exception("Failed to rebuild pinned after stale-left member")
            return
        logger.exception("Failed to kick %s; notifying admin", user_id)
        _notify_admin_kick_failed(client, config, user_id, display_name)
    except Exception:
        logger.exception("Failed to kick %s; notifying admin", user_id)
        _notify_admin_kick_failed(client, config, user_id, display_name)

    db.set_member_status(user_id, "ejected")

    try:
        notice = copy.ejection_notice(user_id, kicked=kicked)
        client.chat_postMessage(channel=config.salary_channel_id, text=notice)
    except Exception:
        logger.exception("Failed to post ejection notice")

    try:
        rebuild_pinned_message(client, db, config)
    except Exception:
        logger.exception("Failed to rebuild pinned after ejection")

    try:
        client.chat_postMessage(channel=user_id, text=copy.EJECTED_DM)
    except Exception:
        logger.exception("Failed to DM ejected user %s", user_id)

    logger.info("Ejected %s (kicked=%s)", user_id, kicked)


def _notify_admin_kick_failed(
    client: Any, config: Config, user_id: str, display_name: str
) -> None:
    try:
        client.chat_postMessage(
            channel=config.admin_user_id,
            text=copy.admin_kick_failed(user_id, display_name),
        )
    except Exception:
        logger.exception("Failed to notify admin about kick failure")
