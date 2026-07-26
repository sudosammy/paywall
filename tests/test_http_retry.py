from email.utils import formatdate
import time

import httpx
from pytest_httpx import HTTPXMock

from app.http_retry import get_with_retries, retry_after_seconds


def test_retry_after_seconds_from_header():
    resp = httpx.Response(429, headers={"Retry-After": "7"})
    assert retry_after_seconds(resp, 0) == 7.0


def test_retry_after_seconds_caps_wait():
    resp = httpx.Response(429, headers={"Retry-After": "999"})
    assert retry_after_seconds(resp, 0, max_wait=30.0) == 30.0


def test_retry_after_seconds_http_date():
    when = formatdate(timeval=time.time() + 5, usegmt=True)
    resp_date = httpx.Response(429, headers={"Retry-After": when})
    wait = retry_after_seconds(resp_date, 0)
    assert 0.0 <= wait <= 5.5


def test_retry_after_seconds_falls_back_to_backoff():
    resp = httpx.Response(429)
    assert retry_after_seconds(resp, 0) == 1.0
    assert retry_after_seconds(resp, 1) == 2.0
    assert retry_after_seconds(resp, 2) == 4.0


def test_get_with_retries_succeeds_after_429(httpx_mock: HTTPXMock):
    sleeps: list[float] = []
    httpx_mock.add_response(
        url="https://example.test/x",
        status_code=429,
        headers={"Retry-After": "3"},
    )
    httpx_mock.add_response(
        url="https://example.test/x",
        json={"ok": True},
    )
    resp = get_with_retries(
        "https://example.test/x",
        sleep=sleeps.append,
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert sleeps == [3.0]
    assert len(httpx_mock.get_requests()) == 2


def test_get_with_retries_exhausts_and_returns_last_429(httpx_mock: HTTPXMock):
    sleeps: list[float] = []
    for _ in range(4):
        httpx_mock.add_response(url="https://example.test/x", status_code=429)
    resp = get_with_retries(
        "https://example.test/x",
        max_attempts=4,
        sleep=sleeps.append,
    )
    assert resp.status_code == 429
    assert len(sleeps) == 3
    assert len(httpx_mock.get_requests()) == 4


def test_stocks_retries_on_429(tmp_path, monkeypatch):
    from app.db import Database
    from app import http_retry
    from app.stocks import StockClient

    sleeps: list[float] = []
    monkeypatch.setattr(http_retry.time, "sleep", sleeps.append)

    class FakeResponse:
        def __init__(self, status_code, json_data=None, headers=None):
            self.status_code = status_code
            self._json = json_data
            self.headers = headers or {}

        def json(self):
            return self._json

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    queue = [
        FakeResponse(429, headers={"Retry-After": "1"}),
        FakeResponse(
            200,
            json_data={
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "regularMarketPrice": 86.88,
                                "currency": "USD",
                                "regularMarketTime": 1720000000,
                            }
                        }
                    ],
                    "error": None,
                }
            },
        ),
    ]

    def fake_get(*_args, **_kwargs):
        return queue.pop(0)

    monkeypatch.setattr(http_retry, "_impersonated_get", fake_get)
    client = StockClient(Database(str(tmp_path / "s.db")))
    result = client.get_price("NASDAQ:TEAM")
    assert result.price == 86.88
    assert sleeps == [1.0]


def test_impersonated_get_with_retries(monkeypatch):
    sleeps: list[float] = []
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, status_code, headers=None):
            self.status_code = status_code
            self.headers = headers or {}

        def json(self):
            return {"ok": True}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    queue = [
        FakeResponse(429, headers={"Retry-After": "2"}),
        FakeResponse(200),
    ]

    def fake_get(url, **_kwargs):
        calls.append(url)
        return queue.pop(0)

    monkeypatch.setattr("app.http_retry._impersonated_get", fake_get)
    resp = get_with_retries(
        "https://example.test/yahoo",
        impersonate="chrome",
        sleep=sleeps.append,
    )
    assert resp.status_code == 200
    assert sleeps == [2.0]
    assert len(calls) == 2
