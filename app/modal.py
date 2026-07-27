"""Disclosure modal construction, parsing, and validation.

Parsing/validation is deliberately split from FX / stock conversion:
`parse_submission` is fast and runs before the Slack 3-second ack deadline
(so field errors can be returned via response_action), while `apply_fx`
performs Frankfurter / Yahoo API calls after the ack.
"""

from __future__ import annotations

from typing import Any

from app import copy
from app.db import Disclosure, Grant
from app.fx import SUPPORTED_CURRENCIES, FxClient
from app.stocks import SUPPORTED_MARKETS, StockClient, parse_ticker

CALLBACK_ID = "salary_disclosure_modal"

# Companies commonly top up RSU grants yearly during performance cycles, so a
# member can have several concurrent grants (different tickers/quantities).
# Modals can't repeat blocks dynamically, so we offer a fixed number of slots.
MAX_GRANTS = 4


def _currency_options() -> list[dict[str, Any]]:
    return [
        {
            "text": {"type": "plain_text", "text": ccy},
            "value": ccy,
        }
        for ccy in SUPPORTED_CURRENCIES
    ]


def _selected_option(currency: str) -> dict[str, Any]:
    return {
        "text": {"type": "plain_text", "text": currency.upper()},
        "value": currency.upper(),
    }


def _initial_from_disclosure(disclosure: Disclosure | None) -> dict[str, Any]:
    if not disclosure:
        return {
            "base_amount": "",
            "base_currency": "AUD",
            "super_type": "on_top_legislated",
            "super_pct": "",
            "bonus_type": "none",
            "bonus_value": "",
            "bonus_currency": "AUD",
            "bonus_note": "",
            "other_text": "",
        }
    return {
        "base_amount": _num(disclosure.base_amount),
        "base_currency": disclosure.base_currency,
        "super_type": disclosure.super_type,
        "super_pct": _num(disclosure.super_pct) if disclosure.super_pct is not None else "",
        "bonus_type": disclosure.bonus_type,
        "bonus_value": _num(disclosure.bonus_value) if disclosure.bonus_value is not None else "",
        "bonus_currency": disclosure.bonus_currency or disclosure.base_currency,
        "bonus_note": disclosure.bonus_note or "",
        "other_text": disclosure.other_text or "",
    }


def _empty_grant_init(base_currency: str) -> dict[str, Any]:
    return {
        "rsu_type": "none",
        "equity_kind": "rsu",
        "rsu_ticker": "",
        "rsu_shares_per_year": "",
        "strike_price": "",
        "rsu_amount": "",
        "rsu_currency": base_currency,
        "rsu_note": "",
    }


def _grant_init(grant: Grant, base_currency: str) -> dict[str, Any]:
    return {
        "rsu_type": grant.rsu_type,
        "equity_kind": grant.equity_kind,
        "rsu_ticker": grant.rsu_ticker or "",
        "rsu_shares_per_year": (
            _num(grant.rsu_shares_per_year)
            if grant.rsu_shares_per_year is not None
            else ""
        ),
        "strike_price": (
            _num(grant.rsu_strike_price) if grant.rsu_strike_price is not None else ""
        ),
        "rsu_amount": _num(grant.rsu_amount) if grant.rsu_amount is not None else "",
        "rsu_currency": grant.rsu_currency or base_currency,
        "rsu_note": grant.rsu_note or "",
    }


def _grants_init(disclosure: Disclosure | None, base_currency: str) -> list[dict[str, Any]]:
    existing = disclosure.grants if disclosure else []
    return [
        _grant_init(existing[i], base_currency)
        if i < len(existing)
        else _empty_grant_init(base_currency)
        for i in range(MAX_GRANTS)
    ]


def _grant_block_id(index: int, field: str) -> str:
    return f"grant{index}_{field}"


def _num(value: float | None) -> str:
    if value is None:
        return ""
    if value == int(value):
        return str(int(value))
    return str(value)


def _text_input(
    *,
    placeholder: str,
    initial: str = "",
    multiline: bool = False,
) -> dict[str, Any]:
    """plain_text_input element; omits initial_value when empty (Slack is
    picky about empty strings in some fields)."""
    element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": "value",
        "placeholder": {"type": "plain_text", "text": placeholder},
    }
    if multiline:
        element["multiline"] = True
    if initial:
        element["initial_value"] = initial
    return element


def _radio(options: list[tuple[str, str]], selected: str) -> dict:
    opts = [
        {
            "text": {"type": "plain_text", "text": label},
            "value": value,
        }
        for value, label in options
    ]
    initial = next(o for o in opts if o["value"] == selected)
    return {
        "type": "radio_buttons",
        "action_id": "value",
        "options": opts,
        "initial_option": initial,
    }


def _grant_blocks(index: int, init: dict[str, Any]) -> list[dict[str, Any]]:
    def bid(field: str) -> str:
        return _grant_block_id(index, field)

    return [
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*Grant {index} of {MAX_GRANTS}*"}
            ],
        },
        {
            "type": "input",
            "block_id": bid("rsu_type"),
            "element": _radio(
                [
                    ("none", "None"),
                    ("public", "Public company (listed)"),
                    ("private", "Private company"),
                ],
                init["rsu_type"] if init["rsu_type"] in ("none", "public", "private") else "none",
            ),
            "label": {"type": "plain_text", "text": "Equity / RSUs"},
        },
        {
            "type": "input",
            "block_id": bid("equity_kind"),
            "element": _radio(
                [
                    ("rsu", "RSU"),
                    ("options", "Options"),
                ],
                init["equity_kind"] if init["equity_kind"] in ("rsu", "options") else "rsu",
            ),
            "label": {"type": "plain_text", "text": "Instrument"},
        },
        {
            "type": "input",
            "block_id": bid("rsu_ticker"),
            "optional": True,
            "element": _text_input(
                placeholder="e.g. NASDAQ:TEAM or ASX:BHP",
                initial=init["rsu_ticker"],
            ),
            "label": {"type": "plain_text", "text": "Ticker (if public)"},
        },
        {
            "type": "input",
            "block_id": bid("rsu_shares_per_year"),
            "optional": True,
            "element": _text_input(
                placeholder="shares/options vesting per year, e.g. 500",
                initial=init["rsu_shares_per_year"],
            ),
            "label": {"type": "plain_text", "text": "Shares/options per year (if public)"},
        },
        {
            "type": "input",
            "block_id": bid("strike_price"),
            "optional": True,
            "element": _text_input(
                placeholder="exercise price per share, e.g. 45.00",
                initial=init["strike_price"],
            ),
            "label": {"type": "plain_text", "text": "Strike price (if public options)"},
        },
        {
            "type": "input",
            "block_id": bid("rsu_amount"),
            "optional": True,
            "element": _text_input(
                placeholder="e.g. 50000", initial=init["rsu_amount"]
            ),
            "label": {
                "type": "plain_text",
                "text": "Representative annual value (if private)",
            },
        },
        {
            "type": "input",
            "block_id": bid("rsu_currency"),
            "optional": True,
            "element": {
                "type": "static_select",
                "action_id": "value",
                "options": _currency_options(),
                "initial_option": _selected_option(init["rsu_currency"]),
            },
            "label": {
                "type": "plain_text",
                "text": "Equity currency (if private)",
            },
        },
        {
            "type": "input",
            "block_id": bid("rsu_note"),
            "optional": True,
            "element": _text_input(
                placeholder="e.g. new-hire grant, 2024 top-up, cliff year...",
                initial=init["rsu_note"],
            ),
            "label": {"type": "plain_text", "text": "Equity note"},
        },
    ]


def build_disclosure_modal(disclosure: Disclosure | None = None) -> dict[str, Any]:
    init = _initial_from_disclosure(disclosure)
    grants_init = _grants_init(disclosure, init["base_currency"])

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": copy.MODAL_INTRO,
            },
        },
        {
            "type": "input",
            "block_id": "base_amount",
            "element": _text_input(
                placeholder="e.g. 250000", initial=init["base_amount"]
            ),
            "label": {"type": "plain_text", "text": "Base salary (annual)"},
        },
        {
            "type": "input",
            "block_id": "base_currency",
            "element": {
                "type": "static_select",
                "action_id": "value",
                "options": _currency_options(),
                "initial_option": _selected_option(init["base_currency"]),
            },
            "label": {"type": "plain_text", "text": "Base currency"},
        },
        {
            "type": "input",
            "block_id": "super_type",
            "element": _radio(
                [
                    ("on_top_legislated", "On top at AU legislated rate"),
                    ("included", "Included in base"),
                    ("custom_pct", "Custom % (enter below)"),
                    ("none", "None / N/A"),
                ],
                init["super_type"],
            ),
            "label": {"type": "plain_text", "text": "Superannuation / pension"},
        },
        {
            "type": "input",
            "block_id": "super_pct",
            "optional": True,
            "element": _text_input(placeholder="e.g. 14", initial=init["super_pct"]),
            "label": {"type": "plain_text", "text": "Custom super % (if applicable)"},
        },
        {
            "type": "input",
            "block_id": "bonus_type",
            "element": _radio(
                [
                    ("none", "None"),
                    ("pct_of_base", "% of base"),
                    ("fixed_amount", "Fixed annual amount"),
                ],
                init["bonus_type"],
            ),
            "label": {"type": "plain_text", "text": "Bonus"},
        },
        {
            "type": "input",
            "block_id": "bonus_value",
            "optional": True,
            "element": _text_input(
                placeholder="% or amount, e.g. 20 or 32000",
                initial=init["bonus_value"],
            ),
            "label": {"type": "plain_text", "text": "Bonus value"},
        },
        {
            "type": "input",
            "block_id": "bonus_currency",
            "optional": True,
            "element": {
                "type": "static_select",
                "action_id": "value",
                "options": _currency_options(),
                "initial_option": _selected_option(init["bonus_currency"]),
            },
            "label": {"type": "plain_text", "text": "Bonus currency (if fixed amount)"},
        },
        {
            "type": "input",
            "block_id": "bonus_note",
            "optional": True,
            "element": _text_input(
                placeholder="e.g. got 130% of target last year",
                initial=init["bonus_note"],
            ),
            "label": {"type": "plain_text", "text": "Bonus note"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": copy.equity_section_intro(MAX_GRANTS)},
        },
    ]

    for i in range(1, MAX_GRANTS + 1):
        blocks.extend(_grant_blocks(i, grants_init[i - 1]))

    blocks.append(
        {
            "type": "input",
            "block_id": "other_text",
            "optional": True,
            "element": _text_input(
                placeholder="Anything else (flex, loans, misc benefits...)",
                initial=init["other_text"],
                multiline=True,
            ),
            "label": {"type": "plain_text", "text": "Other"},
        }
    )

    return {
        "type": "modal",
        "callback_id": CALLBACK_ID,
        "title": {"type": "plain_text", "text": copy.MODAL_TITLE},
        "submit": {"type": "plain_text", "text": copy.MODAL_SUBMIT},
        "close": {"type": "plain_text", "text": copy.MODAL_CLOSE},
        "blocks": blocks,
    }


def _get_plain(values: dict, block_id: str) -> str:
    block = values.get(block_id, {})
    element = next(iter(block.values()), {})
    return (element.get("value") or "").strip()


def _get_select(values: dict, block_id: str) -> str | None:
    block = values.get(block_id, {})
    element = next(iter(block.values()), {})
    selected = element.get("selected_option")
    if not selected:
        return None
    return selected.get("value")


def _parse_number(raw: str, *, field: str, errors: dict[str, str]) -> float | None:
    if not raw:
        return None
    cleaned = raw.replace(",", "").replace("$", "").replace("%", "").strip()
    multiplier = 1.0
    if cleaned.lower().endswith("k"):
        cleaned = cleaned[:-1].strip()
        multiplier = 1000.0
    try:
        value = float(cleaned) * multiplier
    except ValueError:
        errors[field] = "Enter a valid number"
        return None
    if value < 0:
        errors[field] = "Must be zero or positive"
        return None
    return value


def _parse_grant_slot(
    values: dict[str, Any],
    index: int,
    base_currency: str,
    errors: dict[str, str],
) -> dict[str, Any] | None:
    """Validate one grant slot. Returns a grant dict, or None if the slot is
    unused (rsu_type == 'none'), mirroring the mismatch checks used elsewhere
    in this modal (filled fields under a 'None' selection are rejected)."""

    def bid(field: str) -> str:
        return _grant_block_id(index, field)

    rsu_type = _get_select(values, bid("rsu_type")) or "none"
    equity_kind = _get_select(values, bid("equity_kind")) or "rsu"
    rsu_ticker_raw = _get_plain(values, bid("rsu_ticker"))
    rsu_shares_raw = _get_plain(values, bid("rsu_shares_per_year"))
    rsu_shares_per_year = _parse_number(
        rsu_shares_raw, field=bid("rsu_shares_per_year"), errors=errors
    )
    strike_price_raw = _get_plain(values, bid("strike_price"))
    strike_price = _parse_number(strike_price_raw, field=bid("strike_price"), errors=errors)
    rsu_amount_raw = _get_plain(values, bid("rsu_amount"))
    rsu_amount = _parse_number(rsu_amount_raw, field=bid("rsu_amount"), errors=errors)
    rsu_currency: str | None = (
        _get_select(values, bid("rsu_currency")) or base_currency
    ).upper()
    rsu_note = _get_plain(values, bid("rsu_note")) or None

    rsu_ticker: str | None = None
    equity_mismatch = (
        "You entered equity details but selected None — "
        "pick Public/Private or clear this field."
    )

    if rsu_type == "none":
        if rsu_ticker_raw:
            errors[bid("rsu_ticker")] = equity_mismatch
        if rsu_shares_raw:
            errors[bid("rsu_shares_per_year")] = equity_mismatch
        if rsu_amount_raw:
            errors[bid("rsu_amount")] = equity_mismatch
        if strike_price_raw:
            errors[bid("strike_price")] = equity_mismatch
        return None
    elif rsu_type == "public":
        if rsu_amount_raw:
            errors[bid("rsu_amount")] = (
                "You entered a private equity amount but selected Public — "
                "clear this field or switch to Private."
            )
        rsu_amount = None
        rsu_currency = None
        if not rsu_ticker_raw:
            errors[bid("rsu_ticker")] = "Enter a ticker as MARKET:SYMBOL"
        else:
            parsed = parse_ticker(rsu_ticker_raw)
            if not parsed:
                markets = ", ".join(SUPPORTED_MARKETS)
                errors[bid("rsu_ticker")] = f"Use MARKET:TICKER (supported: {markets})"
            else:
                market, symbol = parsed
                rsu_ticker = f"{market}:{symbol}"
        if rsu_shares_per_year is None and bid("rsu_shares_per_year") not in errors:
            errors[bid("rsu_shares_per_year")] = "Enter shares vesting per year"
        elif rsu_shares_per_year is not None and rsu_shares_per_year <= 0:
            errors[bid("rsu_shares_per_year")] = "Must be greater than zero"

        if equity_kind == "options":
            if strike_price is None and bid("strike_price") not in errors:
                errors[bid("strike_price")] = "Enter a strike/exercise price for options"
            elif strike_price is not None and strike_price <= 0:
                errors[bid("strike_price")] = "Must be greater than zero"
        else:
            if strike_price_raw:
                errors[bid("strike_price")] = (
                    "You entered a strike price but selected RSU — "
                    "pick Options or clear this field."
                )
            strike_price = None
    elif rsu_type == "private":
        if rsu_ticker_raw:
            errors[bid("rsu_ticker")] = (
                "You entered a ticker but selected Private — "
                "clear this field or switch to Public."
            )
        if rsu_shares_raw:
            errors[bid("rsu_shares_per_year")] = (
                "You entered shares but selected Private — "
                "clear this field or switch to Public."
            )
        if strike_price_raw:
            errors[bid("strike_price")] = (
                "Strike price only applies to public options — clear this field."
            )
        rsu_ticker = None
        rsu_shares_per_year = None
        strike_price = None
        if rsu_amount is None and bid("rsu_amount") not in errors:
            errors[bid("rsu_amount")] = "Enter a representative annual equity value"
        elif rsu_amount is not None and rsu_amount <= 0:
            errors[bid("rsu_amount")] = "Must be greater than zero"
        if rsu_currency and rsu_currency not in SUPPORTED_CURRENCIES:
            errors[bid("rsu_currency")] = "Unsupported currency"
    else:
        errors[bid("rsu_type")] = "Pick none, public, or private"

    return {
        "rsu_type": rsu_type,
        "equity_kind": equity_kind,
        "rsu_ticker": rsu_ticker,
        "rsu_shares_per_year": rsu_shares_per_year,
        "rsu_strike_price": strike_price,
        "rsu_amount": rsu_amount,
        "rsu_currency": rsu_currency,
        "rsu_note": rsu_note,
    }


def parse_submission(
    values: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Validate modal state and return (data, errors) without FX conversion.

    data is None when validation fails; error keys are block_ids so they can
    be passed straight to ack(response_action="errors").
    """
    errors: dict[str, str] = {}

    base_raw = _get_plain(values, "base_amount")
    base_amount = _parse_number(base_raw, field="base_amount", errors=errors)
    if base_amount is None and "base_amount" not in errors:
        errors["base_amount"] = "Base salary is required"
    elif base_amount is not None and base_amount <= 0:
        errors["base_amount"] = "Base salary must be greater than zero"

    base_currency = (_get_select(values, "base_currency") or "AUD").upper()
    if base_currency not in SUPPORTED_CURRENCIES:
        errors["base_currency"] = "Unsupported currency"

    super_type = _get_select(values, "super_type") or "on_top_legislated"
    super_pct_raw = _get_plain(values, "super_pct")
    super_pct = _parse_number(super_pct_raw, field="super_pct", errors=errors)
    if super_type == "custom_pct" and super_pct is None:
        errors["super_pct"] = "Enter a custom super %"
    elif super_type != "custom_pct" and super_pct_raw:
        errors["super_pct"] = (
            "You entered a custom super % but didn't select Custom % — "
            "pick Custom % or clear this field."
        )
        super_pct = None
    elif super_type != "custom_pct":
        super_pct = None

    bonus_type = _get_select(values, "bonus_type") or "none"
    bonus_value_raw = _get_plain(values, "bonus_value")
    bonus_value = _parse_number(bonus_value_raw, field="bonus_value", errors=errors)
    bonus_currency: str | None = (
        _get_select(values, "bonus_currency") or base_currency
    ).upper()
    bonus_note = _get_plain(values, "bonus_note") or None

    if bonus_type == "none":
        if bonus_value_raw:
            errors["bonus_value"] = (
                "You entered a bonus value but selected None — "
                "pick % of base / Fixed amount or clear this field."
            )
        bonus_value = None
        bonus_currency = None
    elif bonus_value is None and "bonus_value" not in errors:
        errors["bonus_value"] = "Enter a bonus value"
    elif bonus_type == "pct_of_base":
        bonus_currency = base_currency
        if bonus_value is not None and bonus_value > 200:
            errors["bonus_value"] = "Bonus % looks too high"

    grants: list[dict[str, Any]] = []
    for i in range(1, MAX_GRANTS + 1):
        grant = _parse_grant_slot(values, i, base_currency, errors)
        if grant is not None:
            grants.append(grant)

    other_text = _get_plain(values, "other_text") or None

    if errors:
        return None, errors

    data = {
        "base_amount": base_amount,
        "base_currency": base_currency,
        "super_type": super_type,
        "super_pct": super_pct,
        "bonus_type": bonus_type,
        "bonus_value": bonus_value,
        "bonus_currency": bonus_currency,
        "bonus_note": bonus_note,
        "grants": grants,
        "other_text": other_text,
    }
    return data, {}


def apply_fx(
    data: dict[str, Any], fx: FxClient, stocks: StockClient
) -> dict[str, Any]:
    """Add AUD snapshot fields (and public share-price snapshots) to parsed data.

    Raises on conversion / price-lookup failure (no cached value and API down).
    """
    base_conv = fx.convert_to_aud(data["base_amount"], data["base_currency"])
    bonus_aud = None
    rate_dates = [base_conv.rate_date]

    if data["bonus_type"] == "fixed_amount" and data["bonus_value"] is not None:
        bonus_conv = fx.convert_to_aud(data["bonus_value"], data["bonus_currency"])
        bonus_aud = bonus_conv.amount_aud
        rate_dates.append(bonus_conv.rate_date)
    elif data["bonus_type"] == "pct_of_base" and data["bonus_value"] is not None:
        bonus_aud = round(base_conv.amount_aud * (data["bonus_value"] / 100.0), 2)

    grants: list[dict[str, Any]] = []
    for grant in data.get("grants") or []:
        enriched = dict(grant)
        if grant["rsu_type"] == "public":
            quote = stocks.get_price(grant["rsu_ticker"])
            if grant.get("equity_kind") == "options":
                # Options are only worth the spread over the strike price —
                # never the full share price like a vested RSU.
                spread = max(0.0, quote.price - grant["rsu_strike_price"])
                annual_value = grant["rsu_shares_per_year"] * spread
            else:
                annual_value = grant["rsu_shares_per_year"] * quote.price
            rsu_conv = fx.convert_to_aud(annual_value, quote.currency)
            enriched["rsu_aud"] = rsu_conv.amount_aud
            enriched["rsu_share_price"] = quote.price
            enriched["rsu_share_currency"] = quote.currency
            rate_dates.append(rsu_conv.rate_date)
            rate_dates.append(quote.price_date)
        elif grant["rsu_type"] == "private" and grant["rsu_amount"] is not None:
            rsu_conv = fx.convert_to_aud(grant["rsu_amount"], grant["rsu_currency"])
            enriched["rsu_aud"] = rsu_conv.amount_aud
            enriched["rsu_share_price"] = None
            enriched["rsu_share_currency"] = None
            rate_dates.append(rsu_conv.rate_date)
        else:
            enriched["rsu_aud"] = None
            enriched["rsu_share_price"] = None
            enriched["rsu_share_currency"] = None
        grants.append(enriched)

    result = dict(data)
    result.update(
        {
            "base_aud": base_conv.amount_aud,
            "bonus_aud": bonus_aud,
            "grants": grants,
            "fx_rate_date": max(rate_dates),
        }
    )
    return result
