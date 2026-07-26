"""Slash command handlers."""

from __future__ import annotations

import logging

from slack_bolt import App

from app import copy
from app.compliance import is_overdue, overdue_since
from app.config import Config
from app.db import Database
from app.handlers.actions import NOT_A_MEMBER_TEXT, _ensure_member
from app.modal import build_disclosure_modal

logger = logging.getLogger(__name__)


def register(app: App) -> None:
    @app.command("/salary")
    def salary_command(ack, body, client, respond, context, command):
        ack()
        config: Config = context["config"]
        db: Database = context["db"]
        user_id = body["user_id"]
        text = (command.get("text") or "").strip().lower()

        member = _ensure_member(client, db, config, user_id)
        if member is None:
            respond(NOT_A_MEMBER_TEXT)
            return

        if text == "status":
            respond(copy.member_status(member, config))
            return

        if text == "overdue":
            if user_id != config.admin_user_id:
                respond(copy.ADMIN_ONLY_OVERDUE)
                return

            lines = []
            for m in db.list_active_members():
                if is_overdue(m, config):
                    since = overdue_since(m, config)
                    since_s = since.date().isoformat() if since else "?"
                    kind = "never spilled" if not m.last_validated_at else "stale"
                    lines.append(f"• {m.display_name} ({kind}, since {since_s})")

            respond(
                f"{copy.OVERDUE_HEADER}\n" + "\n".join(lines)
                if lines
                else copy.OVERDUE_NONE
            )
            return

        # Default: open disclosure modal
        latest = db.get_latest_disclosure(user_id)
        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view=build_disclosure_modal(latest),
            )
        except Exception:
            logger.exception("Failed to open disclosure modal for %s", user_id)
            respond(copy.MODAL_OPEN_FAILED)
