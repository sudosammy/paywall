"""Amount formatting and pinned summary message rendering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import copy
from app.config import Config
from app.db import Disclosure, Grant, Member


def format_money(amount: float, currency: str) -> str:
    currency = currency.upper()
    m = amount / 1_000_000
    k = amount / 1000
    if amount >= 1_000_000 and abs(m - round(m, 2)) < 1e-9:
        body = f"{round(m, 2):g}m"
    elif amount >= 1000 and abs(k - round(k)) < 1e-9:
        body = f"{int(round(k))}k"
    elif amount >= 1000 and abs(k - round(k, 1)) < 1e-9:
        body = f"{round(k, 1):.1f}k"
    elif amount == int(amount):
        body = f"{amount:,.0f}"
    else:
        body = f"{amount:,.2f}"

    if currency == "AUD":
        return f"${body}"
    return f"{currency} {body}"


def format_money_with_aud(
    amount: float, currency: str, amount_aud: float | None
) -> str:
    currency = currency.upper()
    local = format_money(amount, currency)
    if currency == "AUD" or amount_aud is None:
        return local
    aud = format_money(amount_aud, "AUD")
    aud_paren = aud.replace("$", "A$", 1)
    return f"{local} (~{aud_paren})"


def format_aud(amount: float) -> str:
    return format_money(amount, "AUD").replace("$", "A$", 1)


def total_comp_aud(disclosure: Disclosure, au_super_pct: float) -> float:
    """Base + super (if paid on top) + bonus + all equity grants, all in AUD.

    Super that's 'included' in base or 'none' contributes nothing extra —
    it's either already counted in base_aud or doesn't exist.
    """
    if disclosure.super_type == "on_top_legislated":
        super_amount = disclosure.base_aud * (au_super_pct / 100.0)
    elif disclosure.super_type == "custom_pct":
        pct = disclosure.super_pct if disclosure.super_pct is not None else au_super_pct
        super_amount = disclosure.base_aud * (pct / 100.0)
    else:
        super_amount = 0.0

    bonus = disclosure.bonus_aud or 0.0
    equity = sum(g.rsu_aud or 0.0 for g in disclosure.grants)
    return disclosure.base_aud + super_amount + bonus + equity


def format_super(disclosure: Disclosure, au_super_pct: float) -> str:
    if disclosure.super_type == "on_top_legislated":
        return f"{au_super_pct:g}% super"
    if disclosure.super_type == "included":
        return "super (included)"
    if disclosure.super_type == "custom_pct":
        pct = disclosure.super_pct if disclosure.super_pct is not None else au_super_pct
        return f"{pct:g}% super"
    return "no super"


def format_bonus(disclosure: Disclosure) -> str:
    if disclosure.bonus_type == "none":
        text = "no bonus"
    elif disclosure.bonus_type == "pct_of_base":
        pct = disclosure.bonus_value or 0
        text = f"{pct:g}% bonus"
    else:
        currency = disclosure.bonus_currency or disclosure.base_currency
        text = (
            format_money_with_aud(
                disclosure.bonus_value or 0, currency, disclosure.bonus_aud
            )
            + " bonus"
        )

    if disclosure.bonus_note:
        text = f"{text} ({disclosure.bonus_note})"
    return text


def format_grant(grant: Grant) -> str:
    if grant.rsu_type == "public":
        shares = grant.rsu_shares_per_year or 0
        if shares == int(shares):
            shares_text = f"{int(shares)}"
        else:
            shares_text = f"{shares:g}"
        ticker = grant.rsu_ticker or "?"
        if grant.rsu_aud is not None:
            aud = format_money(grant.rsu_aud, "AUD").replace("$", "A$", 1)
            text = f"{shares_text} {ticker} sh/yr (~{aud}/yr)"
        else:
            text = f"{shares_text} {ticker} sh/yr"
    else:
        # private
        currency = grant.rsu_currency or "AUD"
        text = (
            format_money_with_aud(grant.rsu_amount or 0, currency, grant.rsu_aud)
            + "/yr equity (private)"
        )

    if grant.rsu_note:
        text = f"{text} ({grant.rsu_note})"
    return text


def format_grants(grants: list[Grant]) -> str:
    """Multiple concurrent grants (e.g. yearly top-ups) are itemized rather
    than summed, so individual share counts/tickers stay visible on the board."""
    if not grants:
        return "$0 stock"
    return " + ".join(format_grant(g) for g in grants)


def format_disclosure_line(
    member: Member,
    disclosure: Disclosure,
    *,
    au_super_pct: float,
) -> str:
    base = format_money_with_aud(
        disclosure.base_amount, disclosure.base_currency, disclosure.base_aud
    )
    parts = [
        f"{base} base",
        format_super(disclosure, au_super_pct),
        format_bonus(disclosure),
        format_grants(disclosure.grants),
    ]
    if disclosure.other_text:
        parts.append(disclosure.other_text.strip())

    validated = (
        member.last_validated_at.date().isoformat()
        if member.last_validated_at
        else disclosure.created_at.date().isoformat()
    )
    tc = format_aud(total_comp_aud(disclosure, au_super_pct))
    return (
        f"*{member.display_name}*: {' + '.join(parts)} → *~{tc} TC*"
        f"  _(updated {validated})_"
    )


def build_pinned_message(
    rows: list[tuple[Member, Disclosure]],
    *,
    au_super_pct: float,
    generated_at: datetime | None = None,
) -> str:
    if not rows:
        body = copy.PINNED_EMPTY
        channel_value = ""
    else:
        ordered = sorted(rows, key=lambda pair: pair[1].base_aud, reverse=True)
        body = "\n".join(
            format_disclosure_line(m, d, au_super_pct=au_super_pct) for m, d in ordered
        )
        channel_total = sum(
            total_comp_aud(d, au_super_pct) for _, d in ordered
        )
        channel_value = "\n\n" + copy.channel_value_line(
            f"~{format_aud(channel_total)}", len(ordered)
        )

    stamp = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    footer = f"\n\n{copy.PINNED_FOOTER_REBUILT.format(stamp=stamp)}"
    return f"{copy.PINNED_HEADER}\n{body}{channel_value}{footer}"


def due_date(last_validated_at: datetime, revalidate_days: int) -> datetime:
    return last_validated_at + timedelta(days=revalidate_days)


def member_status_text(member: Member, config: Config) -> str:
    """Compatibility wrapper — preferred entrypoint is `copy.member_status`."""
    return copy.member_status(member, config)
