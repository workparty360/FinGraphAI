"""Offline regression tests for the ticker-based statements API."""
from datetime import date

import pytest
import requests

from stock_api import (FinancialAPIError, calculate_ratios,
                       fetch_financial_statements, fetch_historical_prices,
                       fetch_stock_statements, parse_statements, validate_symbol)


def balance(yr, **kw):
    return dict(date=f"{yr}-03-31", fiscalYear=str(yr), reportedCurrency="INR",
                totalAssets=1000 + yr - 2023, totalCurrentLiabilities=200,
                totalLiabilities=500, totalStockholdersEquity=500,
                totalDebt=100, commonStock=100, retainedEarnings=200,
                propertyPlantEquipmentNet=300, cashAndCashEquivalents=40,
                totalInvestments=20, **kw)


def income(yr, rev, net, operating=None, **kw):
    return dict(date=f"{yr}-03-31", fiscalYear=str(yr), reportedCurrency="INR",
                revenue=rev, netIncome=net,
                operatingIncome=operating if operating is not None else net * 2,
                **kw)


def quarter(yr, name, dt, rev, **kw):
    return dict(date=dt, fiscalYear=str(yr), period=name,
                reportedCurrency="INR", revenue=rev, operatingIncome=rev / 5,
                netIncome=rev / 10, **kw)


@pytest.fixture()
def stocks():
    return parse_statements(
        "RELIANCE.NS",
        [balance(2023), balance(2024), balance(2025)],
        [income(2023, 100, 10), income(2024, 110, 11), income(2025, 132, 22)],
        [quarter(2024, "Q1", "2023-06-30", 40),
         quarter(2025, "Q1", "2024-06-30", 50),
         quarter(2025, "Q3", "2024-12-31", 30)],
    )


def test_symbol_allowlist():
    assert validate_symbol(" reliance.ns ") == "RELIANCE.NS"
    assert validate_symbol("AAPL") == "AAPL"
    for text in ("../bad/secret", "", "TCS?key=1", "TCS&apikey=secret"):
        with pytest.raises(FinancialAPIError):
            validate_symbol(text)


def test_parsing_fiscal_years_is_sorted_and_missing_not_zero(stocks):
    assert stocks.currency == "INR"
    assert list(stocks.annual_balance.index) == [2023, 2024, 2025]
    assert list(stocks.annual_income.index) == [2023, 2024, 2025]
    assert stocks.annual_balance["Capital work in progress"].isna().all()
    assert any("CWIP" in w for w in stocks.warnings)
    assert list(stocks.quarterly_income["Fiscal quarter"]) == ["Q1", "Q1", "Q3"]


def test_ratios_are_correct_and_quarter_yoy_handles_gaps(stocks):
    result = calculate_ratios(stocks)
    annual = result["Annual profitability & leverage"]
    assert annual.loc[2025, "Operating margin (%)"] == pytest.approx(100 * 44 / 132)
    assert annual.loc[2025, "Debt / equity (x)"] == pytest.approx(0.2)
    growth = result["Annual growth"]
    assert growth.loc[2025, "Revenue YoY (%)"] == pytest.approx(20)
    yoy = result["Quarterly YoY growth"]
    assert yoy.loc["2024-06-30", "Revenue YoY (%)"] == pytest.approx(25)
    assert yoy.loc["2024-12-31", "Revenue YoY (%)"] != yoy.loc["2024-12-31", "Revenue YoY (%)"]  # NaN (no Q3 last year)


def test_missing_debt_and_equity_ratios_not_misleading():
    bs = balance(2023); bs.pop("totalDebt"); bs.pop("totalStockholdersEquity")
    bs2 = balance(2024); bs2.pop("totalDebt"); bs2.pop("totalStockholdersEquity")
    bs.pop("totalLiabilities"); bs2.pop("totalLiabilities")
    st = parse_statements("AAPL", [bs, bs2],
                         [income(2023, 50, 5), income(2024, 80, 8)],
                         [quarter(2024, "Q1", "2024-01-31", 15)])
    assert st.annual_balance["Borrowings"].isna().all()
    assert st.annual_balance["Shareholders equity"].isna().all()
    assert calculate_ratios(st)["Annual profitability & leverage"]["Debt / equity (x)"].isna().all()


def test_reject_currency_mismatch_and_year_mismatch():
    with pytest.raises(FinancialAPIError, match="mixed reporting currencies"):
        parse_statements("AAPL", [balance(2023), balance(2024)],
                         [dict(income(2023, 10, 1), reportedCurrency="USD"), income(2024, 20, 2)],
                         [quarter(2024, "Q1", "2024-03-31", 20)])
    with pytest.raises(FinancialAPIError, match="No matching"):
        parse_statements("AAPL", [balance(2023), balance(2024)],
                         [income(2025, 10, 1)],
                         [quarter(2025, "Q1", "2025-03-31", 20)])


class FakeResponse:
    def __init__(self, data, status=200):
        self.data = data
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("Error", response=self)
    def json(self):
        return self.data


class FakeSession:
    def __init__(self, rows, status=200):
        self.calls = []
        self.rows = rows
        self.status = status
    def get(self, url, params, headers, timeout):
        self.calls.append((url, params, headers, timeout))
        if "balance-sheet" in url:
            data = self.rows["balance"]
        elif params["period"] == "annual":
            data = self.rows["income"]
        else:
            data = self.rows["quarter"]
        return FakeResponse(data, self.status)


def test_fetch_calls_three_endpoints_and_keeps_key_in_header():
    fake = FakeSession({
        "balance": [balance(2023), balance(2024)],
        "income": [income(2023, 50, 5), income(2024, 70, 7)],
        "quarter": [quarter(2024, "Q1", "2024-01-31", 15)],
    })
    stock = fetch_stock_statements("RELIANCE.NS", "abc-secret", session=fake)
    assert stock.symbol == "RELIANCE.NS"
    assert len(fake.calls) == 3
    for url, params, headers, timeout in fake.calls:
        assert "abc-secret" not in url
        assert "abc-secret" not in str(params)
        assert headers["apikey"] == "abc-secret"
        assert params["symbol"] == "RELIANCE.NS"
        assert timeout == 25
    assert "apikey" not in stock.source_urls["annual_balance"]


def test_fetch_http_failures_have_actionable_errors():
    fake = FakeSession({"balance": [], "income": [], "quarter": []}, status=402)
    with pytest.raises(FinancialAPIError, match="rejected"):
        fetch_stock_statements("AAPL", "bad-key", session=fake)
    with pytest.raises(FinancialAPIError, match="API key is missing"):
        fetch_stock_statements("AAPL", "", session=fake)


def test_empty_results_are_rejected():
    fake = FakeSession({"balance": [], "income": [], "quarter": []})
    with pytest.raises(FinancialAPIError, match="No records"):
        fetch_stock_statements("AAPL", "key", session=fake)


def test_fetch_historical_prices_validation():
    with pytest.raises(FinancialAPIError, match="Enter a valid ticker"):
        fetch_historical_prices("BAD$$$TICKER")


def test_fetch_financial_statements_validation():
    with pytest.raises(FinancialAPIError, match="Invalid statement/period"):
        fetch_financial_statements("AAPL", statement="invalid_stmt", period="annual")
    with pytest.raises(FinancialAPIError, match="Invalid statement/period"):
        fetch_financial_statements("AAPL", statement="income", period="invalid_period")
    with pytest.raises(FinancialAPIError, match="Enter a valid ticker"):
        fetch_financial_statements("BAD;DROP")

