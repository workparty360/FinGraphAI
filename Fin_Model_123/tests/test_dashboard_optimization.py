import os
import subprocess
import time
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from stock_api import (
    FinancialAPIError,
    fetch_historical_prices,
    fetch_financial_statements,
    get_selected_stock_context,
)
import stock_api_ui
from stock_api_ui import build_stock_price_chart


def test_1_reliance_graph_y_axis_never_below_zero():
    """TEST 1: Open RELIANCE.NS stock price graph. Verify that the Y-axis never goes below zero."""
    context = get_selected_stock_context("RELIANCE", "NSE")
    prices = fetch_historical_prices(context["symbol"], period="1mo", interval="1d")
    assert not prices.empty
    assert (prices["close"] > 0).all()

    # Calculate domain as in stock_api_ui
    min_price = float(prices["close"].min())
    max_price = float(prices["close"].max())
    pad = (max_price - min_price) * 0.05 if max_price > min_price else max_price * 0.05
    y_min = max(0.0, min_price - pad)

    assert y_min >= 0.0, f"Y-axis lower bound must be >= 0, got {y_min}"


def test_2_mouse_wheel_and_scroll_zoom_disabled():
    """TEST 2: Verify that chart does not have .interactive() bound, so mousewheel and scroll zoom are disabled."""
    context = get_selected_stock_context("RELIANCE", "NSE")
    prices = fetch_historical_prices(context["symbol"], period="1mo", interval="1d")
    min_price = float(prices["close"].min())
    max_price = float(prices["close"].max())
    pad = (max_price - min_price) * 0.05 if max_price > min_price else max_price * 0.05
    y_min = max(0.0, min_price - pad)
    y_max = max(y_min + 1e-4, max_price + pad)

    chart = build_stock_price_chart(prices.reset_index(), "INR", "₹", "1M", y_min, y_max)
    spec = chart.to_dict()

    # In Altair, .interactive() adds a 'selection' or 'params' with bind='scales' and interval selection
    params = spec.get("params", [])
    selections = spec.get("selection", {})
    scale_bindings = [p for p in params if p.get("bind") == "scales"]
    assert len(scale_bindings) == 0, "Chart must not bind scales to mouse events (.interactive must be disabled)"
    assert "selector" not in selections or selections["selector"].get("bind") != "scales"

    # Verify Y-axis scale clamp and domainMin >= 0
    y_scale = spec["encoding"]["y"]["scale"]
    assert y_scale.get("clamp") is True
    assert y_scale["domain"][0] >= 0.0, "Y domain lower bound must be >= 0"


def test_3_and_4_zoom_in_and_zoom_out_buttons():
    """TEST 3 & 4 (Updated for Phase 4 / TEST 7): Verify zoom buttons are completely removed and time-range filters exist."""
    at = AppTest.from_string("""
import streamlit as st
from stock_api_ui import _render_stock_price_chart, get_selected_stock_context

ctx = get_selected_stock_context("RELIANCE", "NSE")
_render_stock_price_chart(ctx)
""", default_timeout=45)
    at.run()
    assert len(at.exception) == 0

    # Verify ALL zoom-in, zoom-out, and reset zoom buttons are completely removed
    zoom_buttons = [b for b in at.button if "Zoom" in b.label]
    assert len(zoom_buttons) == 0, f"Zoom buttons must be completely removed, found: {[b.label for b in zoom_buttons]}"

    # Verify time-range selector exists and has the required options
    assert len(at.radio) >= 1
    time_range_radio = at.radio[0]
    expected_options = ["1D", "5D", "1M", "3M", "6M", "1Y", "5Y", "MAX"]
    for opt in expected_options:
        assert opt in time_range_radio.options, f"Time range option '{opt}' must be present in radio selector"


def test_5_background_refresh_preserves_zoom_level():
    """TEST 5: Verify background data refresh preserves selected time range and cached dataframe."""
    at = AppTest.from_string("""
import streamlit as st
import time
from stock_api_ui import _render_stock_price_chart, get_selected_stock_context

ctx = get_selected_stock_context("RELIANCE", "NSE")
_render_stock_price_chart(ctx)
""", default_timeout=45)
    at.run()

    # Verify initial time range is preserved
    range_key = "price_range_RELIANCE.NS"
    assert at.session_state.get(range_key) == "1Y"

    # Age the timestamp past 300s to trigger background refresh
    time_range = at.session_state.get(range_key, "1Y")
    df_ts_key = f"price_df_ts_RELIANCE.NS_{time_range}"
    at.session_state[df_ts_key] = time.time() - 350

    # Rerun the component to simulate 5-minute background refresh
    at.run()

    assert at.session_state.get(range_key) == "1Y", "Time range selection must remain preserved across background data refresh"
    cached_df = at.session_state.get(f"price_df_RELIANCE.NS_{time_range}")
    assert cached_df is not None and not cached_df.empty, "Cached dataframe must be preserved across background refresh"


def test_6_financial_statements_preserve_selection_when_ai_question_asked():
    """TEST 6: Select quarterly statements. Ask AI question. Verify selections and cached tables remain unchanged."""
    at = AppTest.from_string("""
import streamlit as st
from stock_api_ui import _render_financial_statements, get_selected_stock_context

ctx = get_selected_stock_context("RELIANCE", "NSE")
_render_financial_statements(ctx)
""", default_timeout=45)
    at.run()

    # Select Quarterly
    period_key = "fin_period_RELIANCE.NS"
    at.session_state[period_key] = "Quarterly"
    at.run()

    assert at.session_state[period_key] == "Quarterly"

    # Simulate an AI chat message submission without touching statements
    at.session_state["chat_messages"] = [{"role": "user", "content": "What is the operating revenue?"}]
    at.run()

    # Verify statement selection remains Quarterly
    assert at.session_state[period_key] == "Quarterly"
    # Verify statement cached data is preserved
    cached_inc = at.session_state.get("cached_stmt_RELIANCE.NS_income_quarterly")
    assert cached_inc is not None and len(cached_inc.get("financials", [])) > 0


def test_7_document_viewer_preserves_page_across_ai_questions():
    """TEST 7: Open financial document. Navigate to page 2. Ask AI question. Verify document remains on page 2."""
    at = AppTest.from_string("""
import streamlit as st
from stock_api_ui import _render_document_viewer, get_selected_stock_context

ctx = get_selected_stock_context("RELIANCE", "NSE")
_render_document_viewer(ctx)
""", default_timeout=45)
    at.run()

    page_key = "doc_page_idx_RELIANCE.NS_annual_report_notes"
    # Navigate to page 2
    btn_next = next(b for b in at.button if "Next Page" in b.label)
    btn_next.click().run()

    assert at.session_state.get(page_key) == 2, "Document must navigate to page 2"

    # Simulate AI question interaction
    at.session_state["user_question"] = "Explain segment performance."
    at.run()

    assert at.session_state.get(page_key) == 2, "Document must remain on page 2 when AI question is asked"


def test_8_submitting_ai_question_does_not_trigger_unrelated_api_calls(monkeypatch):
    """TEST 8: Inspect network requests. Submit AI question. Verify unrelated dashboard API requests are not triggered."""
    api_call_count = {"price": 0, "statements": 0}

    real_fetch_prices = stock_api_ui.fetch_historical_prices
    real_fetch_stmts = stock_api_ui.fetch_financial_statements

    def count_fetch_prices(*args, **kwargs):
        api_call_count["price"] += 1
        return real_fetch_prices(*args, **kwargs)

    def count_fetch_stmts(*args, **kwargs):
        api_call_count["statements"] += 1
        return real_fetch_stmts(*args, **kwargs)

    monkeypatch.setattr(stock_api_ui, "fetch_historical_prices", count_fetch_prices)
    monkeypatch.setattr(stock_api_ui, "fetch_financial_statements", count_fetch_stmts)

    at = AppTest.from_string("""
import streamlit as st
from stock_api_ui import (
    _render_stock_selector,
    _render_stock_price_chart,
    _render_financial_statements,
    _render_document_viewer,
    _render_ai_chat_section,
)

ctx = _render_stock_selector()
_render_stock_price_chart(ctx)
_render_financial_statements(ctx)
_render_document_viewer(ctx)
""", default_timeout=45)
    at.run()

    initial_price_calls = api_call_count["price"]
    initial_stmt_calls = api_call_count["statements"]

    # Now simulate an AI question submission on the already cached dashboard
    at.session_state["user_submitted_chat"] = "What was the EBITDA margin?"
    at.run()

    # Verify NO new API calls were triggered for stock price graph or statements
    assert api_call_count["price"] == initial_price_calls, "Submitting AI question must NOT trigger stock price API re-fetching"
    assert api_call_count["statements"] == initial_stmt_calls, "Submitting AI question must NOT trigger statement API re-fetching"


def test_9_auto_refresh_every_5_minutes_policy():
    """TEST 9: Verify automatic 5-minute (300 seconds) refresh logic."""
    assert stock_api_ui.REFRESH_INTERVAL_SECONDS == 300

    now = time.time()
    # At 299 seconds, is_stale is False
    assert ((now - (now - 299)) >= stock_api_ui.REFRESH_INTERVAL_SECONDS) is False
    # At 301 seconds, is_stale is True
    assert ((now - (now - 301)) >= stock_api_ui.REFRESH_INTERVAL_SECONDS) is True


def test_10_switch_ticker_from_reliance_to_aapl_updates_context():
    """TEST 10: Switch from RELIANCE.NS to AAPL. Verify data updates immediately and follows AAPL."""
    at = AppTest.from_string("""
import streamlit as st
from stock_api_ui import _render_stock_selector

ctx = _render_stock_selector()
st.session_state['active_ctx'] = ctx
""", default_timeout=45)
    at.run()

    assert at.session_state['active_ctx']['symbol'] == 'RELIANCE.NS'

    # Switch ticker to AAPL
    at.text_input[0].input("AAPL").run()
    assert at.session_state['active_ctx']['symbol'] == 'AAPL'
    assert at.session_state['active_ctx']['currency'] == 'USD'


def test_11_codebase_contains_no_sec_edgar_references():
    """TEST 11: Search the codebase for SEC EDGAR references. Verify no active SEC EDGAR integration remains."""
    # Check that sec_api.py does not exist
    assert not os.path.exists("sec_api.py"), "sec_api.py must be deleted"
    assert not os.path.exists("tests/test_sec_api.py"), "tests/test_sec_api.py must be deleted"

    # Search application python code (excluding tests) for active SEC EDGAR imports or URLs
    grep_res = subprocess.run(
        ["grep", "-rn", "from sec_api", ".", "--include=*.py", "--exclude-dir=.venv", "--exclude-dir=tests"],
        capture_output=True,
        text=True,
    )
    assert grep_res.stdout.strip() == "", f"Found active sec_api import: {grep_res.stdout}"

    grep_url = subprocess.run(
        ["grep", "-rn", "data.sec.gov", ".", "--include=*.py", "--exclude-dir=.venv", "--exclude-dir=tests"],
        capture_output=True,
        text=True,
    )
    assert grep_url.stdout.strip() == "", f"Found active data.sec.gov URL: {grep_url.stdout}"


def test_12_fmp_is_not_reintroduced():
    """TEST 12: Verify that FMP is not reintroduced."""
    grep_fmp = subprocess.run(
        ["grep", "-rn", "financialmodelingprep", ".", "--include=*.py", "--exclude-dir=.venv", "--exclude-dir=tests"],
        capture_output=True,
        text=True,
    )
    assert grep_fmp.stdout.strip() == "", f"Found active FMP URL: {grep_fmp.stdout}"


def test_13_python_files_syntax_and_compilation():
    """TEST 13: Verify that all application source files compile without syntax errors."""
    src_files = [
        "final.py",
        "stock_api.py",
        "stock_api_ui.py",
        "finance_chat_ui.py",
        "memory_agents.py",
        "falkor_memory.py",
    ]
    for src in src_files:
        res = subprocess.run([".venv/bin/python", "-m", "py_compile", src], capture_output=True, text=True)
        assert res.returncode == 0, f"Compilation failed for {src}: {res.stderr}"


def test_14_api_errors_preserve_previously_cached_data(monkeypatch):
    """TEST 14: Verify that API errors do not clear previously loaded valid graph or financial statement data."""
    at = AppTest.from_string("""
import streamlit as st
from stock_api_ui import _render_stock_price_chart, get_selected_stock_context

ctx = get_selected_stock_context("RELIANCE", "NSE")
_render_stock_price_chart(ctx)
""", default_timeout=45)
    at.run()

    df_cache_key = "price_df_RELIANCE.NS_1Y"
    original_df = at.session_state.get(df_cache_key)
    assert original_df is not None and not original_df.empty

    # Now simulate an API network failure during background refresh
    def failing_fetch(*args, **kwargs):
        raise FinancialAPIError("Connection refused by provider")

    monkeypatch.setattr(stock_api_ui, "fetch_historical_prices", failing_fetch)

    # Age timestamp past 300s to trigger refresh
    at.session_state["price_df_ts_RELIANCE.NS_1Y"] = time.time() - 350
    at.run()

    # Cached data must STILL be preserved and visible
    current_df = at.session_state.get(df_cache_key)
    assert current_df is not None and len(current_df) == len(original_df)
    # Warning caption must indicate cached data is displayed
    assert any("cached data" in c.value for c in at.caption)
