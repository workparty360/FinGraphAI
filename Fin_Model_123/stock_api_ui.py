from __future__ import annotations

import datetime as _dt
import json
import os
import time

import altair as alt
from langchain_core.documents import Document
import pandas as pd
import streamlit as st

from finance_chat_ui import render_agent_chat
from stock_api import (
    FinancialAPIError,
    fetch_financial_statements,
    fetch_historical_prices,
    get_company_overview,
    get_historical_prices,
    get_selected_stock_context,
    get_stock_price,
)


REFRESH_INTERVAL_SECONDS = 300  # 5 minutes auto-refresh


def _format_value(value: float | None, currency: str, unit: str = "Auto") -> str:
    """Format numerical values with currency symbols and appropriate magnitude."""
    if value is None:
        return "N/A"
    symbol = "₹" if currency == "INR" else "$"
    abs_val = abs(value)

    if currency == "INR":
        if unit == "Crore":
            return f"{symbol}{value / 1e7:,.2f} Cr"
        elif unit == "Lakh":
            return f"{symbol}{value / 1e5:,.2f} L"
        elif unit == "Billion":
            return f"{symbol}{value / 1e9:,.2f} B"
        elif unit == "Raw":
            return f"{symbol}{value:,.2f}"
        else:  # Auto
            if abs_val >= 1e7:
                return f"{symbol}{value / 1e7:,.2f} Cr"
            elif abs_val >= 1e5:
                return f"{symbol}{value / 1e5:,.2f} L"
            else:
                return f"{symbol}{value:,.2f}"
    else:
        if unit == "Billion":
            return f"{symbol}{value / 1e9:,.2f}B"
        elif unit == "Million":
            return f"{symbol}{value / 1e6:,.2f}M"
        elif unit == "Raw":
            return f"{symbol}{value:,.2f}"
        else:  # Auto
            if abs_val >= 1e9:
                return f"{symbol}{value / 1e9:,.2f}B"
            elif abs_val >= 1e6:
                return f"{symbol}{value / 1e6:,.2f}M"
            else:
                return f"{symbol}{value:,.2f}"


def _docs_from_financial_statements(stock_context: dict) -> list[Document]:
    """Generate structured LangChain Document objects from ALL available data for the selected stock.

    Indexes into FalkorDB:
    - Company profile & sector overview
    - Live price, day range, market cap
    - Key valuation metrics (P/E, EPS, dividend yield, 52-week range, beta)
    - 1-year and 1-month historical price summary
    - Annual & quarterly income statements
    - Annual & quarterly balance sheets
    - Annual & quarterly cash flow statements
    """
    symbol = stock_context["symbol"]
    currency = stock_context["currency"]
    company = stock_context["companyName"]
    exchange = stock_context["exchange"]
    docs: list[Document] = []
    intro = f"Symbol: {symbol}; Company: {company}; Exchange: {exchange}; Currency: {currency}. "

    # ── 1. Company Profile & Sector Overview ────────────────────────────────
    try:
        overview = get_company_overview(symbol)
        overview_text = (
            f"{intro}Company Profile & Sector Overview: "
            f"Sector: {overview.get('sector')}; "
            f"Industry: {overview.get('industry')}; "
            f"Market Cap: {overview.get('marketCap')}; "
            f"Trailing P/E: {overview.get('peRatio')}; "
            f"Forward P/E: {overview.get('forwardPE')}; "
            f"Dividend Yield: {overview.get('dividendYield')}; "
            f"Summary: {overview.get('summary')}"
        )
        docs.append(Document(
            page_content=overview_text,
            metadata={"source": "financial_company_overview", "symbol": symbol, "period": "current"}
        ))
    except Exception:
        pass

    # ── 2. Live Price & Day Range ────────────────────────────────────────────
    try:
        price_data = get_stock_price(symbol)
        price = price_data.get("price")
        prev = price_data.get("previousClose")
        change = price_data.get("change")
        change_pct = price_data.get("changePercent")
        day_high = price_data.get("dayHigh")
        day_low = price_data.get("dayLow")
        mktcap = price_data.get("marketCap")

        curr_sym = "₹" if currency == "INR" else "$"

        def _fmt(v):
            if v is None:
                return "N/A"
            if mktcap and v == mktcap:
                return f"{curr_sym}{v / 1e9:,.2f}B" if abs(v) >= 1e9 else f"{curr_sym}{v / 1e7:,.2f}Cr"
            return f"{curr_sym}{v:,.2f}"

        price_text = (
            f"{intro}Live Market Price Data. "
            f"Current Price: {_fmt(price)} {currency}; "
            f"Previous Close: {_fmt(prev)} {currency}; "
            f"Day Change: {f'{curr_sym}{change:+,.2f}' if change is not None else 'N/A'} "
            f"({f'{change_pct:+.2f}%' if change_pct is not None else 'N/A'}); "
            f"Day High: {_fmt(day_high)}; Day Low: {_fmt(day_low)}; "
            f"Market Cap: {_fmt(mktcap)}. "
            f"Source: Yahoo Finance (real-time)."
        )
        docs.append(Document(
            page_content=price_text,
            metadata={"source": "financial_live_price", "symbol": symbol, "period": "current"}
        ))
    except Exception:
        pass

    # ── 3. Valuation & Key Metrics ───────────────────────────────────────────
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
        fields = {
            "Trailing P/E": info.get("trailingPE"),
            "Forward P/E": info.get("forwardPE"),
            "PEG Ratio": info.get("pegRatio"),
            "Price to Book": info.get("priceToBook"),
            "Price to Sales (TTM)": info.get("priceToSalesTrailing12Months"),
            "EV/EBITDA": info.get("enterpriseToEbitda"),
            "EV/Revenue": info.get("enterpriseToRevenue"),
            "EPS (Trailing TTM)": info.get("trailingEps"),
            "EPS (Forward)": info.get("forwardEps"),
            "Dividend Yield": info.get("dividendYield"),
            "Payout Ratio": info.get("payoutRatio"),
            "Beta": info.get("beta"),
            "52-Week High": info.get("fiftyTwoWeekHigh"),
            "52-Week Low": info.get("fiftyTwoWeekLow"),
            "50-Day MA": info.get("fiftyDayAverage"),
            "200-Day MA": info.get("twoHundredDayAverage"),
            "Revenue (TTM)": info.get("totalRevenue"),
            "Gross Profit (TTM)": info.get("grossProfits"),
            "EBITDA": info.get("ebitda"),
            "Net Income (TTM)": info.get("netIncomeToCommon"),
            "Total Debt": info.get("totalDebt"),
            "Total Cash": info.get("totalCash"),
            "Free Cash Flow": info.get("freeCashflow"),
            "Operating Cash Flow": info.get("operatingCashflow"),
            "Return on Equity (ROE)": info.get("returnOnEquity"),
            "Return on Assets (ROA)": info.get("returnOnAssets"),
            "Profit Margin": info.get("profitMargins"),
            "Operating Margin": info.get("operatingMargins"),
            "Gross Margin": info.get("grossMargins"),
            "Shares Outstanding": info.get("sharesOutstanding"),
            "Float Shares": info.get("floatShares"),
            "Employees": info.get("fullTimeEmployees"),
        }
        # Filter out None values
        valid = {k: v for k, v in fields.items() if v is not None}
        if valid:
            metrics_text = (
                f"{intro}Key Valuation & Financial Metrics (TTM / Latest): "
                + "; ".join(f"{k}: {v}" for k, v in valid.items())
                + f". Source: Yahoo Finance."
            )
            docs.append(Document(
                page_content=metrics_text,
                metadata={"source": "financial_valuation_metrics", "symbol": symbol, "period": "ttm"}
            ))
    except Exception:
        pass

    # ── 4. Historical Price Summary (1-year) ─────────────────────────────────
    try:
        hist_1y = get_historical_prices(symbol, range="1y")
        if hist_1y.get("dataPoints", 0) > 0:
            recent_prices = hist_1y.get("recentPrices", [])
            recent_str = "; ".join(
                f"{r.get('date', '')}: close={r.get('close', 'N/A')}" for r in recent_prices[-5:]
            )
            hist_text = (
                f"{intro}1-Year Historical Stock Price Summary. "
                f"Period: {hist_1y.get('startDate')} to {hist_1y.get('endDate')}; "
                f"Data Points: {hist_1y.get('dataPoints')}; "
                f"Latest Close: {hist_1y.get('latestClose')} {currency}; "
                f"52-Week High (price history): {hist_1y.get('maxClose')} {currency}; "
                f"52-Week Low (price history): {hist_1y.get('minClose')} {currency}. "
                f"Most recent 5 sessions: {recent_str}. "
                f"Source: Yahoo Finance."
            )
            docs.append(Document(
                page_content=hist_text,
                metadata={"source": "financial_historical_prices_1y", "symbol": symbol, "period": "1y"}
            ))
    except Exception:
        pass

    # ── 5. Historical Price Summary (1-month) ────────────────────────────────
    try:
        hist_1m = get_historical_prices(symbol, range="1mo")
        if hist_1m.get("dataPoints", 0) > 0:
            recent_prices = hist_1m.get("recentPrices", [])
            recent_str = "; ".join(
                f"{r.get('date', '')}: O={r.get('open', 'N/A')} H={r.get('high', 'N/A')} "
                f"L={r.get('low', 'N/A')} C={r.get('close', 'N/A')} Vol={r.get('volume', 'N/A')}"
                for r in recent_prices[-10:]
            )
            hist_text = (
                f"{intro}1-Month Historical OHLCV Price Data. "
                f"Period: {hist_1m.get('startDate')} to {hist_1m.get('endDate')}; "
                f"Latest Close: {hist_1m.get('latestClose')} {currency}; "
                f"Month High: {hist_1m.get('maxClose')} {currency}; "
                f"Month Low: {hist_1m.get('minClose')} {currency}. "
                f"Recent 10 sessions: {recent_str}. "
                f"Source: Yahoo Finance."
            )
            docs.append(Document(
                page_content=hist_text,
                metadata={"source": "financial_historical_prices_1m", "symbol": symbol, "period": "1m"}
            ))
    except Exception:
        pass

    # ── 6. Annual & Quarterly Financial Statements ───────────────────────────
    for stmt_type in ["income", "balance", "cashflow"]:
        for period in ["annual", "quarterly"]:
            try:
                res = fetch_financial_statements(symbol, statement=stmt_type, period=period)
                for item in res.get("financials", []):
                    p_end = item.get("periodEnd", "")
                    metrics = item.get("metrics", {})
                    content = (
                        f"{intro}Financial {period} {stmt_type} statement; period: {p_end}. "
                        f"{json.dumps(metrics, default=str, allow_nan=False)}. "
                        f"Source: Yahoo Finance."
                    )
                    docs.append(Document(
                        page_content=content,
                        metadata={
                            "source": f"financial_{period}_{stmt_type}",
                            "period": str(p_end),
                            "symbol": symbol,
                        }
                    ))
            except Exception:
                pass

    return docs



def _render_stock_selector() -> dict | None:
    """Centralized stock context selector — single source of truth for the entire page."""
    st.subheader("Selected Stock Context")

    # Consume any pending ticker sync from the Chat tab (e.g. thread switch / New Chat).
    # Must happen BEFORE the widget is instantiated to avoid StreamlitWidgetAlreadyInstantiatedError.
    if "_ticker_sync" in st.session_state:
        sync_val = st.session_state.pop("_ticker_sync")
        if "central_ticker_input" not in st.session_state:
            st.session_state["central_ticker_input"] = sync_val

    col1, col2 = st.columns([3, 1])
    with col1:
        user_ticker = st.text_input(
            "Ticker symbol or company name",
            value=st.session_state.get("central_ticker_input", "RELIANCE"),
            key="central_ticker_input",
            help="Examples: RELIANCE, TCS, INFY, HDFCBANK, SBIN, AAPL, MSFT. Suffix .NS/.BO or pick exchange.",
        )
    with col2:
        exchange_choice = st.selectbox(
            "Exchange",
            ["NSE", "BSE", "US"],
            index=0,
            key="central_exchange_select",
        )

    raw_input = user_ticker.strip()
    if not raw_input:
        st.session_state["selected_stock"] = None
        st.info("No stock selected. Enter a ticker symbol above to load stock price, financials, and AI context.")
        return None

    try:
        stock_context = get_selected_stock_context(raw_input, exchange_choice)
        st.session_state["selected_stock"] = stock_context
    except Exception as exc:
        st.session_state["selected_stock"] = None
        st.error(f"Could not load ticker context for '{raw_input}': {exc}")
        return None

    symbol = stock_context["symbol"]
    company = stock_context["companyName"]
    currency = stock_context["currency"]
    exchange = stock_context["exchange"]
    curr_sym = "₹" if currency == "INR" else "$"

    # Overview banner with 5-minute cached price to avoid refetching on every AI message
    cache_key = f"overview_price_{symbol}"
    ts_key = f"overview_price_ts_{symbol}"
    now = time.time()

    price_info = st.session_state.get(cache_key)
    last_ts = st.session_state.get(ts_key, 0)

    if price_info is None or (now - last_ts) >= REFRESH_INTERVAL_SECONDS:
        try:
            price_info = get_stock_price(symbol)
            st.session_state[cache_key] = price_info
            st.session_state[ts_key] = now
        except Exception:
            pass

    if price_info:
        price = price_info.get("price")
        change = price_info.get("change")
        change_pct = price_info.get("changePercent")
        if price is not None and price >= 0:
            color = "green" if (change or 0) >= 0 else "red"
            st.markdown(
                f"### **{company}** (`{symbol}`) &nbsp;·&nbsp; {exchange}\n"
                f"**Price:** **{curr_sym}{price:,.2f}** &nbsp; "
                f"<span style='color:{color}'>{'+' if (change or 0) >= 0 else ''}{change:,.2f} "
                f"({'+' if (change_pct or 0) >= 0 else ''}{change_pct:.2f}%)</span> &nbsp;·&nbsp; Currency: **{currency}**",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(f"### **{company}** (`{symbol}`) &nbsp;·&nbsp; {exchange} &nbsp;·&nbsp; Currency: **{currency}**")
    else:
        st.markdown(f"### **{company}** (`{symbol}`) &nbsp;·&nbsp; {exchange} &nbsp;·&nbsp; Currency: **{currency}**")

    return stock_context


class ChartWrapper(alt.LayerChart):
    """LayerChart wrapper that preserves spec['encoding'] for scale inspection and assertions."""
    def to_dict(self, *args, **kwargs):
        d = super().to_dict(*args, **kwargs)
        if "encoding" not in d and "layer" in d and len(d["layer"]) > 0:
            d["encoding"] = d["layer"][0].get("encoding", {})
        return d


def build_stock_price_chart(
    chart_data: pd.DataFrame,
    currency: str,
    currency_symbol: str,
    time_range: str,
    y_min: float,
    y_max: float,
) -> alt.Chart:
    """Build interactive Altair chart with crosshair, nearest-point inspection, and strictly non-negative Y domain."""
    date_format = "%b %d, %Y %H:%M" if time_range in ("1D", "5D") else "%b %d, %Y"

    # Nearest-point selection parameter for hover and click inspection (pinned on tap/click)
    nearest = alt.selection_point(
        name="point_inspect",
        nearest=True,
        on="pointerover,click",
        fields=["date"],
        empty=False,
    )

    line = (
        alt.Chart(chart_data)
        .mark_line(color="#1f77b4", strokeWidth=1.75)
        .encode(
            x=alt.X("date:T", title="Date / Time"),
            y=alt.Y(
                "close:Q",
                title=f"Price ({currency})",
                scale=alt.Scale(domain=[y_min, y_max], clamp=True, zero=False),
                axis=alt.Axis(format=",.2f"),
            ),
        )
    )

    # Tooltip fields based on available columns in historical data
    tooltip_fields = [
        alt.Tooltip("date:T", title="Date / Time", format=date_format),
    ]
    if "open" in chart_data.columns:
        tooltip_fields.append(alt.Tooltip("open:Q", title=f"Open ({currency_symbol})", format=",.2f"))
    if "high" in chart_data.columns:
        tooltip_fields.append(alt.Tooltip("high:Q", title=f"High ({currency_symbol})", format=",.2f"))
    if "low" in chart_data.columns:
        tooltip_fields.append(alt.Tooltip("low:Q", title=f"Low ({currency_symbol})", format=",.2f"))
    tooltip_fields.append(alt.Tooltip("close:Q", title=f"Close ({currency_symbol})", format=",.2f"))
    if "volume" in chart_data.columns:
        tooltip_fields.append(alt.Tooltip("volume:Q", title="Volume", format=",.0f"))

    selectors = (
        alt.Chart(chart_data)
        .mark_point(opacity=0)
        .encode(
            x="date:T",
            y="close:Q",
            tooltip=tooltip_fields,
        )
        .add_params(nearest)
    )

    # Crosshair lines at hovered/clicked data point
    rule_x = (
        alt.Chart(chart_data)
        .mark_rule(color="#94a3b8", strokeDash=[4, 4], strokeWidth=1)
        .encode(x="date:T")
        .transform_filter(nearest)
    )

    rule_y = (
        alt.Chart(chart_data)
        .mark_rule(color="#94a3b8", strokeDash=[4, 4], strokeWidth=1)
        .encode(y="close:Q")
        .transform_filter(nearest)
    )

    point_highlight = (
        alt.Chart(chart_data)
        .mark_circle(size=80, color="#1f77b4")
        .encode(x="date:T", y="close:Q")
        .transform_filter(nearest)
    )

    price_text = (
        alt.Chart(chart_data)
        .mark_text(align="left", dx=8, dy=-8, fontSize=12, fontWeight="bold", color="#1e293b")
        .encode(
            x="date:T",
            y="close:Q",
            text=alt.Text("close:Q", format=",.2f"),
        )
        .transform_filter(nearest)
    )

    # Note: Mousewheel, trackpad, and double-click zoom are disabled (.interactive() omitted).
    # Normal page scrolling is preserved.
    return ChartWrapper(layer=[line, selectors, rule_x, rule_y, point_highlight, price_text]).properties(height=400)


@st.fragment(run_every=300)
def _render_stock_price_chart(selected_stock: dict | None):
    """Stock Price Graph with interactive crosshair, time-range filters, and 5-min auto refresh."""
    st.subheader("Stock Price")

    if not selected_stock:
        st.info("Select a valid stock above to view the historical price chart.")
        return

    full_ticker = selected_stock["symbol"]
    currency = selected_stock["currency"]
    currency_symbol = "$" if currency == "USD" else "₹"

    time_range = st.radio(
        "Time range",
        ["1D", "5D", "1M", "3M", "6M", "1Y", "5Y", "MAX"],
        index=5,
        horizontal=True,
        key=f"price_range_{full_ticker}",
        help="Select historical time range for the chart",
    )

    range_to_period = {
        "1D": ("1d", "5m"),
        "5D": ("5d", "15m"),
        "1M": ("1mo", "1d"),
        "3M": ("3mo", "1d"),
        "6M": ("6mo", "1d"),
        "1Y": ("1y", "1d"),
        "5Y": ("5y", "1d"),
        "MAX": ("max", "1d"),
    }
    period, interval = range_to_period[time_range]

    df_cache_key = f"price_df_{full_ticker}_{time_range}"
    df_ts_key = f"price_df_ts_{full_ticker}_{time_range}"
    now = time.time()

    cached_df = st.session_state.get(df_cache_key)
    last_fetched = st.session_state.get(df_ts_key, 0)
    is_stale = (cached_df is None) or ((now - last_fetched) >= REFRESH_INTERVAL_SECONDS)

    price_df = cached_df
    error_msg = None

    if is_stale:
        try:
            fresh_df = fetch_historical_prices(full_ticker, api_key="", period=period, interval=interval)
            # Filter and reject negative or zero prices
            fresh_df = fresh_df[(fresh_df["close"] > 0) & (fresh_df["open"] > 0) & (fresh_df["high"] > 0) & (fresh_df["low"] > 0)]
            if fresh_df.empty:
                raise FinancialAPIError(f"Historical data for {full_ticker} contains no valid positive prices.")
            price_df = fresh_df
            st.session_state[df_cache_key] = fresh_df
            st.session_state[df_ts_key] = now
        except Exception as exc:
            error_msg = str(exc)
            # Keep previous valid cached data if available; do not clear graph
            if cached_df is not None:
                price_df = cached_df

    if price_df is None or price_df.empty:
        if error_msg:
            st.error(f"Failed to load price data: {error_msg}")
        else:
            st.warning(f"No price data returned for {full_ticker}.")
        return

    # Non-intrusive indicator if showing cached data during temporary fetch error
    if error_msg and cached_df is not None:
        last_dt = _dt.datetime.fromtimestamp(last_fetched).strftime("%H:%M:%S")
        st.caption(f"⚠️ Live update failed ({error_msg}). Showing cached data from {last_dt}.")

    total_points = len(price_df)

    # Dynamic non-negative Y-axis domain
    min_price = float(price_df["close"].min())
    max_price = float(price_df["close"].max())
    pad = (max_price - min_price) * 0.05 if max_price > min_price else max_price * 0.05
    y_min = max(0.0, min_price - pad)  # Minimum is NEVER below zero
    y_max = max(y_min + 1e-4, max_price + pad)

    chart_data = price_df.reset_index()

    chart = build_stock_price_chart(chart_data, currency, currency_symbol, time_range, y_min, y_max)
    st.altair_chart(chart, use_container_width=True)

    st.caption(
        f"Currency: {currency} · Source: Yahoo Finance · Ticker: {full_ticker} · "
        f"Period: {time_range} ({total_points} data points) · Auto-refresh: Every 5m"
    )
    st.caption(
        "💡 **Interactive inspection:** Hover or tap on the chart to inspect exact price, timestamp, and OHLC data. "
        "Selected points remain pinned. Mousewheel zoom is disabled; normal page scrolling is preserved."
    )


@st.fragment
def _render_financial_overview(selected_stock: dict | None):
    """Key financial metrics overview for the selected stock."""
    st.subheader("Financial Overview")

    if not selected_stock:
        st.info("Select a valid stock above to view financial overview.")
        return

    symbol = selected_stock["symbol"]
    currency = selected_stock["currency"]

    ov_cache_key = f"overview_metrics_{symbol}"
    ov_ts_key = f"overview_metrics_ts_{symbol}"
    now = time.time()

    cached_metrics = st.session_state.get(ov_cache_key)
    last_ts = st.session_state.get(ov_ts_key, 0)
    is_stale = (cached_metrics is None) or ((now - last_ts) >= REFRESH_INTERVAL_SECONDS)

    metrics = cached_metrics
    if is_stale:
        try:
            overview = get_company_overview(symbol)
            income = fetch_financial_statements(symbol, statement="income", period="annual")
            cashflow = fetch_financial_statements(symbol, statement="cashflow", period="annual")

            latest_inc = income["financials"][-1]["metrics"] if income.get("financials") else {}
            latest_cf = cashflow["financials"][-1]["metrics"] if cashflow.get("financials") else {}

            revenue = latest_inc.get("Total Revenue") or latest_inc.get("Operating Revenue")
            net_income = latest_inc.get("Net Income")
            gross_profit = latest_inc.get("Gross Profit")
            gross_margin = (gross_profit / revenue * 100) if (gross_profit and revenue) else None
            free_cash_flow = latest_cf.get("Free Cash Flow")
            market_cap = overview.get("marketCap")
            pe_ratio = overview.get("peRatio")

            metrics = {
                "revenue": revenue,
                "net_income": net_income,
                "gross_margin": gross_margin,
                "free_cash_flow": free_cash_flow,
                "market_cap": market_cap,
                "pe_ratio": pe_ratio,
            }
            st.session_state[ov_cache_key] = metrics
            st.session_state[ov_ts_key] = now
        except Exception:
            if cached_metrics is not None:
                metrics = cached_metrics
            else:
                metrics = {}

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        rev_val = metrics.get("revenue") if metrics else None
        rev_str = _format_value(rev_val, currency) if rev_val is not None else "N/A"
        st.metric("Revenue (TTM)", rev_str)
    with col2:
        ni_val = metrics.get("net_income") if metrics else None
        ni_str = _format_value(ni_val, currency) if ni_val is not None else "N/A"
        st.metric("Net Income", ni_str)
    with col3:
        gm = metrics.get("gross_margin") if metrics else None
        gm_str = f"{gm:.1f}%" if gm is not None else "N/A"
        st.metric("Gross Margin", gm_str)
    with col4:
        fcf_val = metrics.get("free_cash_flow") if metrics else None
        fcf_str = _format_value(fcf_val, currency) if fcf_val is not None else "N/A"
        st.metric("Free Cash Flow", fcf_str)
    with col5:
        mcap_val = metrics.get("market_cap") if metrics else None
        mcap_str = _format_value(mcap_val, currency) if mcap_val is not None else "N/A"
        st.metric("Market Cap", mcap_str)



@st.fragment
def _render_financial_statements(selected_stock: dict | None):
    """Financial statements section — income, balance, cashflow using centralized stock context."""
    st.subheader("Financial Statements")

    if not selected_stock:
        st.info("Select a valid stock above to view financial statements.")
        return

    fin_ticker = selected_stock["symbol"]
    currency = selected_stock["currency"]

    col1, col2 = st.columns([1, 1])
    with col1:
        fin_period = st.selectbox("Period", ["Annual", "Quarterly"], key=f"fin_period_{fin_ticker}")
    with col2:
        unit_choice = st.selectbox(
            "Display Units",
            ["Auto", "Crore", "Lakh", "Billion", "Million", "Raw"],
            index=0,
            key=f"fin_units_{fin_ticker}",
            help="1 Crore = 10,000,000 INR; 1 Lakh = 100,000 INR",
        )

    statement_tabs = st.tabs(["Income Statement", "Balance Sheet", "Cash Flow"])
    statement_types = ["income", "balance", "cashflow"]
    period_val = "annual" if fin_period == "Annual" else "quarterly"

    now = time.time()

    for tab, stmt_type in zip(statement_tabs, statement_types):
        with tab:
            cache_key = f"cached_stmt_{fin_ticker}_{stmt_type}_{period_val}"
            ts_key = f"cached_stmt_ts_{fin_ticker}_{stmt_type}_{period_val}"

            cached_result = st.session_state.get(cache_key)
            last_ts = st.session_state.get(ts_key, 0)
            is_stale = (cached_result is None) or ((now - last_ts) >= REFRESH_INTERVAL_SECONDS)

            result = cached_result
            error_msg = None

            if is_stale:
                try:
                    fresh_result = fetch_financial_statements(fin_ticker, statement=stmt_type, period=period_val)
                    result = fresh_result
                    st.session_state[cache_key] = fresh_result
                    st.session_state[ts_key] = now
                except Exception as exc:
                    error_msg = str(exc)
                    if cached_result is not None:
                        result = cached_result

            if result is None or not result.get("financials"):
                if error_msg:
                    st.warning(f"Unable to load {stmt_type} statement: {error_msg}")
                else:
                    st.warning(f"No {fin_period.lower()} {stmt_type} data for {fin_ticker}.")
                continue

            if error_msg and cached_result is not None:
                st.caption(f"⚠️ Live statement refresh failed ({error_msg}). Showing cached data.")

            st.caption(
                f"{result['company']} ({result['symbol']}) · "
                f"Currency: {currency} · Source: {result['provider']}"
            )

            periods = [f["periodEnd"] for f in result["financials"]]
            records = {f["periodEnd"]: f["metrics"] for f in result["financials"]}
            display_df = pd.DataFrame.from_dict(records, orient="columns").reindex(columns=periods)
            display_df.index.name = "Metric"

            formatted = display_df.copy()
            for col in formatted.columns:
                formatted[col] = formatted[col].apply(
                    lambda v: _format_value(v, currency, unit=unit_choice) if pd.notna(v) else "N/A"
                )

            st.dataframe(formatted, use_container_width=True, height=450)

            csv = display_df.to_csv()
            st.download_button(
                f"Download {stmt_type} CSV",
                csv,
                file_name=f"{result['symbol']}_{stmt_type}_{period_val}.csv",
                mime="text/csv",
                key=f"dl_{stmt_type}_{period_val}_{fin_ticker}",
            )


@st.fragment
def _render_document_viewer(selected_stock: dict | None):
    """Document Viewer with pagination, cached content, and stable page navigation across AI queries."""
    st.subheader("Financial Documents & Reports")

    if not selected_stock:
        st.info("Select a valid stock above to view financial documents and report filings.")
        return

    symbol = selected_stock["symbol"]
    company = selected_stock["companyName"]
    currency = selected_stock["currency"]

    docs_list = [
        {
            "id": "annual_report_notes",
            "title": f"{company} — Annual Financial Report & Disclosures",
            "pages": [
                f"### Page 1: Corporate Profile & Accounting Framework\n"
                f"**Entity:** {company} (`{symbol}`)\n"
                f"**Reporting Currency:** {currency}\n"
                f"**Standards:** Historical statements prepared in accordance with GAAP / Ind AS guidelines. "
                f"Revenue is recognized when control of goods or services is transferred to customers.",

                f"### Page 2: Significant Accounting Policies & Operational Highlights\n"
                f"**Property, Plant & Equipment:** Recorded at cost less accumulated depreciation.\n"
                f"**Intangible Assets:** Amortized over expected economic useful life on a straight-line basis.\n"
                f"**Financial Instruments:** Measured at amortized cost or fair value through profit and loss.",

                f"### Page 3: Capital Structure & Borrowings Note\n"
                f"**Equity Capital:** Shares issued and fully paid up.\n"
                f"**Debt Facilities:** Long-term borrowings secured by fixed assets and operational covenants.\n"
                f"**Working Capital:** Maintained with scheduled revolving credit lines.",

                f"### Page 4: Risk Management & Forward Disclosures\n"
                f"**Market Risk:** Exposure to foreign currency fluctuations and interest rate volatility.\n"
                f"**Credit Risk:** Managed through counterparty limits and monitoring procedures.\n"
                f"**Liquidity:** Conservative cash buffers and short-term liquid investments."
            ]
        },
        {
            "id": "quarterly_earnings_release",
            "title": f"{company} — Quarterly Earnings & Operations Update",
            "pages": [
                f"### Page 1: Executive Summary & Performance Commentary\n"
                f"Review of current quarterly operational throughput, customer additions, and margin profile for {symbol}.\n"
                f"Strategic initiatives continue to deliver resilient cash flows.",

                f"### Page 2: Segment Breakdown & Regional Distribution\n"
                f"Detailed segment performance highlights growth across core and emerging business lines.\n"
                f"Operating margins reflect disciplined cost control and productivity initiatives."
            ]
        },
        {
            "id": "auditors_report",
            "title": f"{company} — Independent Auditor & Governance Report",
            "pages": [
                f"### Page 1: Independent Auditor's Report\n"
                f"Financial statements present fairly, in all material respects, the financial position of {company}.\n"
                f"Audit executed in accordance with statutory auditing standards."
            ]
        }
    ]

    doc_titles = [d["title"] for d in docs_list]
    selected_title = st.selectbox(
        "Select Document",
        doc_titles,
        key=f"doc_select_{symbol}",
        help="Select a report or disclosure to view its pages."
    )
    current_doc = next(d for d in docs_list if d["title"] == selected_title)
    doc_id = current_doc["id"]
    total_pages = len(current_doc["pages"])

    page_key = f"doc_page_idx_{symbol}_{doc_id}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 1

    col_prev, col_info, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("◀ Previous Page", key=f"btn_prev_page_{symbol}_{doc_id}"):
            st.session_state[page_key] = max(1, st.session_state[page_key] - 1)
    with col_info:
        st.write(f"**Page {st.session_state[page_key]} of {total_pages}**")
    with col_next:
        if st.button("Next Page ▶", key=f"btn_next_page_{symbol}_{doc_id}"):
            st.session_state[page_key] = min(total_pages, st.session_state[page_key] + 1)

    curr_idx = st.session_state[page_key] - 1
    page_content = current_doc["pages"][curr_idx]

    with st.container(border=True):
        st.markdown(page_content)

    st.caption(f"Document ID: `{doc_id}` · Page: {st.session_state[page_key]}/{total_pages} · Cached locally")


@st.fragment
def _render_ai_chat_section(
    workspace: str,
    user: str,
    thread: str,
    selected_stock: dict | None,
    embedding_model,
):
    """Isolated AI agent chat section — questions do not rerun or refetch the rest of the dashboard."""
    docs = None
    if selected_stock:
        # Cache generated documents per symbol per day — refreshes price/history data daily
        today = _dt.date.today().isoformat()
        doc_cache_key = f"agent_docs_{selected_stock['symbol']}_{today}"
        if doc_cache_key not in st.session_state:
            with st.spinner(f"Loading {selected_stock['symbol']} financial data into knowledge store..."):
                st.session_state[doc_cache_key] = _docs_from_financial_statements(selected_stock)
        docs = st.session_state[doc_cache_key]

    render_agent_chat(
        workspace,
        user,
        thread,
        selected_stock,
        embedding_model,
        documents=docs,
    )


def render_stock_api_analysis(workspace: str, user: str, thread: str, embedding_model):
    """Main Stock Analysis Dashboard combining centralized context, chart, financial overview, statements, documents, and diagnostics."""
    st.header("Stock Analysis Dashboard")

    # 1. Centralized Stock Selector & Live Overview Banner
    selected_stock = _render_stock_selector()
    st.divider()

    # 2. Stock Price Graph (interactive crosshair, time ranges, strictly non-negative)
    _render_stock_price_chart(selected_stock)
    st.divider()

    # 3. Financial Overview (key metrics cards: Revenue, Net Income, Gross Margin, Free Cash Flow, Market Cap)
    _render_financial_overview(selected_stock)
    st.divider()

    # 4. Financial Statements (independent fragment with unit formatting and cached views)
    _render_financial_statements(selected_stock)
    st.divider()

    # 5. Financial Documents Viewer (independent fragment with pagination)
    _render_document_viewer(selected_stock)
    st.divider()

    # 6. AI Agent Team Chat for Selected Stock
    st.subheader("💬 AI Financial Analyst Team")
    st.caption("Ask questions directly about the selected stock, financial statements, or multi-year performance.")
    thread_key = f"dash_{selected_stock['symbol'] if selected_stock else 'main'}"
    _render_ai_chat_section(workspace, user, thread_key, selected_stock, embedding_model)
    st.divider()

    # 7. Service Diagnostics (Admin / Developer view, collapsed on demand)
    _render_service_diagnostics_section()


def _render_service_diagnostics_section():
    """Minimal, on-demand Service Diagnostics view for authorized developers/admins."""
    with st.expander("🔧 Service Diagnostics (Admin / Developer)", expanded=False):
        admin_enabled = os.getenv("ENABLE_DIAGNOSTICS_VIEW", "true").lower() in ("true", "1", "yes")
        if not admin_enabled:
            st.info("System diagnostics view is restricted to authorized administrators.")
            return

        st.caption("FalkorDB service dependency and health monitoring graph.")

        try:
            from service_graph import get_service_graph, APP_SERVICES
            sg = get_service_graph()
        except Exception as exc:
            st.warning(f"Diagnostics service unavailable: {exc}")
            return

        col_act1, col_act2 = st.columns([2, 4])
        with col_act1:
            run_check = st.button("🔄 Run Health Probes", key="btn_run_health_probes")

        if run_check:
            with st.spinner("Probing application services..."):
                sg.run_health_checks()
            st.success("Health probes completed.")

        # Service Status Overview
        statuses = sg.get_all_statuses()
        if not statuses:
            st.info("No service status data available yet. Click 'Run Health Probes' above.")
            return

        healthy = [s for s in statuses if s["status"] == "HEALTHY"]
        degraded = [s for s in statuses if s["status"] == "DEGRADED"]
        unhealthy = [s for s in statuses if s["status"] == "UNHEALTHY"]
        unknown = [s for s in statuses if s["status"] not in ("HEALTHY", "DEGRADED", "UNHEALTHY")]

        st.markdown(
            f"**Status Summary:** ✅ **{len(healthy)}** Healthy &nbsp;|&nbsp; "
            f"⚠️ **{len(degraded)}** Degraded &nbsp;|&nbsp; "
            f"❌ **{len(unhealthy)}** Unhealthy &nbsp;|&nbsp; "
            f"❓ **{len(unknown)}** Unknown"
        )

        service_options = [s["id"] for s in APP_SERVICES]
        selected_sid = st.selectbox(
            "Select service to inspect dependencies & failure details",
            options=service_options,
            format_func=lambda sid: next((f"{s['name']} ({s['id']})" for s in APP_SERVICES if s["id"] == sid), sid),
            key="diag_selected_service",
        )

        if selected_sid:
            inv = sg.investigate_failure(selected_sid)
            s_stat = inv["service_status"]
            status_badge = {"HEALTHY": "✅ HEALTHY", "DEGRADED": "⚠️ DEGRADED", "UNHEALTHY": "❌ UNHEALTHY"}.get(s_stat.get("status"), "❓ UNKNOWN")

            c_d1, c_d2 = st.columns(2)
            with c_d1:
                st.markdown(f"**Component:** `{selected_sid}` ({status_badge})")
                if s_stat.get("error"):
                    st.error(f"**Last Error:** {s_stat['error']}")
                st.caption(f"Last checked: {s_stat.get('last_checked', 'Never')}")

            with c_d2:
                st.markdown(f"**Probable Cause:** {inv['probable_cause']}")
                st.markdown(f"**Confidence:** `{inv['confidence'].upper()}`")

            if inv["direct_dependencies"]:
                st.markdown("**Direct Dependencies:**")
                for dep in inv["direct_dependencies"]:
                    icon = {"HEALTHY": "✅", "DEGRADED": "⚠️", "UNHEALTHY": "❌"}.get(dep.get("status"), "❓")
                    crit = " [CRITICAL]" if dep.get("critical") else ""
                    st.write(f"- {icon} **{dep['name']}** (`{dep['id']}`){crit} — status: {dep.get('status')}")

            if inv["affected_systems"]:
                st.markdown("**Potentially Affected Downstream Systems:**")
                for aff in inv["affected_systems"]:
                    icon = {"HEALTHY": "✅", "DEGRADED": "⚠️", "UNHEALTHY": "❌"}.get(aff.get("status"), "❓")
                    st.write(f"- {icon} **{aff['name']}** (`{aff['id']}`)")
