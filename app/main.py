"""Bolt Socket Mode entrypoint."""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from app.config import load_config
from app.db import Database
from app.fx import FxClient
from app.handlers import register_handlers
from app.pinned import rebuild_pinned_message
from app.scheduler import run_compliance_job, start_scheduler
from app.stocks import StockClient
from app.sync import sync_channel_members

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> App:
    config = load_config()
    db = Database(config.db_path)
    fx = FxClient(db)
    stocks = StockClient(db)

    app = App(token=config.slack_bot_token)

    @app.middleware
    def inject_deps(context, next):
        context["config"] = config
        context["db"] = db
        context["fx"] = fx
        context["stocks"] = stocks
        next()

    register_handlers(app)

    app._salary_config = config  # type: ignore[attr-defined]
    app._salary_db = db  # type: ignore[attr-defined]
    return app


def main() -> None:
    app = create_app()
    config = app._salary_config  # type: ignore[attr-defined]
    db = app._salary_db  # type: ignore[attr-defined]

    handler = SocketModeHandler(app, config.slack_app_token)
    scheduler = start_scheduler(app.client, db, config)

    def shutdown(*_args):
        logger.info("Shutting down")
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    def after_connect():
        # Give the socket a moment to fully establish before Web API calls.
        time.sleep(2)
        try:
            # Register any pre-existing channel members (first deploy) and
            # reconcile membership drift.
            sync_channel_members(app.client, db, config)
        except Exception:
            logger.exception("Startup membership sync failed")
        try:
            rebuild_pinned_message(app.client, db, config)
            logger.info("Pinned summary ready")
        except Exception:
            logger.exception("Failed to initialise pinned summary")
        try:
            run_compliance_job(app.client, db, config, sync=False)
        except Exception:
            logger.exception("Startup compliance job failed")

    threading.Thread(target=after_connect, daemon=True).start()

    logger.info("Starting Paywall (Socket Mode)")
    handler.start()


if __name__ == "__main__":
    main()
