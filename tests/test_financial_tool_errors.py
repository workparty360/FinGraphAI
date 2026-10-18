"""Stock tools preserve their JSON contract on success and failure."""

import json
import logging
from datetime import date

import pytest

import memory_agents

TOOLS = [
    ("tool_stock_price", "get_stock_price", {}),
    ("tool_historical_prices", "get_historical_prices", {"range": "1mo"}),
    ("tool_income_statement", "get_income_statement", {"period": "annual"}),
    ("tool_balance_sheet", "get_balance_sheet", {"period": "annual"}),
    ("tool_cash_flow", "get_cash_flow_statement", {"period": "annual"}),
    ("tool_cash_flow_statement", "get_cash_flow_statement", {"period": "annual"}),
    ("tool_company_overview", "get_company_overview", {}),
]


@pytest.mark.parametrize("tool_name,fetch_name,expected_options", TOOLS)
@pytest.mark.parametrize("failure", [None, "resolution", "fetch"])
def test_stock_tool_json_contract(monkeypatch, caplog, tool_name, fetch_name, expected_options, failure):
    calls = []

    def resolve(symbol):
        assert symbol == "Apple"
        if failure == "resolution":
            raise ValueError("Ticker resolution failed")
        return "AAPL"

    def fetch(symbol, **options):
        calls.append((symbol, options))
        if failure == "fetch":
            raise RuntimeError("Provider unavailable")
        return {"symbol": symbol, "reported_at": date(2024, 1, 1), "value": None}

    monkeypatch.setattr(memory_agents, "resolve_ticker", resolve)
    monkeypatch.setattr(memory_agents, fetch_name, fetch)
    with caplog.at_level(logging.WARNING, logger=memory_agents.__name__):
        result = json.loads(getattr(memory_agents, tool_name).invoke({"symbol": "Apple"}))

    assert calls == ([] if failure == "resolution" else [("AAPL", expected_options)])
    if failure:
        error = "Ticker resolution failed" if failure == "resolution" else "Provider unavailable"
        assert result == {"error": error, "symbol": "Apple"}
        assert f"{tool_name} error for Apple: {error}" in caplog.text
    else:
        assert result == {"symbol": "AAPL", "reported_at": "2024-01-01", "value": None}


@pytest.mark.parametrize("tool_name,fetch_name,expected_options", TOOLS)
def test_stock_tool_preserves_explicit_options(monkeypatch, tool_name, fetch_name, expected_options):
    calls = []
    options = {key: "1y" if key == "range" else "quarterly" for key in expected_options}

    def fetch(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return {"symbol": symbol}

    monkeypatch.setattr(memory_agents, "resolve_ticker", lambda symbol: "AAPL")
    monkeypatch.setattr(memory_agents, fetch_name, fetch)
    result = getattr(memory_agents, tool_name).invoke({"symbol": "Apple", **options})
    assert json.loads(result) == {"symbol": "AAPL"}
    assert calls == [("AAPL", options)]
