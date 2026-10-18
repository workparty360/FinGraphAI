"""Financial calculations and year comparisons performed directly in Python."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def format_financial_amount(value: float | None, currency: str = "USD") -> str:
    """Format large currency values into human-readable Crores or Billions."""
    if value is None:
        return "N/A"
    curr_sym = "₹" if currency.upper() in ("INR", "RUPEES") else "$"
    abs_val = abs(value)
    sign = "-" if value < 0 else ""

    if currency.upper() == "INR" and abs_val >= 1e7:
        # Format Indian numbers in Crores (1 Cr = 10,000,000)
        cr = abs_val / 1e7
        return f"{sign}{curr_sym}{cr:,.2f} Cr"
    elif abs_val >= 1e9:
        return f"{sign}{curr_sym}{abs_val / 1e9:,.2f}B"
    elif abs_val >= 1e6:
        return f"{sign}{curr_sym}{abs_val / 1e6:,.2f}M"
    else:
        return f"{sign}{curr_sym}{abs_val:,.2f}"


def calculate_growth_rate(current: float | None, previous: float | None) -> dict[str, Any]:
    """Calculate absolute and percentage growth between two periods."""
    if current is None or previous is None or previous == 0:
        return {
            "abs_change": None,
            "pct_change": None,
            "formatted_pct": "N/A",
            "direction": "unchanged",
        }
    abs_change = current - previous
    pct_change = (abs_change / abs(previous)) * 100.0
    direction = "growth" if abs_change > 0 else ("decline" if abs_change < 0 else "flat")
    return {
        "abs_change": round(abs_change, 2),
        "pct_change": round(pct_change, 2),
        "formatted_pct": f"{pct_change:+.2f}%",
        "direction": direction,
    }


def calculate_margin(numerator: float | None, denominator: float | None) -> dict[str, Any]:
    """Calculate ratio percentage (e.g. Operating Margin, Net Profit Margin)."""
    if numerator is None or denominator is None or denominator == 0:
        return {"margin_pct": None, "formatted_pct": "N/A"}
    margin_pct = (numerator / denominator) * 100.0
    return {
        "margin_pct": round(margin_pct, 2),
        "formatted_pct": f"{margin_pct:.2f}%",
    }


def calculate_free_cash_flow(operating_cf: float | None, capex: float | None) -> dict[str, Any]:
    """Calculate Free Cash Flow: Operating Cash Flow - Capital Expenditures.

    Handles capex reported as either negative or positive in statements.
    """
    if operating_cf is None or capex is None:
        return {"fcf": None, "formatted": "N/A"}
    actual_fcf = operating_cf - abs(capex)
    return {
        "fcf": round(actual_fcf, 2),
        "formatted": str(actual_fcf),
    }


def calculate_roa(net_income: float | None, total_assets: float | None) -> dict[str, Any]:
    """Calculate Return on Assets (ROA) percentage."""
    if net_income is None or total_assets is None or total_assets == 0:
        return {"roa_pct": None, "formatted_pct": "N/A"}
    roa_pct = (net_income / total_assets) * 100.0
    return {
        "roa_pct": round(roa_pct, 2),
        "formatted_pct": f"{roa_pct:.2f}%",
    }


def calculate_roe(net_income: float | None, stockholders_equity: float | None) -> dict[str, Any]:
    """Calculate Return on Equity (ROE) percentage."""
    if net_income is None or stockholders_equity is None or stockholders_equity == 0:
        return {"roe_pct": None, "formatted_pct": "N/A"}
    roe_pct = (net_income / stockholders_equity) * 100.0
    return {
        "roe_pct": round(roe_pct, 2),
        "formatted_pct": f"{roe_pct:.2f}%",
    }


def calculate_current_ratio(
    current_assets: float | None, current_liabilities: float | None
) -> dict[str, Any]:
    """Calculate Current Ratio (Liquidity)."""
    if current_assets is None or current_liabilities is None or current_liabilities == 0:
        return {"ratio": None, "formatted": "N/A"}
    ratio = current_assets / current_liabilities
    return {
        "ratio": round(ratio, 2),
        "formatted": f"{ratio:.2f}",
    }


def calculate_debt_to_equity(
    total_debt: float | None, total_equity: float | None
) -> dict[str, Any]:
    """Calculate Debt-to-Equity Ratio (Solvency)."""
    if total_debt is None or total_equity is None or total_equity == 0:
        return {"ratio": None, "formatted": "N/A"}
    ratio = total_debt / total_equity
    return {
        "ratio": round(ratio, 2),
        "formatted": f"{ratio:.2f}",
    }


def analyze_best_performing_year(
    ticker: str,
    annual_financials: dict[int, dict[str, Any]] | None = None,
    fkg: Any | None = None,
) -> dict[str, Any]:
    """Perform deterministic multi-metric evaluation of annual financial performance across all available years.

    Evaluates Total Revenue, Net Income, Operating Margin, Free Cash Flow, and YoY Growth rates.
    Determines single-metric leaders, overall composite score, and produces a structured markdown table.
    """
    symbol = ticker.strip().upper()
    if annual_financials is None:
        if fkg is None:
            try:
                from financial_graph import get_financial_graph

                fkg = get_financial_graph()
            except Exception as exc:
                return {
                    "error": f"FalkorDB unavailable for historical analysis: {exc}",
                    "ticker": symbol,
                }
        annual_financials = fkg.get_annual_financials(symbol)

    if not annual_financials:
        return {
            "error": f"No annual financial data available for {symbol}.",
            "ticker": symbol,
            "years": [],
        }

    sorted_years = sorted(annual_financials.keys())
    if not sorted_years:
        return {"error": f"No fiscal years found for {symbol}.", "ticker": symbol, "years": []}

    currency = "USD"
    for y in sorted_years:
        c = annual_financials[y].get("currency")
        if c:
            currency = c
            break

    rows: list[dict[str, Any]] = []
    previous_revenue: float | None = None
    previous_net_income: float | None = None

    for yr in sorted_years:
        data = annual_financials[yr]
        revenue = data.get("Total Revenue") or data.get("revenue") or data.get("TotalRevenue")
        net = data.get("Net Income") or data.get("net_income") or data.get("NetIncome")
        operating_margin = data.get("Operating Margin") or data.get("operating_margin")
        fcf = data.get("Free Cash Flow") or data.get("free_cash_flow")

        if operating_margin is None and "Operating Income" in data and revenue:
            operating_income = data.get("Operating Income")
            if operating_income is not None:
                operating_margin = round((operating_income / revenue) * 100.0, 2)

        revenue_growth = (
            calculate_growth_rate(revenue, previous_revenue)
            if previous_revenue is not None
            else None
        )
        net_income_growth = (
            calculate_growth_rate(net, previous_net_income)
            if previous_net_income is not None
            else None
        )

        rows.append(
            {
                "year": yr,
                "revenue": revenue,
                "revenue_growth": revenue_growth,
                "net_income": net,
                "net_income_growth": net_income_growth,
                "operating_margin": operating_margin,
                "free_cash_flow": fcf,
            }
        )

        if revenue is not None:
            previous_revenue = revenue
        if net is not None:
            previous_net_income = net

    leaders: dict[str, Any] = {}

    metric_values = {}
    for metric, leader_name in (
        ("revenue", "highest_revenue"),
        ("net_income", "highest_net_income"),
        ("operating_margin", "highest_operating_margin"),
        ("free_cash_flow", "highest_free_cash_flow"),
    ):
        values = [(row["year"], row[metric]) for row in rows if row[metric] is not None]
        metric_values[metric] = values
        if values:
            year, value = max(values, key=lambda item: item[1])
            leaders[leader_name] = {
                "year": year,
                "value": value,
                "formatted": (
                    f"{value:.2f}%"
                    if metric == "operating_margin"
                    else format_financial_amount(value, currency)
                ),
            }

    valid_rev_growth = [
        (r["year"], r["revenue_growth"]["pct_change"])
        for r in rows
        if r["revenue_growth"] and r["revenue_growth"]["pct_change"] is not None
    ]
    if valid_rev_growth:
        best_rg_yr, best_rg_val = max(valid_rev_growth, key=lambda x: x[1])
        leaders["highest_revenue_growth"] = {
            "year": best_rg_yr,
            "value": best_rg_val,
            "formatted": f"{best_rg_val:+.2f}%",
        }

    valid_net_growth = [
        (r["year"], r["net_income_growth"]["pct_change"])
        for r in rows
        if r["net_income_growth"] and r["net_income_growth"]["pct_change"] is not None
    ]
    if valid_net_growth:
        best_ng_yr, best_ng_val = max(valid_net_growth, key=lambda x: x[1])
        leaders["highest_net_income_growth"] = {
            "year": best_ng_yr,
            "value": best_ng_val,
            "formatted": f"{best_ng_val:+.2f}%",
        }

    year_ranks: dict[int, list[int]] = {yr: [] for yr in sorted_years}

    for valid_list in metric_values.values():
        if valid_list:
            sorted_by_val = sorted(valid_list, key=lambda x: x[1], reverse=True)
            for rank, (yr, _) in enumerate(sorted_by_val, start=1):
                year_ranks[yr].append(rank)

    composite_scores: dict[int, float] = {}
    for yr, ranks in year_ranks.items():
        if ranks:
            composite_scores[yr] = round(sum(ranks) / len(ranks), 2)

    best_composite_year = (
        min(composite_scores, key=composite_scores.get) if composite_scores else sorted_years[-1]
    )

    table_lines = [
        "| Fiscal Year | Total Revenue | YoY Rev Growth | Net Income | YoY Net Income Growth | Operating Margin | Free Cash Flow |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for r in rows:
        yr = r["year"]
        rev_str = format_financial_amount(r["revenue"], currency)
        rg_str = r["revenue_growth"]["formatted_pct"] if r["revenue_growth"] else "-"
        net_str = format_financial_amount(r["net_income"], currency)
        ng_str = r["net_income_growth"]["formatted_pct"] if r["net_income_growth"] else "-"
        op_str = f"{r['operating_margin']:.2f}%" if r["operating_margin"] is not None else "N/A"
        fcf_str = (
            format_financial_amount(r["free_cash_flow"], currency)
            if r["free_cash_flow"] is not None
            else "N/A"
        )
        table_lines.append(
            f"| FY{yr} | {rev_str} | {rg_str} | {net_str} | {ng_str} | {op_str} | {fcf_str} |"
        )

    table_md = "\n".join(table_lines)

    summary_parts = []
    summary_parts.append(
        f"**Best Performance Overview for {symbol} (FY{sorted_years[0]}–FY{sorted_years[-1]}):**\n"
        f"Based on a comprehensive multi-metric evaluation across {len(sorted_years)} fiscal years, "
        f"**FY{best_composite_year}** emerged as {symbol}'s strongest financial year overall."
    )

    lead_lines = []
    if "highest_revenue" in leaders:
        lead_lines.append(
            f"• **Peak Revenue:** FY{leaders['highest_revenue']['year']} at {leaders['highest_revenue']['formatted']}"
        )
    if "highest_net_income" in leaders:
        lead_lines.append(
            f"• **Peak Net Income:** FY{leaders['highest_net_income']['year']} at {leaders['highest_net_income']['formatted']}"
        )
    if "highest_operating_margin" in leaders:
        lead_lines.append(
            f"• **Highest Operating Margin:** FY{leaders['highest_operating_margin']['year']} at {leaders['highest_operating_margin']['formatted']}"
        )
    if "highest_free_cash_flow" in leaders:
        lead_lines.append(
            f"• **Highest Free Cash Flow:** FY{leaders['highest_free_cash_flow']['year']} at {leaders['highest_free_cash_flow']['formatted']}"
        )

    if lead_lines:
        summary_parts.append("\n**Key Metric Highs:**\n" + "\n".join(lead_lines))

    summary_parts.append(f"\n**Annual Performance Comparison Table:**\n\n{table_md}")
    summary_text = "\n".join(summary_parts)

    return {
        "ticker": symbol,
        "currency": currency,
        "years": sorted_years,
        "best_composite_year": best_composite_year,
        "leaders": leaders,
        "rows": rows,
        "composite_scores": composite_scores,
        "comparison_table_markdown": table_md,
        "summary_text": summary_text,
    }


def compare_financial_performance(
    ticker: str,
    start_year: int,
    end_year: int,
    fkg: Any | None = None,
) -> dict[str, Any]:
    """Retrieve observations from FalkorDB and deterministically compute performance changes.

    Returns compact summary dictionary ready for direct user presentation or minimal LLM formatting.
    """
    if fkg is None:
        try:
            from financial_graph import get_financial_graph

            fkg = get_financial_graph()
        except Exception as exc:
            return {"error": f"FalkorDB unavailable for calculation: {exc}"}

    symbol = ticker.strip().upper()
    try:
        rev_start = fkg.get_company_financials(symbol, "Total Revenue", fiscal_year=start_year)
        rev_end = fkg.get_company_financials(symbol, "Total Revenue", fiscal_year=end_year)

        net_start = fkg.get_company_financials(symbol, "Net Income", fiscal_year=start_year)
        net_end = fkg.get_company_financials(symbol, "Net Income", fiscal_year=end_year)

        op_start = fkg.get_company_financials(symbol, "Operating Margin", fiscal_year=start_year)
        op_end = fkg.get_company_financials(symbol, "Operating Margin", fiscal_year=end_year)

        val_rev_start = rev_start[0]["value"] if rev_start else None
        val_rev_end = rev_end[0]["value"] if rev_end else None
        currency = (
            rev_start[0]["currency"]
            if rev_start
            else (rev_end[0]["currency"] if rev_end else "USD")
        )

        val_net_start = net_start[0]["value"] if net_start else None
        val_net_end = net_end[0]["value"] if net_end else None

        val_op_start = op_start[0]["value"] if op_start else None
        val_op_end = op_end[0]["value"] if op_end else None

        revenue_growth = calculate_growth_rate(val_rev_end, val_rev_start)
        net_income_growth = calculate_growth_rate(val_net_end, val_net_start)

        op_diff = (
            (val_op_end - val_op_start)
            if (val_op_end is not None and val_op_start is not None)
            else None
        )
        op_diff_bps = round(op_diff * 100, 0) if op_diff is not None else None

        summary = (
            f"{symbol} Performance Comparison (FY{start_year} ➔ FY{end_year}):\n"
            f"• Revenue: {format_financial_amount(val_rev_start, currency)} ➔ {format_financial_amount(val_rev_end, currency)} ({revenue_growth['formatted_pct']})\n"
        )
        if val_net_start is not None and val_net_end is not None:
            summary += f"• Net Income: {format_financial_amount(val_net_start, currency)} ➔ {format_financial_amount(val_net_end, currency)} ({net_income_growth['formatted_pct']})\n"
        if val_op_start is not None and val_op_end is not None:
            summary += f"• Operating Margin: {val_op_start:.2f}% ➔ {val_op_end:.2f}% ({op_diff:+.2f}%, {op_diff_bps:+.0f} bps)\n"

        comparison_table = []
        notes = []

        if val_rev_start is not None and val_rev_end is not None:
            comparison_table.append(
                {
                    "metric": "Total Revenue",
                    "start": val_rev_start,
                    "end": val_rev_end,
                    "start_formatted": format_financial_amount(val_rev_start, currency),
                    "end_formatted": format_financial_amount(val_rev_end, currency),
                    "change": revenue_growth["abs_change"],
                    "change_formatted": format_financial_amount(
                        revenue_growth["abs_change"], currency
                    ),
                    "pct_change": revenue_growth["pct_change"],
                    "pct_change_formatted": revenue_growth["formatted_pct"],
                }
            )
            notes.append(
                f"Revenue changed by {revenue_growth['formatted_pct']} ({format_financial_amount(revenue_growth['abs_change'], currency)})"
            )

        if val_net_start is not None and val_net_end is not None:
            comparison_table.append(
                {
                    "metric": "Net Income",
                    "start": val_net_start,
                    "end": val_net_end,
                    "start_formatted": format_financial_amount(val_net_start, currency),
                    "end_formatted": format_financial_amount(val_net_end, currency),
                    "change": net_income_growth["abs_change"],
                    "change_formatted": format_financial_amount(
                        net_income_growth["abs_change"], currency
                    ),
                    "pct_change": net_income_growth["pct_change"],
                    "pct_change_formatted": net_income_growth["formatted_pct"],
                }
            )
            notes.append(
                f"Net Income changed by {net_income_growth['formatted_pct']} ({format_financial_amount(net_income_growth['abs_change'], currency)})"
            )

        if val_op_start is not None and val_op_end is not None:
            comparison_table.append(
                {
                    "metric": "Operating Margin",
                    "start": val_op_start,
                    "end": val_op_end,
                    "start_formatted": f"{val_op_start:.2f}%",
                    "end_formatted": f"{val_op_end:.2f}%",
                    "change": op_diff,
                    "change_formatted": f"{op_diff:+.2f}%",
                    "pct_change": round(op_diff, 2) if op_diff is not None else None,
                    "pct_change_formatted": (
                        f"{op_diff:+.2f}% ({op_diff_bps:+.0f} bps)"
                        if op_diff_bps is not None
                        else "N/A"
                    ),
                }
            )
            notes.append(f"Operating Margin shifted by {op_diff:+.2f}% ({op_diff_bps:+.0f} bps)")

        return {
            "ticker": symbol,
            "start_year": start_year,
            "end_year": end_year,
            "currency": currency,
            "revenue": {
                "start": val_rev_start,
                "end": val_rev_end,
                "growth": revenue_growth,
            },
            "net_income": {
                "start": val_net_start,
                "end": val_net_end,
                "growth": net_income_growth,
            },
            "operating_margin": {
                "start": val_op_start,
                "end": val_op_end,
                "diff_pct": op_diff,
                "diff_bps": op_diff_bps,
            },
            "comparison_table": comparison_table,
            "notes": notes,
            "summary_text": summary.strip(),
        }
    except Exception as exc:
        logger.error(f"Error computing performance comparison for {symbol}: {exc}")
        return {"error": str(exc), "ticker": symbol}
