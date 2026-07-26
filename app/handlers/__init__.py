"""Slack event, action, and command handlers."""

from app.handlers import actions, commands, events


def register_handlers(app) -> None:
    events.register(app)
    actions.register(app)
    commands.register(app)
