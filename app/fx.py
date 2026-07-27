"""Frankfurter (ECB reference rates) currency conversion with a 24-hour
SQLite cache. No API key required."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.db import Database, utcnow
from app.http_retry import get_with_retries

logger = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
CACHE_TTL = timedelta(hours=24)
SUPPORTED_CURRENCIES = (
    "AUD",
    "NZD",
    "USD",
    "GBP",
    "EUR",
    "SGD",
    "CAD",
    "JPY",
    "HKD",
    "CHF",
)


@dataclass
class ConversionResult:
    amount: float
    currency: str
    amount_aud: float
    rate: float
    rate_date: str


class FxClient:
    def __init__(self, db: Database) -> None:
        self.db = db

    def convert_to_aud(
        self, amount: float, currency: str
    ) -> ConversionResult:
        currency = currency.upper()
        if currency == "AUD":
            today = utcnow().date().isoformat()
            return ConversionResult(
                amount=amount,
                currency="AUD",
                amount_aud=amount,
                rate=1.0,
                rate_date=today,
            )

        rate, rate_date = self.get_rate(currency)
        return ConversionResult(
            amount=amount,
            currency=currency,
            amount_aud=round(amount * rate, 2),
            rate=rate,
            rate_date=rate_date,
        )

    def get_rate(self, source: str) -> tuple[float, str]:
        source = source.upper()
        if source == "AUD":
            return 1.0, utcnow().date().isoformat()

        cached = self.db.get_fx_rate(source)
        if cached and cached.fetched_at >= utcnow() - CACHE_TTL:
            return cached.rate, cached.fetched_at.date().isoformat()

        try:
            rate, fetched_at = self._fetch_frankfurter_rate(source)
            self.db.set_fx_rate(source, rate, fetched_at)
            return rate, fetched_at.date().isoformat()
        except Exception:
            logger.exception("Failed to fetch Frankfurter rate for %s", source)
            if cached:
                logger.warning("Using stale cached rate for %s", source)
                return cached.rate, cached.fetched_at.date().isoformat()
            raise

    def _fetch_frankfurter_rate(self, source: str) -> tuple[float, datetime]:
        params = {"base": source, "symbols": "AUD"}
        resp = get_with_retries(FRANKFURTER_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()

        rate = float(payload["rates"]["AUD"])
        date_str = payload.get("date")
        if date_str:
            fetched_at = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        else:
            fetched_at = utcnow()
        return rate, fetched_at
