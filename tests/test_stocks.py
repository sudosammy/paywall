from datetime import timedelta
from typing import Any

import pytest

from app.db import Database, utcnow
from app.stocks import (
    StockClient,
    TickerNotFoundError,
    parse_ticker,
    to_yahoo_symbol,
)


@pytest.fixture
def stocks(tmp_path) -> StockClient:
    return StockClient(Database(str(tmp_path / "stocks.db")))


def test_parse_ticker_valid():
    assert parse_ticker("nasdaq:team") == ("NASDAQ", "TEAM")
    assert parse_ticker("ASX:BHP") == ("ASX", "BHP")


def test_parse_ticker_rejects_bad_format_and_market():
    assert parse_ticker("TEAM") is None
    assert parse_ticker("FOO:BAR") is None
    assert parse_ticker("") is None


def test_to_yahoo_symbol_suffixes():
    assert to_yahoo_symbol("NASDAQ", "TEAM") == "TEAM"
    assert to_yahoo_symbol("ASX", "BHP") == "BHP.AX"
    assert to_yahoo_symbol("LSE", "HSBA") == "HSBA.L"


def _yahoo_payload(price: float, currency: str, market_time: int = 1720000000):
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": price,
                        "currency": currency,
                        "regularMarketTime": market_time,
                    }
                }
            ],
            "error": None,
        }
    }


class FakeResponse:
    """Minimal duck-typed response for the impersonated Yahoo transport."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_yahoo(monkeypatch, responses: list[FakeResponse]):
    """Queue FakeResponse objects for successive _impersonated_get calls."""
    queue = list(responses)
    calls: list[dict[str, Any]] = []

    def fake_get(url, *, headers=None, params=None, timeout=15.0, impersonate="chrome"):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "timeout": timeout,
                "impersonate": impersonate,
            }
        )
        if not queue:
            raise AssertionError("unexpected extra Yahoo request")
        return queue.pop(0)

    monkeypatch.setattr("app.http_retry._impersonated_get", fake_get)
    return calls


def test_get_price_fetches_and_caches(stocks: StockClient, monkeypatch):
    calls = _patch_yahoo(
        monkeypatch, [FakeResponse(json_data=_yahoo_payload(86.88, "USD"))]
    )
    result = stocks.get_price("NASDAQ:TEAM")
    assert result.price == 86.88
    assert result.currency == "USD"
    assert result.ticker == "NASDAQ:TEAM"
    assert calls[0]["impersonate"] == "chrome"

    # Second call should hit cache — no additional HTTP request.
    cached = stocks.get_price("NASDAQ:TEAM")
    assert cached.price == 86.88
    assert len(calls) == 1


def test_gbpence_normalised_to_gbp(stocks: StockClient, monkeypatch):
    _patch_yahoo(
        monkeypatch, [FakeResponse(json_data=_yahoo_payload(650.0, "GBp"))]
    )
    result = stocks.get_price("LSE:HSBA")
    assert result.currency == "GBP"
    assert result.price == 6.5


def test_ticker_not_found(stocks: StockClient, monkeypatch):
    _patch_yahoo(
        monkeypatch,
        [
            FakeResponse(
                status_code=404,
                json_data={
                    "chart": {
                        "result": None,
                        "error": {
                            "code": "Not Found",
                            "description": "No data found, symbol may be delisted",
                        },
                    }
                },
            )
        ],
    )
    with pytest.raises(TickerNotFoundError, match="delisted"):
        stocks.get_price("NASDAQ:NOPE")


def test_ticker_not_found_chart_error_on_200(stocks: StockClient, monkeypatch):
    _patch_yahoo(
        monkeypatch,
        [
            FakeResponse(
                status_code=200,
                json_data={
                    "chart": {
                        "result": None,
                        "error": {"code": "Not Found", "description": "gone"},
                    }
                },
            )
        ],
    )
    with pytest.raises(TickerNotFoundError, match="gone"):
        stocks.get_price("NASDAQ:NOPE")


def test_stale_cache_fallback(stocks: StockClient, monkeypatch):
    stocks.db.set_stock_price(
        "TEAM",
        80.0,
        "USD",
        fetched_at=utcnow() - timedelta(days=3),
    )

    def boom(*_args, **_kwargs):
        raise ConnectionError("boom")

    monkeypatch.setattr("app.http_retry._impersonated_get", boom)
    result = stocks.get_price("NASDAQ:TEAM")
    assert result.price == 80.0
    assert result.currency == "USD"


def test_exhausted_429_is_not_ticker_not_found(stocks: StockClient, monkeypatch):
    """A hard Yahoo 429 (non-JSON) must surface as a transport failure."""
    _patch_yahoo(
        monkeypatch,
        [
            FakeResponse(status_code=429, text="Too Many Requests"),
            FakeResponse(status_code=429, text="Too Many Requests"),
            FakeResponse(status_code=429, text="Too Many Requests"),
            FakeResponse(status_code=429, text="Too Many Requests"),
        ],
    )
    monkeypatch.setattr("app.http_retry.time.sleep", lambda *_: None)
    with pytest.raises(Exception):
        stocks.get_price("NASDAQ:TEAM")
