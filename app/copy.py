"""User-facing copy for Paywall."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Config
from app.db import Member

# Slack button labels (≤75 chars; keep short)
BTN_DISCLOSE = "Spill the beans"
BTN_STILL_ACCURATE = "Still that, yeah"
BTN_UPDATE = "Numbers moved"


def welcome_dm(grace_days: int, revalidate_days: int) -> str:
    return (
        f"Welcome behind the *Paywall*.\n\n"
        f"This channel runs on mutual nosiness: drop your comp within "
        f"*{grace_days} days* (button below, or `/salary`) or you're out. "
        f"Everyone re-ups every *{revalidate_days} days* stale numbers get you "
        f"booted too. Transparency or bust."
    )


NOT_A_MEMBER = (
    "Paywall only works if you're actually *in* the channel. "
    "Ask the admin for an invite, then come back and spill."
)


def join_reminder(grace_days: int) -> str:
    return (
        f"Hey. Still waiting on your numbers. You've got *{grace_days} days* "
        f"from joining to disclose, or Paywall shows you the door. "
        f"Don't make me the bad guy. (I will be the bad guy.)"
    )


def revalidate_reminder(revalidate_days: int, grace_days: int) -> str:
    return (
        f"Your disclosure is getting dusty we re-check every "
        f"*{revalidate_days} days*. Hit *Still that, yeah* if nothing's changed, "
        f"or update if you've leveled up / been shafted. "
        f"Ghost for *{grace_days} days* and you're gone."
    )


def ejection_notice(user_id: str, *, kicked: bool) -> str:
    if kicked:
        return (
            f"<@{user_id}> got Paywalled, didn't disclose/update in time. "
            f"Silence isn't a compensation strategy."
        )
    return (
        f"<@{user_id}> is overdue and should be out "
        f"(auto-kick whiffed; admin's been pinged)."
    )


EJECTED_DM = (
    "You've been kicked from the salary channel — disclosure went stale "
    "(or never showed up). Want back in? Ask the admin for a re-invite. "
    "You'll get a fresh window to spill again. No hard feelings. Mostly."
)


def admin_kick_failed(user_id: str, display_name: str) -> str:
    return (
        f"Couldn't auto-yeet <@{user_id}> ({display_name}) for non-disclosure. "
        f"Manual boot required — the Paywall doesn't enforce itself without legs."
    )


NO_DISCLOSURE_TO_CONFIRM = (
    "You've got nothing on file to confirm. Spill first — then we can high-five later."
)

RECONFIRM_THANKS = (
    "Locked in — you're good for another stretch. May your TC stay juicy."
)

SUBMIT_THANKS = (
    "Got it. Your numbers are on the board and the pin's been refreshed. "
    "Welcome to the brag."
)

FX_FAILED = (
    "An external API call to Wise or Yahoo failed, so nothing was saved. "
    "Give it a minute and try `/salary` again."
)

TICKER_FAILED = (
    "Couldn't find that ticker, so nothing was saved. "
    "Double-check the `MARKET:TICKER` (e.g. `NASDAQ:TEAM`, `ASX:BHP`) "
    "and try `/salary` again."
)

ADMIN_ONLY_OVERDUE = "That's admin-only. Nice try, civilian."

OVERDUE_NONE = "Nobody's overdue. Everyone is compliant. Suspicious."

OVERDUE_HEADER = "*The naughty list:*"

MODAL_OPEN_FAILED = (
    "Couldn't open the confession booth. Slack hiccup — try again in a sec."
)

PINNED_HEADER = "*The board*"
PINNED_EMPTY = "_Crickets. Nobody's spilled yet._"
PINNED_FOOTER_REBUILT = "_Last rebuilt {stamp}_"

MODAL_INTRO = (
    "Time to confess. Non-AUD amounts convert via Wise; equity details are "
    "below (scroll down — you can list multiple grants)."
)

def equity_section_intro(max_grants: int) -> str:
    return (
        f"*Equity / RSUs* — got topped up during a performance cycle? List each "
        f"grant separately below (up to {max_grants}), new-hire grant included. "
        f"For public equity, enter shares vesting *per year* for that grant "
        f"(not the full multi-year grant) plus a `MARKET:TICKER` — we price it "
        f"and convert to AUD. Private equity is a representative annual dollar "
        f"value per grant."
    )

MODAL_TITLE = "Spill your TC"
MODAL_SUBMIT = "Lock it in"
MODAL_CLOSE = "Never mind"


def member_status(member: Member, config: Config, *, now: datetime | None = None) -> str:
    # Lazy import: formatting imports copy for pinned strings.
    from app.formatting import due_date

    now = now or datetime.now(timezone.utc)

    if not member.last_validated_at:
        deadline = member.joined_at + timedelta(days=config.grace_days)
        if now >= deadline:
            return (
                f"You've got *zero* on the board and your deadline "
                f"({deadline.date().isoformat()}) already passed. "
                f"`/salary` now, or enjoy the boot."
            )
        return (
            f"Nothing disclosed yet. Spill by {deadline.date().isoformat()} "
            f"or Paywall shows you out. Clock's ticking."
        )

    due = due_date(member.last_validated_at, config.revalidate_days)
    overdue_deadline = due + timedelta(days=config.grace_days)
    if now < due:
        return (
            f"You're current. Next re-up: {due.date().isoformat()}. "
            f"Don't get cocky — we check again."
        )
    if now <= overdue_deadline:
        return (
            f"Overdue since {due.date().isoformat()}. "
            f"Update or re-confirm by {overdue_deadline.date().isoformat()} "
            f"or you're out. The pin doesn't wait."
        )
    return (
        f"You've been overdue since {due.date().isoformat()} "
        f"and blew past grace. Expect company policy (the fun kind)."
    )
