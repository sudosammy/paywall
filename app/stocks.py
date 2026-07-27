"""Yahoo Finance stock price lookup with a 24-hour SQLite cache."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.db import Database, utcnow
from app.http_retry import get_with_retries

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
CACHE_TTL = timedelta(hours=24)

# Markets whose quote currencies are supported by the Frankfurter FX client.
MARKET_YAHOO_SUFFIX: dict[str, str] = {
    "NASDAQ": "",
    "NYSE": "",
    "ASX": ".AX",
    "LSE": ".L",
    "NZX": ".NZ",
    "TSX": ".TO",
    "SGX": ".SI",
    "HKEX": ".HK",
    "TYO": ".T",
    "SWX": ".SW",
}

SUPPORTED_MARKETS = tuple(MARKET_YAHOO_SUFFIX.keys())
TICKER_RE = re.compile(r"^([A-Z]+):([A-Z0-9.]+)$")


class TickerNotFoundError(ValueError):
    """Raised when Yahoo returns no quote for the given ticker."""


@dataclass
class PriceResult:
    ticker: str
    price: float
    currency: str
    price_date: str


def parse_ticker(raw: str) -> tuple[str, str] | None:
    """Return (market, symbol) if *raw* is a valid MARKET:TICKER, else None."""
    match = TICKER_RE.match(raw.strip().upper())
    if not match:
        return None
    market, symbol = match.group(1), match.group(2)
    if market not in MARKET_YAHOO_SUFFIX:
        return None
    return market, symbol


def to_yahoo_symbol(market: str, symbol: str) -> str:
    suffix = MARKET_YAHOO_SUFFIX[market]
    return f"{symbol}{suffix}"


class StockClient:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_price(self, ticker: str) -> PriceResult:
        ticker = ticker.strip().upper()
        parsed = parse_ticker(ticker)
        if not parsed:
            raise TickerNotFoundError(f"Unsupported ticker format: {ticker}")
        market, symbol = parsed
        yahoo_symbol = to_yahoo_symbol(market, symbol)

        cached = self.db.get_stock_price(yahoo_symbol)
        if cached and cached.fetched_at >= utcnow() - CACHE_TTL:
            return PriceResult(
                ticker=ticker,
                price=cached.price,
                currency=cached.currency,
                price_date=cached.fetched_at.date().isoformat(),
            )

        try:
            price, currency, price_date = self._fetch_yahoo(yahoo_symbol)
            fetched_at = utcnow()
            self.db.set_stock_price(yahoo_symbol, price, currency, fetched_at)
            return PriceResult(
                ticker=ticker,
                price=price,
                currency=currency,
                price_date=price_date,
            )
        except TickerNotFoundError:
            raise
        except Exception:
            logger.exception("Failed to fetch Yahoo price for %s", yahoo_symbol)
            if cached:
                logger.warning("Using stale cached price for %s", yahoo_symbol)
                return PriceResult(
                    ticker=ticker,
                    price=cached.price,
                    currency=cached.currency,
                    price_date=cached.fetched_at.date().isoformat(),
                )
            raise

    def _fetch_yahoo(self, yahoo_symbol: str) -> tuple[float, str, str]:
        url = YAHOO_CHART_URL.format(symbol=yahoo_symbol)
        params = {"interval": "1d", "range": "1d"}
        # Yahoo blocks plain HTTP clients via TLS fingerprinting; curl_cffi
        # with Chrome impersonation is required for a successful quote.
        resp = get_with_retries(url, params=params, impersonate="chrome")

        # Unknown tickers return 404 with a chart.error JSON body — classify
        # before raise_for_status so users get TICKER_FAILED, not FX_FAILED.
        payload: dict | None = None
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = None

        if payload is not None:
            chart = payload.get("chart") or {}
            error = chart.get("error")
            if resp.status_code == 404 or error:
                description = ""
                if isinstance(error, dict):
                    description = (
                        error.get("description") or error.get("code") or ""
                    )
                detail = f": {description}" if description else ""
                raise TickerNotFoundError(
                    f"No quote for {yahoo_symbol}{detail}"
                )

        resp.raise_for_status()

        chart = (payload or {}).get("chart") or {}
        results = chart.get("result") or []
        if not results:
            raise TickerNotFoundError(f"No quote for {yahoo_symbol}")

        meta = results[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        currency = meta.get("currency")
        if price is None or not currency:
            raise TickerNotFoundError(f"Incomplete quote for {yahoo_symbol}")

        price = float(price)
        currency = str(currency)

        # Yahoo quotes LSE stocks in pence (GBp); normalise to GBP pounds.
        if currency == "GBp":
            price = price / 100.0
            currency = "GBP"

        market_time = meta.get("regularMarketTime")
        if isinstance(market_time, (int, float)):
            price_date = datetime.fromtimestamp(
                market_time, tz=timezone.utc
            ).date().isoformat()
        else:
            price_date = utcnow().date().isoformat()

        return price, currency.upper(), price_date
