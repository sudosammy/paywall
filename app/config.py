"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    slack_bot_token: str
    slack_app_token: str
    salary_channel_id: str
    admin_user_id: str
    db_path: str
    revalidate_days: int
    grace_days: int
    nag_interval_days: int
    au_super_pct: float
    board_repost_days: int


def load_config() -> Config:
    missing = [
        name
        for name in (
            "SLACK_BOT_TOKEN",
            "SLACK_APP_TOKEN",
            "SALARY_CHANNEL_ID",
            "ADMIN_USER_ID",
        )
        if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    return Config(
        slack_bot_token=os.environ["SLACK_BOT_TOKEN"],
        slack_app_token=os.environ["SLACK_APP_TOKEN"],
        salary_channel_id=os.environ["SALARY_CHANNEL_ID"],
        admin_user_id=os.environ["ADMIN_USER_ID"],
        db_path=os.getenv("DB_PATH", "/data/salary.db"),
        revalidate_days=int(os.getenv("REVALIDATE_DAYS", "180")),
        grace_days=int(os.getenv("GRACE_DAYS", "14")),
        nag_interval_days=int(os.getenv("NAG_INTERVAL_DAYS", "3")),
        au_super_pct=float(os.getenv("AU_SUPER_PCT", "12.0")),
        board_repost_days=int(os.getenv("BOARD_REPOST_DAYS", "80")),
    )
