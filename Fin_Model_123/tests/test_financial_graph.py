"""Comprehensive tests for FalkorDB Financial Knowledge Graph,

Graph-Powered Financial Intelligence, Evidence Provenance,
and Hard FalkorDB Dependency Validation.

Covers Hackathon Rules 2, 3, 5, 6, 7, and Sections 3, 4, 5, 6, 7, 8, 20.
"""
from __future__ import annotations

import os
import pytest

from financial_graph import (
    FinancialKnowledgeGraph,
    FalkorDBUnavailableError,
    get_financial_graph,
)
from memory_agents import deterministic_fetch_financial_data


@pytest.fixture(scope="module")
def live_graph():
    """Returns connected FinancialKnowledgeGraph instance."""
    try:
        fkg = FinancialKnowledgeGraph()
        fkg.ro_query("MATCH (c:Company) RETURN count(c) LIMIT 1")
        return fkg
    except Exception as exc:
        pytest.skip(f"Live FalkorDB instance not available: {exc}")


# =============================================================================
# 1. CORE GRAPH SCHEMA & STATISTICS TESTS
# =============================================================================

def test_graph_statistics_and_schema(live_graph):
    """Verify that all required node labels and relationship types exist in FalkorDB."""
    stats = live_graph.get_graph_statistics()

    # Required node labels
    assert stats.get("Company", 0) >= 5, "Expected at least 5 companies in graph"
    assert stats.get("Sector", 0) >= 2, "Expected at least 2 sectors in graph"
    assert stats.get("Industry", 0) >= 2, "Expected at least 2 industries in graph"
    assert stats.get("FinancialMetric", 0) >= 4, "Expected at least 4 metrics in graph"
    assert stats.get("FinancialObservation", 0) >= 20, "Expected at least 20 observations in graph"
    assert stats.get("ReportingPeriod", 0) >= 10, "Expected reporting periods"
    assert stats.get("FinancialStatement", 0) >= 10, "Expected financial statements"
    assert stats.get("SourceDocument", 0) >= 5, "Expected verified source documents"

    # Required relationship types
    required_rels = [
        "REL_BELONGS_TO",
        "REL_OPERATES_IN",
        "REL_REPORTS",
        "REL_FOR_PERIOD",
        "REL_CONTAINS",
        "REL_MEASURES",
        "REL_SUPPORTED_BY",
        "REL_HAS_OBSERVATION",
    ]
    for rel in required_rels:
        assert stats.get(rel, 0) > 0, f"Relationship {rel} has 0 instances in graph"


def test_find_company_node_resolution(live_graph):
    """STEP 1 in Graph Workflow: Resolve ticker to Company node with Sector & Industry."""
    res_aapl = live_graph.find_company("AAPL")
    assert res_aapl is not None
    assert res_aapl["name"] == "Apple Inc."
    assert res_aapl["sector"] == "Technology"
    assert res_aapl["country"] == "USA"

    res_rel = live_graph.find_company("RELIANCE.NS")
    assert res_rel is not None
    assert "Reliance" in res_rel["name"]
    assert res_rel["sector"] == "Energy"
    assert res_rel["country"] in ("IND", "India")


# =============================================================================
# 2. FEATURE 1: CROSS-COMPANY FINANCIAL COMPARISON
# =============================================================================

def test_cross_company_comparison_aapl_vs_msft(live_graph):
    """Verify cross-company comparison traverses relationships to compare revenue."""
    comparison = live_graph.compare_company_metrics(
        ["AAPL", "MSFT"],
        metric_name="Total Revenue",
        fiscal_year=2024,
    )
    assert len(comparison) >= 2, "Expected observations for both AAPL and MSFT"

    tickers = {c["ticker"] for c in comparison}
    assert "AAPL" in tickers
    assert "MSFT" in tickers

    for entry in comparison:
        assert entry["metric"] == "Total Revenue"
        assert entry["fiscal_year"] == 2024
        assert entry["value"] > 0
        assert entry["currency"] == "USD"
        assert entry.get("source_title") or entry.get("document_title"), "Each entry must include source provenance"


def test_cross_company_multi_year_history(live_graph):
    """Verify multi-year financial history retrieval for growth rate calculations."""
    history = live_graph.get_company_financial_history("AAPL", period_type="annual")
    assert history["ticker"] == "AAPL"
    assert "Total Revenue" in history["metrics"]

    rev_history = history["metrics"]["Total Revenue"]
    assert len(rev_history) >= 4, "Expected at least 4 years of revenue observations"

    years = [h["year"] for h in rev_history]
    assert sorted(years) == years, "Observations must be chronologically ordered"


# =============================================================================
# 3. FEATURE 2: SECTOR-BASED FINANCIAL DISCOVERY
# =============================================================================

def test_sector_peer_discovery(live_graph):
    """Verify traversing (:Company)-[:BELONGS_TO]->(:Sector)<-[:BELONGS_TO]-(:Company)."""
    peers = live_graph.get_sector_peers("AAPL")
    peer_tickers = [p["ticker"] for p in peers]
    assert "MSFT" in peer_tickers, "MSFT must be a Technology peer of AAPL"
    assert "NVDA" in peer_tickers, "NVDA must be a Technology peer of AAPL"
    assert "AAPL" not in peer_tickers, "Target company must not appear in its own peer list"


def test_peer_metric_comparison_operating_margin(live_graph):
    """Verify comparing operating margins across sector peers from FalkorDB."""
    peer_margin = live_graph.get_peer_metric_comparison(
        "AAPL",
        metric_name="Operating Margin",
        fiscal_year=2024,
    )
    assert len(peer_margin) >= 3, "Expected at least 3 peer records"

    target_entry = next((p for p in peer_margin if p["is_target"]), None)
    assert target_entry is not None
    assert target_entry["ticker"] == "AAPL"
    assert target_entry["value"] == pytest.approx(31.5, abs=1.0)


# =============================================================================
# 4. FEATURE 3: FINANCIAL EVIDENCE & PROVENANCE TRACING
# =============================================================================

def test_evidence_provenance_tracing_with_public_urls(live_graph):
    """Verify observation-to-source traversal returns valid public document citation."""
    evidence = live_graph.trace_evidence_path("AAPL", "Total Revenue", fiscal_year=2024)
    assert evidence is not None

    assert evidence["company_ticker"] == "AAPL"
    assert evidence["metric_name"] == "Total Revenue"
    assert evidence["fiscal_year"] == 2024
    assert evidence["value"] > 3e11  # Apple revenue is > $300B

    # Check verified public citation metadata
    title = evidence.get("source_title") or evidence.get("document_title") or ""
    assert "Form 10-K" in title or "10-K" in title
    url = evidence.get("source_url") or evidence.get("document_url") or ""
    assert url.startswith("https://www.sec.gov/")
    license_str = evidence.get("source_license", "")
    assert "Public" in license_str


def test_indian_company_evidence_provenance(live_graph):
    """Verify Indian company (Reliance / TCS) provenance cites official public filings."""
    evidence = live_graph.trace_evidence_path("RELIANCE.NS", "Total Revenue", fiscal_year=2024)
    assert evidence is not None

    assert evidence["company_ticker"] == "RELIANCE.NS"
    assert evidence["currency"] == "INR"
    url = evidence.get("source_url") or evidence.get("document_url") or ""
    assert "bseindia.com" in url or "ril.com" in url


# =============================================================================
# 5. HARD FALKORDB DEPENDENCY & DISCONNECTION TESTS (TEST B & TEST C)
# =============================================================================

def test_falkordb_disconnection_causes_explicit_failure():
    """TEST B: Disconnecting FalkorDB causes graph queries to raise FalkorDBUnavailableError.

    Verifies that the application does NOT silently substitute an unrelated database
    or fabricate graph results.
    """
    offline_graph = FinancialKnowledgeGraph(host="127.0.0.1", port=59999)  # Non-existent port
    assert not offline_graph.is_available

    with pytest.raises(FalkorDBUnavailableError):
        offline_graph.find_company("AAPL")

    with pytest.raises(FalkorDBUnavailableError):
        offline_graph.compare_company_metrics(["AAPL", "MSFT"], "Total Revenue")

    with pytest.raises(FalkorDBUnavailableError):
        offline_graph.get_sector_peers("AAPL")

    with pytest.raises(FalkorDBUnavailableError):
        offline_graph.trace_evidence_path("AAPL", "Total Revenue")


def test_deterministic_fetch_graph_error_when_offline(monkeypatch):
    """Verify deterministic_fetch_financial_data reports graph_error when FalkorDB is offline."""
    offline_graph = FinancialKnowledgeGraph(host="127.0.0.1", port=59999)

    monkeypatch.setattr("financial_graph.get_financial_graph", lambda *args, **kwargs: offline_graph)

    res = deterministic_fetch_financial_data(
        "Compare Apple and Microsoft revenue growth",
        "AAPL",
    )
    assert "graph_error" in res
    assert "temporarily unavailable" in res["graph_error"].lower()
    assert "graph_cross_company_revenue" not in res


def test_reconnection_restores_functionality(live_graph):
    """TEST C: Reconnecting FalkorDB restores graph-powered intelligence."""
    assert live_graph.is_available
    res = live_graph.find_company("AAPL")
    assert res is not None
    assert res["ticker"] == "AAPL"


# =============================================================================
# 6. INTEGRATION WITH DETERMINISTIC FETCHING & AI ROUTING
# =============================================================================

def test_deterministic_fetch_routes_cross_company_comparison(live_graph):
    """Verify user query 'Compare AAPL and MSFT revenue' routes to FalkorDB graph."""
    res = deterministic_fetch_financial_data(
        "Compare AAPL and MSFT revenue growth in 2024",
        "AAPL",
    )
    assert "graph_cross_company_revenue" in res
    comp_data = res["graph_cross_company_revenue"]
    assert len(comp_data) >= 2


def test_deterministic_fetch_routes_sector_peers(live_graph):
    """Verify user query 'peers' routes to FalkorDB sector peer traversal."""
    res = deterministic_fetch_financial_data(
        "Which sector peers have higher operating margins than AAPL?",
        "AAPL",
    )
    assert "graph_sector_peers" in res
    peers_list = res["graph_sector_peers"]
    assert len(peers_list) > 0


def test_deterministic_fetch_routes_evidence_path(live_graph):
    """Verify user query 'evidence' routes to FalkorDB provenance tracing."""
    res = deterministic_fetch_financial_data(
        "Where did Apple's 2024 net income number come from? Show evidence.",
        "AAPL",
    )
    assert "graph_evidence_path" in res
    ev = res["graph_evidence_path"]
    assert ev.get("source_title") or ev.get("document_title")


# =============================================================================
# 7. INTEGRITY, ISOLATION & SECURITY CHECKS
# =============================================================================

def test_fiscal_year_and_period_type_integrity(live_graph):
    """Verify that annual and quarterly observations are never silently mixed."""
    annual_obs = live_graph.get_company_financials("AAPL", "Total Revenue", period_type="annual")
    for o in annual_obs:
        assert o["period_type"] == "annual"
        assert o["fiscal_year"] is not None


def test_parameterized_queries_prevent_injection(live_graph):
    """Verify Cypher query parameterization safely handles malicious inputs."""
    injection_payload = "AAPL' OR 1=1 RETURN * //"
    res = live_graph.find_company(injection_payload)
    assert res is None
