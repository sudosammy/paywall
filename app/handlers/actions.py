"""Slack interactive action handlers."""

from __future__ import annotations

import logging

from slack_bolt import App

from app import copy
from app.config import Config
from app.db import Database, Member
from app.fx import FxClient
from app.modal import CALLBACK_ID, apply_fx, build_disclosure_modal, parse_submission
from app.pinned import rebuild_pinned_message
from app.stocks import StockClient, TickerNotFoundError
from app.sync import is_channel_member, resolve_user

logger = logging.getLogger(__name__)

NOT_A_MEMBER_TEXT = copy.NOT_A_MEMBER

DISCLOSE_BUTTON_BLOCKS = [
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


def register(app: App) -> None:
    @app.action("open_disclosure_modal")
    def open_modal(ack, body, client, context):
        ack()
        config: Config = context["config"]
        db: Database = context["db"]
        user_id = body["user"]["id"]
        trigger_id = body["trigger_id"]

        member = _ensure_member(client, db, config, user_id)
        if member is None:
            client.chat_postMessage(channel=user_id, text=NOT_A_MEMBER_TEXT)
            return

        latest = db.get_latest_disclosure(user_id)
        try:
            client.views_open(
                trigger_id=trigger_id, view=build_disclosure_modal(latest)
            )
        except Exception:
            logger.exception("views_open failed for %s", user_id)

    @app.action("confirm_disclosure")
    def confirm_disclosure(ack, body, client, context):
        ack()
        config: Config = context["config"]
        db: Database = context["db"]
        user_id = body["user"]["id"]

        member = _ensure_member(client, db, config, user_id)
        if member is None:
            client.chat_postMessage(channel=user_id, text=NOT_A_MEMBER_TEXT)
            return

        if not member.last_validated_at:
            text = copy.NO_DISCLOSURE_TO_CONFIRM
            client.chat_postMessage(
                channel=user_id,
                text=text,
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": text},
                    },
                    *DISCLOSE_BUTTON_BLOCKS,
                ],
            )
            return

        db.mark_validated(user_id)
        try:
            rebuild_pinned_message(client, db, config)
        except Exception:
            logger.exception("Failed to rebuild pinned after confirm")

        client.chat_postMessage(channel=user_id, text=copy.RECONFIRM_THANKS)

    @app.view(CALLBACK_ID)
    def handle_submission(ack, body, client, context, view):
        config: Config = context["config"]
        db: Database = context["db"]
        fx: FxClient = context["fx"]
        stocks: StockClient = context["stocks"]
        user_id = body["user"]["id"]
        values = view["state"]["values"]

        # Fast validation before the 3-second ack deadline.
        data, errors = parse_submission(values)
        if errors:
            ack(response_action="errors", errors=errors)
            return
        ack()
        assert data is not None

        member = _ensure_member(client, db, config, user_id)
        if member is None:
            client.chat_postMessage(channel=user_id, text=NOT_A_MEMBER_TEXT)
            return

        # FX / stock price lookups (may hit external APIs) happen after the ack.
        try:
            full = apply_fx(data, fx, stocks)
        except TickerNotFoundError:
            logger.exception("Ticker lookup failed for %s", user_id)
            client.chat_postMessage(
                channel=user_id,
                text=copy.TICKER_FAILED,
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": copy.TICKER_FAILED},
                    },
                    *DISCLOSE_BUTTON_BLOCKS,
                ],
            )
            return
        except Exception:
            logger.exception("FX / price conversion failed for %s", user_id)
            client.chat_postMessage(
                channel=user_id,
                text=copy.FX_FAILED,
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": copy.FX_FAILED},
                    },
                    *DISCLOSE_BUTTON_BLOCKS,
                ],
            )
            return

        db.add_disclosure(user_id, full)

        try:
            rebuild_pinned_message(client, db, config)
        except Exception:
            logger.exception("Failed to rebuild pinned after disclosure")

        try:
            client.chat_postMessage(channel=user_id, text=copy.SUBMIT_THANKS)
        except Exception:
            logger.exception("Failed to DM confirmation to %s", user_id)


def _ensure_member(
    client, db: Database, config: Config, user_id: str
) -> Member | None:
    """Return the active member record for user_id, or None.

    Active members get a display-name refresh only. Unknown or inactive users
    are checked against actual channel membership before being (re)activated,
    so ejected/left users can't reappear in the summary via /salary alone.
    """
    member = db.get_member(user_id)
    if member and member.status == "active":
        return member

    if not is_channel_member(client, config.salary_channel_id, user_id):
        return None

    display_name, is_bot = resolve_user(client, user_id)
    if is_bot:
        return None
    if member:
        db.mark_rejoined(user_id)
    return db.upsert_member(user_id, display_name)
