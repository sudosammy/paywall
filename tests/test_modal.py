from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from app.db import Database
from app.fx import WISE_RATES_URL, FxClient
from app.modal import apply_fx, build_disclosure_modal, parse_submission
from app.stocks import StockClient


class FakeYahooResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _values(**overrides):
    base = {
        "base_amount": {"value": {"value": "250000"}},
        "base_currency": {
            "value": {"selected_option": {"value": "AUD"}}
        },
        "super_type": {
            "value": {"selected_option": {"value": "on_top_legislated"}}
        },
        "super_pct": {"value": {"value": ""}},
        "bonus_type": {
            "value": {"selected_option": {"value": "pct_of_base"}}
        },
        "bonus_value": {"value": {"value": "20"}},
        "bonus_currency": {
            "value": {"selected_option": {"value": "AUD"}}
        },
        "bonus_note": {"value": {"value": ""}},
        "rsu_type": {
            "value": {"selected_option": {"value": "private"}}
        },
        "rsu_ticker": {"value": {"value": ""}},
        "rsu_shares_per_year": {"value": {"value": ""}},
        "rsu_amount": {"value": {"value": "120000"}},
        "rsu_currency": {
            "value": {"selected_option": {"value": "AUD"}}
        },
        "rsu_note": {"value": {"value": ""}},
        "other_text": {"value": {"value": ""}},
    }
    base.update(overrides)
    return base


@pytest.fixture
def fx(tmp_path) -> FxClient:
    db = Database(str(tmp_path / "modal.db"))
    return FxClient(db, "token")


@pytest.fixture
def stocks(tmp_path) -> StockClient:
    db = Database(str(tmp_path / "modal_stocks.db"))
    return StockClient(db)


def test_valid_aud_submission(fx: FxClient, stocks: StockClient):
    data, errors = parse_submission(_values())
    assert errors == {}
    assert data is not None
    assert data["base_amount"] == 250000
    assert data["bonus_type"] == "pct_of_base"
    assert data["rsu_type"] == "private"

    full = apply_fx(data, fx, stocks)
    assert full["base_aud"] == 250000
    assert full["bonus_aud"] == 50000
    assert full["rsu_aud"] == 120000


def test_number_parsing_tolerates_symbols(fx: FxClient, stocks: StockClient):
    values = _values(
        base_amount={"value": {"value": "$232,900"}},
        bonus_value={"value": {"value": "20%"}},
        rsu_amount={"value": {"value": "120k"}},
    )
    data, errors = parse_submission(values)
    assert errors == {}
    assert data["base_amount"] == 232900
    assert data["bonus_value"] == 20
    assert data["rsu_amount"] == 120000


def test_missing_base_errors():
    values = _values(base_amount={"value": {"value": ""}})
    data, errors = parse_submission(values)
    assert data is None
    assert "base_amount" in errors


def test_custom_super_requires_pct():
    values = _values(
        super_type={"value": {"selected_option": {"value": "custom_pct"}}},
        super_pct={"value": {"value": ""}},
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "super_pct" in errors


def test_public_requires_ticker():
    values = _values(
        rsu_type={"value": {"selected_option": {"value": "public"}}},
        rsu_ticker={"value": {"value": ""}},
        rsu_shares_per_year={"value": {"value": "500"}},
        rsu_amount={"value": {"value": ""}},
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "rsu_ticker" in errors


def test_public_rejects_bad_market():
    values = _values(
        rsu_type={"value": {"selected_option": {"value": "public"}}},
        rsu_ticker={"value": {"value": "FOO:BAR"}},
        rsu_shares_per_year={"value": {"value": "500"}},
        rsu_amount={"value": {"value": ""}},
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "rsu_ticker" in errors
    assert "NASDAQ" in errors["rsu_ticker"]


def test_public_requires_shares():
    values = _values(
        rsu_type={"value": {"selected_option": {"value": "public"}}},
        rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
        rsu_shares_per_year={"value": {"value": ""}},
        rsu_amount={"value": {"value": ""}},
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "rsu_shares_per_year" in errors


def test_private_requires_amount():
    values = _values(
        rsu_type={"value": {"selected_option": {"value": "private"}}},
        rsu_amount={"value": {"value": ""}},
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "rsu_amount" in errors


def test_public_normalizes_ticker():
    values = _values(
        rsu_type={"value": {"selected_option": {"value": "public"}}},
        rsu_ticker={"value": {"value": "nasdaq:team"}},
        rsu_shares_per_year={"value": {"value": "500"}},
        rsu_amount={"value": {"value": ""}},
    )
    data, errors = parse_submission(values)
    assert errors == {}
    assert data["rsu_ticker"] == "NASDAQ:TEAM"
    assert data["rsu_shares_per_year"] == 500
    assert data["rsu_amount"] is None


def test_public_valuation(
    fx: FxClient, stocks: StockClient, httpx_mock: HTTPXMock, monkeypatch
):
    monkeypatch.setattr(
        "app.http_retry._impersonated_get",
        lambda *a, **k: FakeYahooResponse(
            json_data={
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "regularMarketPrice": 100.0,
                                "currency": "USD",
                                "regularMarketTime": 1720000000,
                            }
                        }
                    ],
                    "error": None,
                }
            }
        ),
    )
    httpx_mock.add_response(
        url=f"{WISE_RATES_URL}?source=USD&target=AUD",
        json=[{"rate": 1.5, "time": "2026-07-26T00:00:00Z"}],
    )
    values = _values(
        bonus_type={"value": {"selected_option": {"value": "none"}}},
        bonus_value={"value": {"value": ""}},
        rsu_type={"value": {"selected_option": {"value": "public"}}},
        rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
        rsu_shares_per_year={"value": {"value": "500"}},
        rsu_amount={"value": {"value": ""}},
    )
    data, errors = parse_submission(values)
    assert errors == {}
    full = apply_fx(data, fx, stocks)
    # 500 shares * $100 = $50,000 USD * 1.5 = A$75,000
    assert full["rsu_share_price"] == 100.0
    assert full["rsu_share_currency"] == "USD"
    assert full["rsu_aud"] == 75000.0


def test_none_rsu_rejects_filled_shares():
    values = _values(
        rsu_type={"value": {"selected_option": {"value": "none"}}},
        rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
        rsu_shares_per_year={"value": {"value": "400"}},
        rsu_amount={"value": {"value": ""}},
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "rsu_ticker" in errors
    assert "rsu_shares_per_year" in errors
    assert "Public" in errors["rsu_shares_per_year"]


def test_none_bonus_rejects_filled_value():
    values = _values(
        bonus_type={"value": {"selected_option": {"value": "none"}}},
        bonus_value={"value": {"value": "20"}},
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "bonus_value" in errors


def test_non_custom_super_rejects_filled_pct():
    values = _values(
        super_type={"value": {"selected_option": {"value": "on_top_legislated"}}},
        super_pct={"value": {"value": "14"}},
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "super_pct" in errors


def test_public_rejects_private_amount():
    values = _values(
        rsu_type={"value": {"selected_option": {"value": "public"}}},
        rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
        rsu_shares_per_year={"value": {"value": "500"}},
        rsu_amount={"value": {"value": "120000"}},
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "rsu_amount" in errors


def test_private_rejects_ticker_and_shares():
    values = _values(
        rsu_type={"value": {"selected_option": {"value": "private"}}},
        rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
        rsu_shares_per_year={"value": {"value": "400"}},
        rsu_amount={"value": {"value": "120000"}},
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "rsu_ticker" in errors
    assert "rsu_shares_per_year" in errors


def test_usd_conversion(fx: FxClient, stocks: StockClient, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{WISE_RATES_URL}?source=USD&target=AUD",
        json=[{"rate": 1.5, "time": "2026-07-26T00:00:00Z"}],
    )
    values = _values(
        base_amount={"value": {"value": "100000"}},
        base_currency={"value": {"selected_option": {"value": "USD"}}},
        bonus_type={"value": {"selected_option": {"value": "none"}}},
        bonus_value={"value": {"value": ""}},
        rsu_type={"value": {"selected_option": {"value": "none"}}},
        rsu_amount={"value": {"value": ""}},
    )
    data, errors = parse_submission(values)
    assert errors == {}
    full = apply_fx(data, fx, stocks)
    assert full["base_aud"] == 150000.0
    assert full["bonus_aud"] is None
    assert full["rsu_aud"] is None


def test_modal_omits_empty_initial_values():
    view = build_disclosure_modal(None)
    for block in view["blocks"]:
        element = block.get("element")
        if element and element["type"] == "plain_text_input":
            assert element.get("initial_value") != ""
