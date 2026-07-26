"""Wise currency conversion with a 24-hour SQLite cache."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.db import Database, utcnow
from app.http_retry import get_with_retries

logger = logging.getLogger(__name__)

WISE_RATES_URL = "https://api.wise.com/v1/rates"
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
    def __init__(self, db: Database, api_token: str) -> None:
        self.db = db
        self.api_token = api_token

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
            rate, fetched_at = self._fetch_wise_rate(source)
            self.db.set_fx_rate(source, rate, fetched_at)
            return rate, fetched_at.date().isoformat()
        except Exception:
            logger.exception("Failed to fetch Wise rate for %s", source)
            if cached:
                logger.warning("Using stale cached rate for %s", source)
                return cached.rate, cached.fetched_at.date().isoformat()
            raise

    def _fetch_wise_rate(self, source: str) -> tuple[float, datetime]:
        headers = {"Authorization": f"Bearer {self.api_token}"}
        params = {"source": source, "target": "AUD"}
        resp = get_with_retries(WISE_RATES_URL, headers=headers, params=params)
        resp.raise_for_status()
        payload = resp.json()

        # Wise returns a list of rate objects, or a single object depending on params.
        if isinstance(payload, list):
            if not payload:
                raise ValueError(f"No Wise rate returned for {source}->AUD")
            entry = payload[0]
        else:
            entry = payload

        rate = float(entry["rate"])
        time_str = entry.get("time")
        if time_str:
            fetched_at = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        else:
            fetched_at = utcnow()
        return rate, fetched_at.astimezone(timezone.utc)
