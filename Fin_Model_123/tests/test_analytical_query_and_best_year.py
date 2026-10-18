"""Comprehensive test suite for analytical multi-year financial queries,
best-performing year evaluation, multi-turn follow-ups, provenance tracing,
and rate-limit immunity in FalkorDB.
"""
from __future__ import annotations

import pytest
from falkor_memory import FalkorMemory
from financial_graph import get_financial_graph
from financial_math import (
    analyze_best_performing_year,
    compare_financial_performance,
    format_financial_amount,
)
from memory_agents import (
    FinanceAgentTeam,
    deterministic_fetch_financial_data,
    plan_financial_query,
)


class DummyEmbeddings:
    def embed_query(self, text: str) -> list[float]:
        return [0.0] * 384


class MockDeterministicLLM:
    """Mock simulating quota-exhausted or offline LLM to test deterministic execution."""
    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages):
        raise RuntimeError("Groq daily organization quota reached (TPD). Routing to deterministic FalkorDB retrieval.")


@pytest.fixture(scope="module")
def shared_memory():
    """Initializes a shared FalkorMemory instance for the analytical test suite."""
    return FalkorMemory("analytical_tenant", "analyst_user", DummyEmbeddings())


# =============================================================================
# 1. APPLE FY2023 NET INCOME LOOKUP WITH FORM 10-K PROVENANCE
# =============================================================================
def test_1_apple_fy2023_net_income_lookup_with_provenance(shared_memory):
    """Verify Apple FY2023 Net Income is accurately retrieved with Form 10-K provenance and public SEC URL."""
    fkg = get_financial_graph()
    ev = fkg.trace_evidence_path("AAPL", "Net Income", 2023)
    assert ev is not None
    assert ev["company_ticker"] == "AAPL"
    assert ev["fiscal_year"] == 2023
    assert ev["metric_name"] == "Net Income"
    assert ev["value"] == 96995000000.0
    assert "Form 10-K" in ev["document_title"]
    assert "sec.gov" in ev["document_url"]
    assert "FinancialObservation" in ev["evidence_path"]

    team = FinanceAgentTeam(shared_memory, MockDeterministicLLM())
    res = team.ask("What was Apple's net income in 2023 and where did that come from?", thread_id="th_test1")
    ans = res["answer"]
    assert res["ticker"] == "AAPL"
    assert "$97.00B" in ans or "96,995,000,000" in ans
    assert "Form 10-K" in ans
    assert "sec.gov" in ans
    assert "Graph Provenance Path" in ans


# =============================================================================
# 2. APPLE HIGHEST NET INCOME YEAR IDENTIFICATION
# =============================================================================
def test_2_apple_highest_net_income_year_identification(shared_memory):
    """Verify system accurately identifies FY2025 ($112.01B) as Apple's highest net income year."""
    fkg = get_financial_graph()
    analysis = analyze_best_performing_year("AAPL", fkg=fkg)
    highest_net = analysis["leaders"]["highest_net_income"]
    assert highest_net["year"] == 2025
    assert highest_net["value"] == 112010000000.0
    assert "$112.01B" in highest_net["formatted"]

    team = FinanceAgentTeam(shared_memory, MockDeterministicLLM())
    res = team.ask("Which year had the highest net income for Apple?", thread_id="th_test2")
    ans = res["answer"]
    assert res["ticker"] == "AAPL"
    assert "FY2025" in ans
    assert "$112.01B" in ans
    assert "Annual Performance Comparison Table" in ans or "Multi-Year Financial Performance Overview" in ans


# =============================================================================
# 3. APPLE BEST YEAR OVERALL PERFORMANCE EVALUATION
# =============================================================================
def test_3_apple_best_year_overall_performance_evaluation(shared_memory):
    """Verify multi-metric evaluation names FY2025 as Apple's best year overall with structured comparison table."""
    team = FinanceAgentTeam(shared_memory, MockDeterministicLLM())
    res = team.ask("Which year was Apple's best performance-wise?", thread_id="th_test3")
    ans = res["answer"]
    assert res["ticker"] == "AAPL"
    assert "FY2025" in ans
    assert "Peak Revenue" in ans
    assert "$416.16B" in ans
    assert "Peak Net Income" in ans
    assert "$112.01B" in ans
    assert "Highest Operating Margin" in ans
    assert "32.10%" in ans
    assert "| Fiscal Year | Total Revenue |" in ans
    assert "| FY2021 |" in ans
    assert "| FY2025 |" in ans


# =============================================================================
# 4. APPLE 2022 VS 2023 PERFORMANCE COMPARISON
# =============================================================================
def test_4_apple_2022_vs_2023_performance_comparison(shared_memory):
    """Verify deterministic 2-year comparison calculates exact revenue, net income, and operating margin growth."""
    fkg = get_financial_graph()
    comp = compare_financial_performance("AAPL", 2022, 2023, fkg=fkg)
    assert comp["start_year"] == 2022
    assert comp["end_year"] == 2023
    assert comp["revenue"]["growth"]["pct_change"] == -2.8
    assert comp["net_income"]["growth"]["pct_change"] == -2.81
    assert comp["operating_margin"]["diff_pct"] == -0.5

    team = FinanceAgentTeam(shared_memory, MockDeterministicLLM())
    res = team.ask("Compare Apple performance in 2022 vs 2023", thread_id="th_test4")
    ans = res["answer"]
    assert res["ticker"] == "AAPL"
    assert "$394.33B" in ans
    assert "$383.29B" in ans
    assert "-2.80%" in ans
    assert "-2.81%" in ans
    assert "-0.50%" in ans


# =============================================================================
# 5. MULTI-TURN FOLLOW-UP SEQUENCE
# =============================================================================
def test_5_multi_turn_follow_up_sequence(shared_memory):
    """Verify seamless multi-turn reasoning: Turn 1 (2023 overview) -> Turn 2 (2024 comparison) -> Turn 3 (profitability comparison)."""
    thread = "th_multiturn_seq_full"
    team = FinanceAgentTeam(shared_memory, MockDeterministicLLM())

    # Turn 1: Analyze Apple in 2023
    res1 = team.ask("Analyze Apple in 2023", thread_id=thread)
    ans1 = res1["answer"]
    assert res1["ticker"] == "AAPL"
    assert "FY2023" in ans1
    assert "$383.29B" in ans1  # Revenue
    assert "$97.00B" in ans1   # Net Income
    assert "29.80%" in ans1    # Operating Margin
    assert "$99.58B" in ans1   # Free Cash Flow

    # Turn 2: What about 2024?
    res2 = team.ask("What about 2024?", thread_id=thread)
    ans2 = res2["answer"]
    assert res2["ticker"] == "AAPL"
    assert "FY2023 ➔ FY2024" in ans2 or "FY2023 vs FY2024" in ans2
    assert "$383.29B ➔ $391.04B" in ans2
    assert "+2.02%" in ans2
    assert "$97.00B ➔ $93.74B" in ans2
    assert "-3.36%" in ans2
    assert "29.80% ➔ 31.50%" in ans2

    # Turn 3: Which of those two was more profitable?
    res3 = team.ask("Which of those two was more profitable?", thread_id=thread)
    ans3 = res3["answer"]
    assert res3["ticker"] == "AAPL"
    assert "FY2023" in ans3
    assert "FY2024" in ans3
    assert "$97.00B" in ans3
    assert "$93.74B" in ans3
    assert "FY2023" in ans3 and "higher net income" in ans3
    assert "FY2024" in ans3 and "higher operating margin" in ans3


# =============================================================================
# 6. INDIAN STOCK RELIANCE COMPARISON IN CRORES
# =============================================================================
def test_6_indian_stock_reliance_comparison_in_crores(shared_memory):
    """Verify Indian stock queries preserve INR currency and format in Crores."""
    team = FinanceAgentTeam(shared_memory, MockDeterministicLLM())
    res = team.ask("Compare Reliance performance in 2023 vs 2024", thread_id="th_test6")
    ans = res["answer"]
    assert res["ticker"] == "RELIANCE.NS"
    assert "₹974,864.00 Cr" in ans
    assert "₹1,057,219.00 Cr" in ans
    assert "+8.45%" in ans
    assert "₹74,088.00 Cr" in ans
    assert "₹80,775.00 Cr" in ans
    assert "+9.03%" in ans
    assert "15.60% ➔ 16.80%" in ans


# =============================================================================
# 7. CROSS-COMPANY COMPARISON (APPLE VS MICROSOFT)
# =============================================================================
def test_7_cross_company_metric_comparison_aapl_vs_msft(shared_memory):
    """Verify cross-company revenue comparison retrieves both companies from FalkorDB."""
    fkg = get_financial_graph()
    cross = fkg.compare_company_metrics(["AAPL", "MSFT"], "Total Revenue")
    tickers_retrieved = {c["ticker"] for c in cross}
    assert "AAPL" in tickers_retrieved
    assert "MSFT" in tickers_retrieved

    data = deterministic_fetch_financial_data("Compare Apple and Microsoft revenue", "AAPL")
    assert "graph_cross_company_revenue" in data
    found_tickers = {r["ticker"] for r in data["graph_cross_company_revenue"]}
    assert "AAPL" in found_tickers
    assert "MSFT" in found_tickers


# =============================================================================
# 8. PEER OPERATING MARGIN COMPARISON
# =============================================================================
def test_8_peer_operating_margin_comparison(shared_memory):
    """Verify peer discovery and metric comparison returns sector peers with operating margins."""
    fkg = get_financial_graph()
    peers = fkg.get_peer_metric_comparison("AAPL", "Operating Margin")
    assert len(peers) >= 2
    peer_tickers = {p["ticker"] for p in peers}
    assert "MSFT" in peer_tickers or "GOOGL" in peer_tickers
    for p in peers:
        assert p["value"] is not None


# =============================================================================
# 9. SESSION PERSISTENCE ACROSS AGENT INSTANTIATIONS
# =============================================================================
def test_9_session_persistence_across_agent_instantiations(shared_memory):
    """Verify conversation context persists across different FinanceAgentTeam instances via FalkorDB thread storage."""
    thread_persist = "th_session_cross_inst"

    # Agent Instance 1
    team1 = FinanceAgentTeam(shared_memory, MockDeterministicLLM())
    r1 = team1.ask("Analyze Apple in 2023", thread_id=thread_persist)
    assert r1["ticker"] == "AAPL"

    # Agent Instance 2 (completely new object, same thread)
    team2 = FinanceAgentTeam(shared_memory, MockDeterministicLLM())
    r2 = team2.ask("What about 2024?", thread_id=thread_persist)
    assert r2["ticker"] == "AAPL"
    assert "FY2023" in r2["answer"] and "FY2024" in r2["answer"]

    # Agent Instance 3 (completely new object, same thread)
    team3 = FinanceAgentTeam(shared_memory, MockDeterministicLLM())
    r3 = team3.ask("Which of those two was more profitable?", thread_id=thread_persist)
    assert r3["ticker"] == "AAPL"
    assert "FY2023" in r3["answer"] and "FY2024" in r3["answer"]
    assert "$97.00B" in r3["answer"]
    assert "$93.74B" in r3["answer"]


# =============================================================================
# 10. RATE-LIMIT IMMUNITY & DETERMINISTIC MATH EXECUTION
# =============================================================================
def test_10_rate_limit_immunity_deterministic_execution(shared_memory):
    """Verify analytical queries never fail or return 'Not enough data available' when LLM hits quota."""
    team = FinanceAgentTeam(shared_memory, MockDeterministicLLM())

    queries = [
        "Which year was Apple's best performance-wise?",
        "Which year had the highest net income for Apple?",
        "Compare Apple performance in 2022 vs 2023",
        "Analyze Apple in 2023",
        "Compare Reliance performance in 2023 vs 2024",
    ]

    for q in queries:
        res = team.ask(q, thread_id="th_rate_limit_immune")
        ans = res["answer"]
        assert ans is not None
        assert len(ans.strip()) > 50
        assert "Not enough data available" not in ans
        assert "Unable to complete financial analysis" not in ans
