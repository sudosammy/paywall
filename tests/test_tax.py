import pytest

from app.tax import estimate_tax_aud, marginal_rate


def test_marginal_rate_at_each_bracket():
    assert marginal_rate(10000) == pytest.approx(0.02)  # 0% + 2% medicare
    assert marginal_rate(30000) == pytest.approx(0.17)  # 15% + 2%
    assert marginal_rate(100000) == pytest.approx(0.32)  # 30% + 2%
    assert marginal_rate(150000) == pytest.approx(0.39)  # 37% + 2%
    assert marginal_rate(250000) == pytest.approx(0.47)  # 45% + 2%


def test_estimate_tax_aud_at_bracket_boundaries():
    # 15% * (45,000 - 18,200) = 4,020; + 2% medicare on 45,000 = 900
    assert estimate_tax_aud(45000) == pytest.approx(4920)
    # 4,020 + 30% * (135,000 - 45,000) = 31,020; + 2% medicare on 135,000 = 2,700
    assert estimate_tax_aud(135000) == pytest.approx(33720)
    # 31,020 + 37% * (190,000 - 135,000) = 51,370; + 2% medicare on 190,000 = 3,800
    assert estimate_tax_aud(190000) == pytest.approx(55170)


def test_estimate_tax_aud_top_bracket():
    # 51,370 + 45% * (250,000 - 190,000) = 78,370; + 2% medicare on 250,000 = 5,000
    assert estimate_tax_aud(250000) == pytest.approx(83370)


def test_estimate_tax_aud_zero_or_negative():
    assert estimate_tax_aud(0) == 0
    assert estimate_tax_aud(-500) == 0
