from streamlit.testing.v1 import AppTest


def test_stock_api_ui_renders_chart_and_statements():
    """Verify that the Stock Price chart and Financial Statements render in Streamlit."""
    at = AppTest.from_string("""
import streamlit as st
from stock_api_ui import _render_stock_selector, _render_stock_price_chart, _render_financial_statements

selected = _render_stock_selector()
_render_stock_price_chart(selected)
_render_financial_statements(selected)
""", default_timeout=45)
    at.run()
    assert len(at.exception) == 0, f"UI raised exceptions: {[e.value for e in at.exception]}"
    subheaders = [s.value for s in at.subheader]
    assert "Selected Stock Context" in subheaders
    assert "Stock Price" in subheaders
    assert "Financial Statements" in subheaders
    # Verify dataframes rendered for the active statement tab
    assert len(at.dataframe) >= 1
    # Verify captions
    captions = [c.value for c in at.caption]
    assert any("Yahoo Finance" in c for c in captions)
