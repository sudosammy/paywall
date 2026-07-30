"""Amount formatting and pinned summary message rendering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import copy, tax
from app.config import Config
from app.db import Disclosure, Grant, Member


def format_money(amount: float, currency: str) -> str:
    """Always rounds for display (nearest dollar under $1k, nearest $100 as
    'X.Xk', nearest $10k as 'X.XXm') — cents never survive to the board,
    since e.g. a per-share equity conversion is never going to land clean."""
    currency = currency.upper()
    if amount >= 1_000_000:
        body = f"{round(amount / 1_000_000, 2):g}m"
    elif amount >= 1000:
        body = f"{round(amount / 1000, 1):g}k"
    else:
        body = f"{round(amount):,}"

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


def _super_amount_aud(disclosure: Disclosure, au_super_pct: float) -> float:
    """Super that's 'included' in base or 'none' contributes nothing extra —
    it's either already counted in base_aud or doesn't exist."""
    if disclosure.super_type == "on_top_legislated":
        return disclosure.base_aud * (au_super_pct / 100.0)
    if disclosure.super_type == "custom_pct":
        pct = disclosure.super_pct if disclosure.super_pct is not None else au_super_pct
        return disclosure.base_aud * (pct / 100.0)
    return 0.0


def _equity_aud(disclosure: Disclosure) -> float:
    return sum(g.rsu_aud or 0.0 for g in disclosure.grants)


def total_comp_aud(disclosure: Disclosure, au_super_pct: float) -> float:
    """Base + super (if paid on top) + bonus + all equity grants, all in AUD."""
    bonus = disclosure.bonus_aud or 0.0
    return (
        disclosure.base_aud
        + _super_amount_aud(disclosure, au_super_pct)
        + bonus
        + _equity_aud(disclosure)
    )


def taxable_income_aud(disclosure: Disclosure) -> float:
    """Base + bonus + equity — excludes super, which isn't part of an
    individual's assessable income (the fund pays its own contributions tax
    on it instead, separate from personal marginal rates)."""
    return disclosure.base_aud + (disclosure.bonus_aud or 0.0) + _equity_aud(disclosure)


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
    is_options = grant.equity_kind == "options"

    if grant.rsu_type == "public":
        shares = grant.rsu_shares_per_year or 0
        shares_text = f"{int(shares)}" if shares == int(shares) else f"{shares:g}"
        ticker = grant.rsu_ticker or "?"

        if is_options:
            strike = format_money(
                grant.rsu_strike_price or 0, grant.rsu_share_currency or "AUD"
            )
            unit = f"{shares_text} {ticker} options/yr @ {strike} strike"
            text = (
                f"{unit} (~{format_aud(grant.rsu_aud)}/yr spread)"
                if grant.rsu_aud is not None
                else unit
            )
        else:
            unit = f"{shares_text} {ticker} sh/yr"
            text = f"{unit} (~{format_aud(grant.rsu_aud)}/yr)" if grant.rsu_aud is not None else unit
    else:
        # private
        currency = grant.rsu_currency or "AUD"
        suffix = "private, options" if is_options else "private"
        text = (
            format_money_with_aud(grant.rsu_amount or 0, currency, grant.rsu_aud)
            + f"/yr equity ({suffix})"
        )

    if grant.grant_year_start and grant.rsu_note:
        text = f"{text} — {grant.grant_year_start}: {grant.rsu_note}"
    elif grant.grant_year_start:
        text = f"{text} — {grant.grant_year_start}"
    elif grant.rsu_note:
        text = f"{text} — {grant.rsu_note}"
    return text


def format_disclosure_line(
    member: Member,
    disclosure: Disclosure,
    *,
    au_super_pct: float,
) -> str:
    """A name+TC header line followed by one bullet per comp component, using
    Slack's native '- ' list syntax — a single long '+'-chained line becomes
    unreadable once someone has several grants plus free-text notes."""
    base = format_money_with_aud(
        disclosure.base_amount, disclosure.base_currency, disclosure.base_aud
    )
    validated = (
        member.last_validated_at.date().isoformat()
        if member.last_validated_at
        else disclosure.created_at.date().isoformat()
    )
    tc = format_aud(total_comp_aud(disclosure, au_super_pct))
    fy_prefix = f"FY {disclosure.fy_period}, " if disclosure.fy_period else ""
    header = (
        f"*{member.display_name}* → *~{tc} TC*  _({fy_prefix}updated {validated})_"
    )

    bullets = [
        f"- {base} base + {format_super(disclosure, au_super_pct)} + {format_bonus(disclosure)}"
    ]
    bullets.extend(f"- {format_grant(g)}" for g in disclosure.grants)
    if disclosure.other_text:
        bullets.append(f"- {disclosure.other_text.strip()}")

    taxable = taxable_income_aud(disclosure)
    est_tax = format_aud(tax.estimate_tax_aud(taxable))
    marginal_pct = tax.marginal_rate(taxable) * 100
    bullets.append(f"- Est. tax (excl. super): ~{est_tax} ({marginal_pct:g}% marginal)")

    return "\n".join([header, *bullets])


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
        ordered = sorted(
            rows,
            key=lambda pair: total_comp_aud(pair[1], au_super_pct),
            reverse=True,
        )
        body = "\n\n".join(
            format_disclosure_line(m, d, au_super_pct=au_super_pct) for m, d in ordered
        )
        channel_total = sum(
            total_comp_aud(d, au_super_pct) for _, d in ordered
        )
        channel_tax_total = sum(
            tax.estimate_tax_aud(taxable_income_aud(d)) for _, d in ordered
        )
        channel_value = "\n\n" + copy.channel_value_line(
            f"~{format_aud(channel_total)}",
            len(ordered),
            f"~{format_aud(channel_tax_total)}",
        )

    stamp = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    footer = f"\n\n{copy.PINNED_FOOTER_REBUILT.format(stamp=stamp)}"
    return f"{copy.PINNED_HEADER}\n{body}{channel_value}{footer}"


def due_date(last_validated_at: datetime, revalidate_days: int) -> datetime:
    return last_validated_at + timedelta(days=revalidate_days)


def member_status_text(member: Member, config: Config) -> str:
    """Compatibility wrapper — preferred entrypoint is `copy.member_status`."""
    return copy.member_status(member, config)
