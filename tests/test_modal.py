from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from app.db import Database
from app.fx import FRANKFURTER_URL, FxClient
from app.modal import MAX_GRANTS, apply_fx, build_disclosure_modal, parse_submission
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


def _empty_grant_values(index: int) -> dict[str, Any]:
    return {
        f"grant{index}_rsu_type": {
            "value": {"selected_option": {"value": "none"}}
        },
        f"grant{index}_equity_kind": {
            "value": {"selected_option": {"value": "rsu"}}
        },
        f"grant{index}_rsu_ticker": {"value": {"value": ""}},
        f"grant{index}_rsu_shares_per_year": {"value": {"value": ""}},
        f"grant{index}_strike_price": {"value": {"value": ""}},
        f"grant{index}_rsu_amount": {"value": {"value": ""}},
        f"grant{index}_rsu_currency": {
            "value": {"selected_option": {"value": "AUD"}}
        },
        f"grant{index}_rsu_note": {"value": {"value": ""}},
    }


def _grant_values(index: int, **overrides) -> dict[str, Any]:
    base = _empty_grant_values(index)
    for key, value in overrides.items():
        base[f"grant{index}_{key}"] = value
    return base


def _values(*, grant1: dict[str, Any] | None = None, **overrides):
    base = {
        "fy_period": {"value": {"value": "1 Jul 2026 – 30 Jun 2027"}},
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
        "other_text": {"value": {"value": ""}},
    }
    for i in range(1, MAX_GRANTS + 1):
        base.update(_empty_grant_values(i))
    if grant1:
        base.update(grant1)
    base.update(overrides)
    return base


@pytest.fixture
def fx(tmp_path) -> FxClient:
    db = Database(str(tmp_path / "modal.db"))
    return FxClient(db)


@pytest.fixture
def stocks(tmp_path) -> StockClient:
    db = Database(str(tmp_path / "modal_stocks.db"))
    return StockClient(db)


def test_valid_aud_submission(fx: FxClient, stocks: StockClient):
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "private"}}},
            rsu_amount={"value": {"value": "120000"}},
        )
    )
    data, errors = parse_submission(values)
    assert errors == {}
    assert data is not None
    assert data["base_amount"] == 250000
    assert data["bonus_type"] == "pct_of_base"
    assert len(data["grants"]) == 1
    assert data["grants"][0]["rsu_type"] == "private"

    full = apply_fx(data, fx, stocks)
    assert full["base_aud"] == 250000
    assert full["bonus_aud"] == 50000
    assert full["grants"][0]["rsu_aud"] == 120000


def test_number_parsing_tolerates_symbols(fx: FxClient, stocks: StockClient):
    values = _values(
        base_amount={"value": {"value": "$232,900"}},
        bonus_value={"value": {"value": "20%"}},
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "private"}}},
            rsu_amount={"value": {"value": "120k"}},
        ),
    )
    data, errors = parse_submission(values)
    assert errors == {}
    assert data["base_amount"] == 232900
    assert data["bonus_value"] == 20
    assert data["grants"][0]["rsu_amount"] == 120000


def test_missing_base_errors():
    values = _values(base_amount={"value": {"value": ""}})
    data, errors = parse_submission(values)
    assert data is None
    assert "base_amount" in errors


def test_missing_fy_period_errors():
    values = _values(fy_period={"value": {"value": ""}})
    data, errors = parse_submission(values)
    assert data is None
    assert "fy_period" in errors


def test_fy_period_passed_through():
    values = _values(fy_period={"value": {"value": "1 Jan 2026 – 31 Dec 2026"}})
    data, errors = parse_submission(values)
    assert errors == {}
    assert data["fy_period"] == "1 Jan 2026 – 31 Dec 2026"


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
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            rsu_ticker={"value": {"value": ""}},
            rsu_shares_per_year={"value": {"value": "500"}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_rsu_ticker" in errors


def test_public_rejects_bad_market():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            rsu_ticker={"value": {"value": "FOO:BAR"}},
            rsu_shares_per_year={"value": {"value": "500"}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_rsu_ticker" in errors
    assert "NASDAQ" in errors["grant1_rsu_ticker"]


def test_public_requires_shares():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": ""}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_rsu_shares_per_year" in errors


def test_private_requires_amount():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "private"}}},
            rsu_amount={"value": {"value": ""}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_rsu_amount" in errors


def test_public_normalizes_ticker():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            rsu_ticker={"value": {"value": "nasdaq:team"}},
            rsu_shares_per_year={"value": {"value": "500"}},
        )
    )
    data, errors = parse_submission(values)
    assert errors == {}
    assert data["grants"][0]["rsu_ticker"] == "NASDAQ:TEAM"
    assert data["grants"][0]["rsu_shares_per_year"] == 500
    assert data["grants"][0]["rsu_amount"] is None


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
        url=f"{FRANKFURTER_URL}?base=USD&symbols=AUD",
        json={"amount": 1.0, "base": "USD", "date": "2026-07-26", "rates": {"AUD": 1.5}},
    )
    values = _values(
        bonus_type={"value": {"selected_option": {"value": "none"}}},
        bonus_value={"value": {"value": ""}},
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": "500"}},
        ),
    )
    data, errors = parse_submission(values)
    assert errors == {}
    full = apply_fx(data, fx, stocks)
    # 500 shares * $100 = $50,000 USD * 1.5 = A$75,000
    grant = full["grants"][0]
    assert grant["rsu_share_price"] == 100.0
    assert grant["rsu_share_currency"] == "USD"
    assert grant["rsu_aud"] == 75000.0


def test_public_options_requires_strike_price():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            equity_kind={"value": {"selected_option": {"value": "options"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": "500"}},
            strike_price={"value": {"value": ""}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_strike_price" in errors


def test_public_rsu_rejects_strike_price():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            equity_kind={"value": {"selected_option": {"value": "rsu"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": "500"}},
            strike_price={"value": {"value": "45"}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_strike_price" in errors
    assert "RSU" in errors["grant1_strike_price"]


def test_private_rejects_strike_price():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "private"}}},
            equity_kind={"value": {"selected_option": {"value": "options"}}},
            rsu_amount={"value": {"value": "120000"}},
            strike_price={"value": {"value": "45"}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_strike_price" in errors


def test_none_rejects_filled_strike_price():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "none"}}},
            strike_price={"value": {"value": "45"}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_strike_price" in errors


def test_public_options_parses_correctly():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            equity_kind={"value": {"selected_option": {"value": "options"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": "1000"}},
            strike_price={"value": {"value": "45"}},
        )
    )
    data, errors = parse_submission(values)
    assert errors == {}
    grant = data["grants"][0]
    assert grant["equity_kind"] == "options"
    assert grant["rsu_strike_price"] == 45.0


def test_grant_year_start_parses_correctly():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "private"}}},
            rsu_amount={"value": {"value": "20000"}},
            grant_year_start={"value": {"value": "2022"}},
        )
    )
    data, errors = parse_submission(values)
    assert errors == {}
    assert data["grants"][0]["grant_year_start"] == 2022


def test_grant_year_start_rejects_implausible_year():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "private"}}},
            rsu_amount={"value": {"value": "20000"}},
            grant_year_start={"value": {"value": "1899"}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_grant_year_start" in errors


def test_grant_year_start_rejected_when_type_none():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "none"}}},
            grant_year_start={"value": {"value": "2022"}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_grant_year_start" in errors


def test_public_options_valuation_is_spread_over_strike(
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
                                "regularMarketPrice": 120.0,
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
        url=f"{FRANKFURTER_URL}?base=USD&symbols=AUD",
        json={"amount": 1.0, "base": "USD", "date": "2026-07-26", "rates": {"AUD": 1.5}},
    )
    values = _values(
        bonus_type={"value": {"selected_option": {"value": "none"}}},
        bonus_value={"value": {"value": ""}},
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            equity_kind={"value": {"selected_option": {"value": "options"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": "1000"}},
            strike_price={"value": {"value": "45"}},
        ),
    )
    data, errors = parse_submission(values)
    assert errors == {}
    full = apply_fx(data, fx, stocks)
    grant = full["grants"][0]
    # spread = $120 - $45 = $75; 1000 * $75 = $75,000 USD * 1.5 = A$112,500
    # (market price $120 is still recorded, unlike the strike, which isn't converted)
    assert grant["rsu_share_price"] == 120.0
    assert grant["rsu_strike_price"] == 45.0
    assert grant["rsu_aud"] == 112500.0


def test_public_options_underwater_floors_at_zero(
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
                                "regularMarketPrice": 30.0,
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
        url=f"{FRANKFURTER_URL}?base=USD&symbols=AUD",
        json={"amount": 1.0, "base": "USD", "date": "2026-07-26", "rates": {"AUD": 1.5}},
    )
    values = _values(
        bonus_type={"value": {"selected_option": {"value": "none"}}},
        bonus_value={"value": {"value": ""}},
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            equity_kind={"value": {"selected_option": {"value": "options"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": "1000"}},
            strike_price={"value": {"value": "45"}},
        ),
    )
    data, errors = parse_submission(values)
    assert errors == {}
    full = apply_fx(data, fx, stocks)
    # strike ($45) above market price ($30) — underwater options are worth $0, not negative
    assert full["grants"][0]["rsu_aud"] == 0.0


def test_multiple_concurrent_grants(
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
        url=f"{FRANKFURTER_URL}?base=USD&symbols=AUD",
        json={"amount": 1.0, "base": "USD", "date": "2026-07-26", "rates": {"AUD": 1.5}},
    )
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": "500"}},
            rsu_note={"value": {"value": "new-hire grant"}},
        ),
    )
    values.update(
        _grant_values(
            2,
            rsu_type={"value": {"selected_option": {"value": "private"}}},
            rsu_amount={"value": {"value": "20000"}},
            rsu_note={"value": {"value": "2024 top-up"}},
        )
    )
    data, errors = parse_submission(values)
    assert errors == {}
    assert len(data["grants"]) == 2

    full = apply_fx(data, fx, stocks)
    assert len(full["grants"]) == 2
    grant1, grant2 = full["grants"]
    assert grant1["rsu_type"] == "public"
    assert grant1["rsu_aud"] == 75000.0
    assert grant1["rsu_note"] == "new-hire grant"
    assert grant2["rsu_type"] == "private"
    assert grant2["rsu_aud"] == 20000
    assert grant2["rsu_note"] == "2024 top-up"


def test_none_rsu_rejects_filled_shares():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "none"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": "400"}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_rsu_ticker" in errors
    assert "grant1_rsu_shares_per_year" in errors
    assert "Public" in errors["grant1_rsu_shares_per_year"]


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
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "public"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": "500"}},
            rsu_amount={"value": {"value": "120000"}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_rsu_amount" in errors


def test_private_rejects_ticker_and_shares():
    values = _values(
        grant1=_grant_values(
            1,
            rsu_type={"value": {"selected_option": {"value": "private"}}},
            rsu_ticker={"value": {"value": "NASDAQ:TEAM"}},
            rsu_shares_per_year={"value": {"value": "400"}},
            rsu_amount={"value": {"value": "120000"}},
        )
    )
    data, errors = parse_submission(values)
    assert data is None
    assert "grant1_rsu_ticker" in errors
    assert "grant1_rsu_shares_per_year" in errors


def test_usd_conversion(fx: FxClient, stocks: StockClient, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{FRANKFURTER_URL}?base=USD&symbols=AUD",
        json={"amount": 1.0, "base": "USD", "date": "2026-07-26", "rates": {"AUD": 1.5}},
    )
    values = _values(
        base_amount={"value": {"value": "100000"}},
        base_currency={"value": {"selected_option": {"value": "USD"}}},
        bonus_type={"value": {"selected_option": {"value": "none"}}},
        bonus_value={"value": {"value": ""}},
    )
    data, errors = parse_submission(values)
    assert errors == {}
    full = apply_fx(data, fx, stocks)
    assert full["base_aud"] == 150000.0
    assert full["bonus_aud"] is None
    assert full["grants"] == []


def test_modal_omits_empty_initial_values():
    view = build_disclosure_modal(None)
    for block in view["blocks"]:
        element = block.get("element")
        if element and element["type"] == "plain_text_input":
            assert element.get("initial_value") != ""


def test_modal_has_slot_for_each_grant():
    view = build_disclosure_modal(None)
    block_ids = {b["block_id"] for b in view["blocks"] if "block_id" in b}
    for i in range(1, MAX_GRANTS + 1):
        assert f"grant{i}_rsu_type" in block_ids
