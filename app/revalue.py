"""Daily re-pricing of active members' latest disclosures.

`apply_fx` only runs at submission time, so a disclosure's AUD figures are a
snapshot from whenever it was last saved. Ticker prices and FX rates move
daily, so the pinned board's TC numbers drift stale unless something
re-derives them from the original (non-computed) inputs on a schedule.
"""

from __future__ import annotations

import logging

from app.db import Database, Disclosure
from app.fx import FxClient
from app.modal import apply_fx
from app.stocks import StockClient

logger = logging.getLogger(__name__)


def revalue_active_disclosures(db: Database, fx: FxClient, stocks: StockClient) -> None:
    for member, disclosure in db.list_latest_disclosures_for_active():
        try:
            _revalue_one(db, fx, stocks, disclosure)
        except Exception:
            logger.exception(
                "Failed to revalue disclosure %s for %s",
                disclosure.id,
                member.slack_user_id,
            )


def _revalue_one(
    db: Database, fx: FxClient, stocks: StockClient, disclosure: Disclosure
) -> None:
    data = {
        "base_amount": disclosure.base_amount,
        "base_currency": disclosure.base_currency,
        "bonus_type": disclosure.bonus_type,
        "bonus_value": disclosure.bonus_value,
        "bonus_currency": disclosure.bonus_currency,
        "grants": [
            {
                "rsu_type": g.rsu_type,
                "equity_kind": g.equity_kind,
                "rsu_ticker": g.rsu_ticker,
                "rsu_shares_per_year": g.rsu_shares_per_year,
                "rsu_strike_price": g.rsu_strike_price,
                "rsu_amount": g.rsu_amount,
                "rsu_currency": g.rsu_currency,
            }
            for g in disclosure.grants
        ],
    }
    full = apply_fx(data, fx, stocks)

    db.update_disclosure_valuation(
        disclosure.id,
        base_aud=full["base_aud"],
        bonus_aud=full.get("bonus_aud"),
        fx_rate_date=full.get("fx_rate_date"),
    )
    for grant, enriched in zip(disclosure.grants, full["grants"]):
        db.update_grant_valuation(
            grant.id,
            rsu_aud=enriched.get("rsu_aud"),
            rsu_share_price=enriched.get("rsu_share_price"),
            rsu_share_currency=enriched.get("rsu_share_currency"),
        )
