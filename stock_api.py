from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any

import pandas as pd
import requests
import yfinance as yf

BASE_URL = "https://api.example.com"
TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,24}$")


class FinancialAPIError(RuntimeError):
    pass


@dataclass
class StockStatements:
    symbol: str
    currency: str
    annual_balance: pd.DataFrame
    annual_income: pd.DataFrame
    quarterly_income: pd.DataFrame
    fetched_at: str
    source_urls: dict[str, str]
    warnings: list[str]


def validate_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if not TICKER_PATTERN.fullmatch(cleaned):
        raise FinancialAPIError(
            "Enter a valid ticker, e.g. AAPL or RELIANCE.NS (letters, digits, '.', '-', '_')."
        )
    return cleaned


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _first(record: dict, *keys: str) -> float | None:
    for key in keys:
        num = _number(record.get(key))
        if num is not None:
            return num
    return None


def _total_debt(rec: dict) -> float | None:
    direct = _first(rec, "totalDebt")
    if direct is not None:
        return direct
    short = _first(rec, "shortTermDebt")
    long = _first(rec, "longTermDebt")
    if short is None or long is None:
        return None
    return short + long


def _investments(rec: dict) -> float | None:
    direct = _first(rec, "totalInvestments")
    if direct is not None:
        return direct
    short = _first(rec, "shortTermInvestments")
    long = _first(rec, "longTermInvestments")
    if short is None or long is None:
        return None
    return short + long


def _get_rows(session: Any, path: str, params: dict[str, Any], key: str) -> list[dict]:
    try:
        response = session.get(
            f"{BASE_URL}/{path}",
            params=params,
            headers={"apikey": key, "Accept": "application/json"},
            timeout=25,
        )
        response.raise_for_status()
        body = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        if status in (401, 402, 403):
            raise FinancialAPIError(
                f"API request rejected. Check API key and whether your plan includes {path} for this symbol (HTTP {status})."
            ) from exc
        if status == 429:
            raise FinancialAPIError("Rate limit reached. Retry later.") from exc
        raise FinancialAPIError(f"Request failed (HTTP {status}) for {path}.") from exc
    except (requests.RequestException, ValueError) as exc:
        raise FinancialAPIError(f"Could not fetch {path}: {exc}") from exc

    if isinstance(body, dict):
        raise FinancialAPIError(
            f"Did not return statement rows for {path}. Check the stock symbol and API access."
        )
    if not isinstance(body, list) or not all(isinstance(x, dict) for x in body):
        raise FinancialAPIError(f"Invalid response for {path}.")
    if not body:
        raise FinancialAPIError(f"No records returned for {path}.")
    return body


def _fiscal_year(rec: dict) -> int | None:
    raw = rec.get("fiscalYear")
    if raw is not None and str(raw).isdigit():
        return int(raw)
    try:
        return datetime.fromisoformat(rec["date"][:10]).year
    except (KeyError, ValueError, TypeError):
        return None


def _annual_rows(records: list[dict], kind: str) -> dict[int, dict]:
    by_year: dict[int, dict] = {}
    for rec in sorted(records, key=lambda r: str(r.get("date", ""))):
        year = _fiscal_year(rec)
        if year is None:
            continue
        if kind == "balance":
            by_year[year] = {
                "Total assets": _first(rec, "totalAssets"),
                "Current liabilities": _first(rec, "totalCurrentLiabilities"),
                "Total liabilities": _first(rec, "totalLiabilities"),
                "Shareholders equity": _first(rec, "totalStockholdersEquity"),
                "Borrowings": _total_debt(rec),
                "Cash and cash equivalents": _first(rec, "cashAndCashEquivalents"),
                "Investments": _investments(rec),
                "Fixed assets": _first(rec, "propertyPlantEquipmentNet"),
                "Capital work in progress": _first(rec, "capitalWorkInProgress"),
                "Equity capital": _first(rec, "commonStock"),
                "Retained earnings": _first(rec, "retainedEarnings"),
            }
        else:
            by_year[year] = {
                "Revenue": _first(rec, "revenue"),
                "Operating profit (EBIT)": _first(rec, "operatingIncome"),
                "Net profit": _first(rec, "netIncome"),
                "Net non-operating income/expenses": _first(
                    rec, "totalOtherIncomeExpensesNet", "otherIncomeExpensesNet"
                ),
            }
    return by_year


def _quarter_rows(records: list[dict]) -> pd.DataFrame:
    rows = {}
    for rec in records:
        try:
            date = pd.Timestamp(rec["date"][:10])
        except (ValueError, KeyError, TypeError):
            continue
        rows[date] = {
            "Fiscal year": _fiscal_year(rec),
            "Fiscal quarter": str(rec.get("period", "")).upper(),
            "Revenue": _first(rec, "revenue"),
            "Operating profit (EBIT)": _first(rec, "operatingIncome"),
            "Net profit": _first(rec, "netIncome"),
        }
    if not rows:
        raise FinancialAPIError("Quarterly statements have no usable reporting dates.")
    return pd.DataFrame.from_dict(rows, orient="index").sort_index().rename_axis("Quarter end")


def parse_statements(
    symbol: str,
    balance: list[dict],
    income: list[dict],
    quarterly: list[dict],
) -> StockStatements:
    symbol = validate_symbol(symbol)
    currencies = {
        str(r.get("reportedCurrency")).strip().upper()
        for group in (balance, income, quarterly)
        for r in group
        if r.get("reportedCurrency")
    }
    if len(currencies) > 1:
        raise FinancialAPIError(
            f"API returned mixed reporting currencies ({', '.join(sorted(currencies))}); "
            "automatic calculations would be misleading."
        )
    currency = next(iter(currencies), "UNKNOWN")

    balance_years = _annual_rows(balance, "balance")
    income_years = _annual_rows(income, "income")
    common = sorted(set(balance_years) & set(income_years))

    if not common:
        raise FinancialAPIError(
            "No matching annual fiscal years between balance and income statements."
        )
    if len(common) < 2:
        raise FinancialAPIError(
            "At least two matching annual fiscal years are required for growth analysis."
        )

    bs = pd.DataFrame([balance_years[y] for y in common], index=common).rename_axis("Fiscal year")
    inc = pd.DataFrame([income_years[y] for y in common], index=common).rename_axis("Fiscal year")
    q = _quarter_rows(quarterly)

    if inc["Revenue"].isna().all() and inc["Net profit"].isna().all():
        raise FinancialAPIError("The API response contains no usable revenue or net-profit values.")

    warnings = []
    if bs["Capital work in progress"].isna().all():
        warnings.append(
            "Capital work in progress (CWIP) is not available in the normalized API records; it is not assumed to be zero."
        )
    if bs["Shareholders equity"].isna().any() or bs["Borrowings"].isna().any():
        warnings.append("Some leverage inputs are missing; those ratios will remain unavailable.")
    if inc["Net non-operating income/expenses"].isna().all():
        warnings.append(
            "Separate other-income figures are unavailable; net non-operating income/expenses is not assumed to be zero."
        )

    return StockStatements(
        symbol=symbol,
        currency=currency,
        annual_balance=bs,
        annual_income=inc,
        quarterly_income=q,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        source_urls={
            "annual_balance": "N/A",
            "annual_income": "N/A",
            "quarterly_income": "N/A",
        },
        warnings=warnings,
    )


def fetch_stock_statements(
    symbol: str,
    api_key: str,
    *,
    session: Any = None,
    annual_limit: int = 8,
    quarter_limit: int = 20,
) -> StockStatements:
    symbol = validate_symbol(symbol)
    if not api_key or not api_key.strip():
        raise FinancialAPIError("API key is missing. Add it to .env to enable API fetching.")
    if not (2 <= annual_limit <= 100) or not (4 <= quarter_limit <= 100):
        raise ValueError("Annual limit must be 2–100, quarter limit must be 4–100")

    client = session or requests.Session()
    base = {"symbol": symbol}
    balance = _get_rows(
        client,
        "balance-sheet-statement",
        {**base, "period": "annual", "limit": annual_limit},
        api_key,
    )
    income = _get_rows(
        client, "income-statement", {**base, "period": "annual", "limit": annual_limit}, api_key
    )
    quarterly = _get_rows(
        client, "income-statement", {**base, "period": "quarter", "limit": quarter_limit}, api_key
    )

    return parse_statements(symbol, balance, income, quarterly)


def _pct(num: Any, den: Any) -> float | None:
    n, d = _number(num), _number(den)
    return (100 * n / d) if (n is not None and d is not None and d != 0) else None


def _ratio(num: Any, den: Any) -> float | None:
    val = _pct(num, den)
    return (val / 100) if val is not None else None


def calculate_ratios(statements: StockStatements) -> dict[str, pd.DataFrame]:
    bal = statements.annual_balance
    inc = statements.annual_income
    q = statements.quarterly_income
    annual = inc.join(bal, how="inner")

    rows = []
    for year, r in annual.iterrows():
        employed = None
        if _number(r["Total assets"]) is not None and _number(r["Current liabilities"]) is not None:
            employed = r["Total assets"] - r["Current liabilities"]

        rows.append(
            {
                "Fiscal year": year,
                "Operating margin (%)": _pct(r["Operating profit (EBIT)"], r["Revenue"]),
                "Net margin (%)": _pct(r["Net profit"], r["Revenue"]),
                "ROE - closing equity (%)": _pct(r["Net profit"], r["Shareholders equity"]),
                "ROCE - EBIT / capital employed (%)": _pct(r["Operating profit (EBIT)"], employed),
                "Debt / equity (x)": _ratio(r["Borrowings"], r["Shareholders equity"]),
            }
        )

    ratios = pd.DataFrame(rows).set_index("Fiscal year")

    growth = pd.DataFrame(index=inc.index)
    metrics = [
        ("Revenue", "Revenue YoY (%)"),
        ("Operating profit (EBIT)", "Operating profit YoY (%)"),
        ("Net profit", "Net profit YoY (%)"),
    ]
    for col, label in metrics:
        vals = inc[col]
        growth[label] = [
            (
                _pct(vals.loc[y] - vals.loc[y - 1], vals.loc[y - 1])
                if (y - 1 in vals.index and pd.notna(vals.loc[y]) and pd.notna(vals.loc[y - 1]))
                else None
            )
            for y in inc.index
        ]

    qratios = pd.DataFrame(index=q.index)
    qratios["Operating margin (%)"] = [
        _pct(r["Operating profit (EBIT)"], r["Revenue"]) for _, r in q.iterrows()
    ]
    qratios["Net margin (%)"] = [_pct(r["Net profit"], r["Revenue"]) for _, r in q.iterrows()]

    # Match each quarter with the same quarter in the previous year
    lookup = {}
    for _, r in q.iterrows():
        yr, period = r["Fiscal year"], r["Fiscal quarter"]
        if pd.notna(yr) and period in ("Q1", "Q2", "Q3", "Q4"):
            lookup[(int(yr), period)] = r

    yoy = []
    for date, r in q.iterrows():
        yr = r["Fiscal year"]
        previous = lookup.get((int(yr) - 1, r["Fiscal quarter"])) if pd.notna(yr) else None
        item = {"Quarter end": date}
        for col, label in metrics:
            curr_val = _number(r[col])
            prev_val = _number(previous[col]) if previous is not None else None
            item[label] = (
                _pct(curr_val - prev_val, prev_val)
                if (curr_val is not None and prev_val is not None)
                else None
            )
        yoy.append(item)

    quarterly_yoy = pd.DataFrame(yoy).set_index("Quarter end")

    return {
        "Annual profitability & leverage": ratios,
        "Annual growth": growth,
        "Quarterly margins": qratios,
        "Quarterly YoY growth": quarterly_yoy,
    }


def fetch_historical_prices(
    symbol: str,
    api_key: str = "",
    *,
    start: str | None = None,
    end: str | None = None,
    period: str | None = None,
    interval: str | None = None,
    session: Any = None,
) -> pd.DataFrame:
    """Fetch historical OHLCV prices for *symbol* using yfinance.

    Args:
        symbol: Validated ticker symbol (e.g., ``AAPL`` or ``RELIANCE.NS``).
        api_key: Ignored – retained for compatibility with existing callers.
        start, end: Optional ``YYYY-MM-DD`` strings limiting the date range.
        period: Optional yfinance period string (e.g., '1d', '5d', '1mo', '1y', 'max').
        interval: Optional interval string (e.g., '5m', '1d', '1wk').
        session: Unused – kept for signature compatibility.

    Returns:
        ``DataFrame`` indexed by ``date`` with columns
        ``open``, ``high``, ``low``, ``close``, ``volume``.
    """
    symbol = validate_symbol(symbol)
    try:
        if period:
            eff_interval = interval or ("5m" if period == "1d" else "1d")
            data = yf.download(symbol, period=period, interval=eff_interval, progress=False)
        else:
            data = yf.download(symbol, start=start, end=end, interval=interval, progress=False)
    except Exception as exc:
        raise FinancialAPIError(f"Failed to download data for {symbol}: {exc}") from exc
    if data.empty:
        raise FinancialAPIError(f"No historical price records returned for {symbol}.")

    # Flatten MultiIndex columns if present
    if data.columns.nlevels > 1:
        data.columns = data.columns.droplevel(1)

    expected = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in expected if c not in data.columns]
    if missing:
        raise FinancialAPIError(
            f"Historical data for {symbol} missing columns: {', '.join(missing)}"
        )

    df = data[expected].rename(columns={c: c.lower() for c in expected})
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = "date"
    df = df.dropna()
    # Reject invalid negative price values; stock prices must never be negative
    price_cols = ["open", "high", "low", "close"]
    invalid_prices = (df[price_cols] < 0).any(axis=1)
    if invalid_prices.any():
        df = df[~invalid_prices]
    if df.empty:
        raise FinancialAPIError(
            f"Historical price data for {symbol} contains invalid negative prices."
        )
    # Deduplicate timestamps, keeping the most recent point
    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    return df


def fetch_financial_statements(
    symbol: str,
    statement: str = "income",
    period: str = "annual",
) -> dict:
    """Fetch financial statements via yfinance.

    Args:
        symbol: Ticker symbol (e.g. 'AAPL' or 'RELIANCE.NS').
        statement: One of 'income', 'balance', 'cashflow'.
        period: 'annual' or 'quarterly'.

    Returns:
        Dict with symbol, currency, statement type, period, and financials list.
    """
    symbol = validate_symbol(symbol)
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        currency = info.get("currency", "USD")
        company_name = info.get("longName", info.get("shortName", symbol))
    except Exception:
        currency = "USD"
        company_name = symbol

    attr_map = {
        ("income", "annual"): "income_stmt",
        ("income", "quarterly"): "quarterly_income_stmt",
        ("balance", "annual"): "balance_sheet",
        ("balance", "quarterly"): "quarterly_balance_sheet",
        ("cashflow", "annual"): "cashflow",
        ("cashflow", "quarterly"): "quarterly_cashflow",
    }
    attr = attr_map.get((statement, period))
    if attr is None:
        raise FinancialAPIError(f"Invalid statement/period: {statement}/{period}")

    try:
        df = getattr(ticker, attr)
    except Exception as exc:
        raise FinancialAPIError(
            f"Failed to fetch {statement} ({period}) for {symbol}: {exc}"
        ) from exc

    if df is None or df.empty:
        raise FinancialAPIError(f"No {period} {statement} data available for {symbol}.")

    financials = []
    for col in df.columns:
        period_end = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
        metrics = {}
        for metric_name, value in df[col].items():
            if pd.notna(value):
                metrics[metric_name] = float(value)
        if metrics:
            financials.append(
                {
                    "periodEnd": period_end,
                    "metrics": metrics,
                }
            )

    # Reverse so oldest first
    financials.reverse()

    return {
        "symbol": symbol,
        "company": company_name,
        "currency": currency,
        "statement": statement,
        "period": period,
        "provider": "yahoo_finance",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "financials": financials,
    }


KNOWN_TICKER_MAPPINGS = {
    "RELIANCE": "RELIANCE.NS",
    "RELIANCE INDUSTRIES": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "TATA CONSULTANCY SERVICES": "TCS.NS",
    "INFY": "INFY.NS",
    "INFOSYS": "INFY.NS",
    "HDFC": "HDFCBANK.NS",
    "HDFC BANK": "HDFCBANK.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "SBI": "SBIN.NS",
    "STATE BANK OF INDIA": "SBIN.NS",
    "SBIN": "SBIN.NS",
    "ITC": "ITC.NS",
    "BHARTI AIRTEL": "BHARTIARTL.NS",
    "BHARTIARTL": "BHARTIARTL.NS",
    "ICICI": "ICICIBANK.NS",
    "ICICI BANK": "ICICIBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "APPLE": "AAPL",
    "AAPL": "AAPL",
    "MICROSOFT": "MSFT",
    "MSFT": "MSFT",
    "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL",
    "GOOGL": "GOOGL",
    "GOOG": "GOOG",
    "NVIDIA": "NVDA",
    "NVDA": "NVDA",
    "TESLA": "TSLA",
    "TSLA": "TSLA",
    "AMAZON": "AMZN",
    "AMZN": "AMZN",
    "META": "META",
}


def resolve_ticker(raw: str, exchange: str = "NSE") -> str:
    cleaned = raw.strip().upper()
    if cleaned in KNOWN_TICKER_MAPPINGS:
        return KNOWN_TICKER_MAPPINGS[cleaned]
    from stock_resolver import resolve_ticker as sr_resolve_ticker

    return sr_resolve_ticker(raw, default_exchange=exchange)


def get_selected_stock_context(symbol_or_name: str, exchange: str = "NSE") -> dict:
    raw = (symbol_or_name or "").strip()
    if not raw:
        raise FinancialAPIError("No ticker or company name provided.")

    from stock_resolver import resolve_company as sr_resolve_company

    comp_info = sr_resolve_company(raw)

    if comp_info:
        resolved = comp_info["ticker"]
        comp_name = comp_info.get("name") or resolved
        curr = comp_info.get("currency") or ("INR" if resolved.endswith((".NS", ".BO")) else "USD")
        exch = comp_info.get("exchange") or (
            "NSE" if resolved.endswith(".NS") else ("BSE" if resolved.endswith(".BO") else "US")
        )
    else:
        resolved = resolve_ticker(raw, exchange)
        comp_name = resolved
        curr = "INR" if resolved.endswith((".NS", ".BO")) else "USD"
        exch = "NSE" if resolved.endswith(".NS") else ("BSE" if resolved.endswith(".BO") else "US")

    symbol = validate_symbol(resolved)
    try:
        ticker_obj = yf.Ticker(symbol)
        info = ticker_obj.info or {}
        comp_name = info.get("longName") or info.get("shortName") or comp_name
        curr = info.get("currency") or curr
        exch = info.get("exchange") or exch
    except Exception:
        pass

    return {
        "symbol": symbol,
        "companyName": comp_name,
        "exchange": exch,
        "currency": curr,
    }


def get_stock_price(symbol: str) -> dict:
    """Get real-time stock price and day range for a ticker symbol."""
    symbol = validate_symbol(symbol)
    ticker = yf.Ticker(symbol)
    info = ticker.info or {}
    fast = getattr(ticker, "fast_info", None)
    price = (
        getattr(fast, "last_price", None)
        or info.get("currentPrice")
        or info.get("regularMarketPrice")
    )
    previous = getattr(fast, "previous_close", None) or info.get("regularMarketPreviousClose")
    if price is not None and price < 0:
        price = None
    if previous is not None and previous < 0:
        previous = None
    curr = info.get("currency") or ("INR" if symbol.endswith((".NS", ".BO")) else "USD")
    change = (price - previous) if (price is not None and previous is not None) else None
    change_pct = (change / previous * 100) if (change is not None and previous) else None
    return {
        "symbol": symbol,
        "company": info.get("longName", info.get("shortName", symbol)),
        "price": price,
        "previousClose": previous,
        "change": change,
        "changePercent": change_pct,
        "currency": curr,
        "exchange": info.get("exchange", "N/A"),
        "dayHigh": getattr(fast, "day_high", None) or info.get("dayHigh"),
        "dayLow": getattr(fast, "day_low", None) or info.get("dayLow"),
        "marketCap": info.get("marketCap"),
    }


def get_historical_prices(symbol: str, range: str = "1mo") -> dict:
    """Get historical prices summary and closing range for a ticker."""
    symbol = validate_symbol(symbol)
    range_clean = range.lower().strip()
    period_map = {
        "1d": "1d",
        "1w": "5d",
        "5d": "5d",
        "1m": "1mo",
        "1mo": "1mo",
        "3m": "3mo",
        "3mo": "3mo",
        "6m": "6mo",
        "6mo": "6mo",
        "1y": "1y",
        "5y": "5y",
        "max": "max",
    }
    p = period_map.get(range_clean, "1mo")
    df = fetch_historical_prices(symbol, period=p)
    curr = "INR" if symbol.endswith((".NS", ".BO")) else "USD"
    recent = df.tail(10).reset_index()
    recent["date"] = recent["date"].astype(str)
    return {
        "symbol": symbol,
        "range": range,
        "currency": curr,
        "dataPoints": len(df),
        "startDate": str(df.index[0].date()) if not df.empty else None,
        "endDate": str(df.index[-1].date()) if not df.empty else None,
        "latestClose": float(df["close"].iloc[-1]) if not df.empty else None,
        "minClose": float(df["close"].min()) if not df.empty else None,
        "maxClose": float(df["close"].max()) if not df.empty else None,
        "recentPrices": recent.to_dict(orient="records"),
    }


def get_income_statement(symbol: str, period: str = "annual") -> dict:
    """Fetch annual or quarterly income statement metrics (revenue, operating income, net income, etc.)."""
    p = period.lower().strip()
    p_norm = "quarterly" if "quarter" in p else "annual"
    return fetch_financial_statements(symbol, statement="income", period=p_norm)


def get_balance_sheet(symbol: str, period: str = "annual") -> dict:
    """Fetch annual or quarterly balance sheet metrics (assets, liabilities, debt, equity, etc.)."""
    p = period.lower().strip()
    p_norm = "quarterly" if "quarter" in p else "annual"
    return fetch_financial_statements(symbol, statement="balance", period=p_norm)


def get_cash_flow_statement(symbol: str, period: str = "annual") -> dict:
    """Fetch annual or quarterly cash flow metrics (operating cash flow, capital expenditure, free cash flow, etc.)."""
    p = period.lower().strip()
    p_norm = "quarterly" if "quarter" in p else "annual"
    return fetch_financial_statements(symbol, statement="cashflow", period=p_norm)


def get_company_overview(symbol: str) -> dict:
    """Fetch company profile, business summary, industry, sector, and valuation metrics."""
    symbol = validate_symbol(symbol)
    ticker = yf.Ticker(symbol)
    info = ticker.info or {}
    curr = info.get("currency") or ("INR" if symbol.endswith((".NS", ".BO")) else "USD")
    return {
        "symbol": symbol,
        "companyName": info.get("longName", info.get("shortName", symbol)),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "exchange": info.get("exchange", "N/A"),
        "currency": curr,
        "marketCap": info.get("marketCap"),
        "peRatio": info.get("trailingPE"),
        "forwardPE": info.get("forwardPE"),
        "dividendYield": info.get("dividendYield"),
        "summary": (info.get("longBusinessSummary") or "")[:500],
    }
