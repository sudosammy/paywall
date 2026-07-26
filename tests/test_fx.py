from datetime import timedelta

import httpx
import pytest
from pytest_httpx import HTTPXMock

from app.db import Database, utcnow
from app.fx import WISE_RATES_URL, FxClient


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def fx(db: Database) -> FxClient:
    return FxClient(db, api_token="test-token")


def test_aud_is_passthrough(fx: FxClient):
    result = fx.convert_to_aud(100000, "AUD")
    assert result.amount_aud == 100000
    assert result.rate == 1.0
    assert result.currency == "AUD"


def test_fetches_and_caches_wise_rate(fx: FxClient, db: Database, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{WISE_RATES_URL}?source=USD&target=AUD",
        json=[{"rate": 1.5, "time": "2026-07-26T00:00:00Z", "source": "USD", "target": "AUD"}],
    )
    result = fx.convert_to_aud(100, "USD")
    assert result.amount_aud == 150.0
    assert result.rate == 1.5

    cached = db.get_fx_rate("USD")
    assert cached is not None
    assert cached.rate == 1.5

    # Second call should use cache — no additional HTTP request needed.
    requests_before = len(httpx_mock.get_requests())
    result2 = fx.convert_to_aud(200, "USD")
    assert result2.amount_aud == 300.0
    assert len(httpx_mock.get_requests()) == requests_before


def test_falls_back_to_stale_cache_on_api_failure(
    fx: FxClient, db: Database, httpx_mock: HTTPXMock
):
    stale_time = utcnow() - timedelta(days=3)
    db.set_fx_rate("USD", 1.4, stale_time)

    httpx_mock.add_exception(httpx.ConnectError("boom"))
    rate, rate_date = fx.get_rate("USD")
    assert rate == 1.4
    assert rate_date == stale_time.date().isoformat()


def test_raises_when_no_cache_and_api_fails(fx: FxClient, httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    with pytest.raises(Exception):
        fx.get_rate("EUR")
