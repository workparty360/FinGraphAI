#!/usr/bin/env python3
"""
Verify Financial Knowledge Graph integrity, node counts, and Cypher traversals in FalkorDB.
"""

import logging
import sys
from financial_graph import get_financial_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_graph")


def verify():
    financial_graph = get_financial_graph()
    if not financial_graph.is_available:
        logger.error(
            f"Cannot connect to FalkorDB at {financial_graph.host}:{financial_graph.port}. Verification failed."
        )
        sys.exit(1)

    logger.info("=== 1. VERIFYING GRAPH STATISTICS ===")
    stats = financial_graph.get_graph_statistics()
    for entity, count in stats.items():
        logger.info(f"  {entity}: {count}")

    # Check minimum thresholds
    assert (
        stats.get("Company", 0) >= 7
    ), f"Expected at least 7 Company nodes, got {stats.get('Company')}"
    assert (
        stats.get("FinancialObservation", 0) >= 30
    ), f"Expected at least 30 FinancialObservation nodes, got {stats.get('FinancialObservation')}"
    assert (
        stats.get("SourceDocument", 0) >= 10
    ), f"Expected at least 10 SourceDocument nodes, got {stats.get('SourceDocument')}"
    assert stats.get("REL_HAS_OBSERVATION", 0) >= 30, "Missing HAS_OBSERVATION relationships"
    assert stats.get("REL_SUPPORTED_BY", 0) >= 30, "Missing SUPPORTED_BY relationships"
    assert stats.get("REL_BELONGS_TO", 0) >= 7, "Missing BELONGS_TO relationships"

    logger.info("=== 2. VERIFYING COMPANY LOOKUP ===")
    aapl = financial_graph.find_company("AAPL")
    assert aapl is not None, "Failed to find company AAPL"
    logger.info(
        f"Found AAPL: {aapl['name']} | Sector: {aapl['sector']} | Country: {aapl['country']}"
    )

    reliance = financial_graph.find_company("RELIANCE.NS")
    assert reliance is not None, "Failed to find company RELIANCE.NS"
    logger.info(f"Found RELIANCE.NS: {reliance['name']} | Sector: {reliance['sector']}")

    logger.info("=== 3. VERIFYING HISTORICAL REVENUE RETRIEVAL ===")
    apple_revenues = financial_graph.get_company_financials("AAPL", "Total Revenue")
    assert (
        len(apple_revenues) >= 4
    ), f"Expected >= 4 revenue observations for AAPL, got {len(apple_revenues)}"
    for r in apple_revenues:
        logger.info(
            f"  AAPL FY{r['fiscal_year']}: ${r['value']/1e9:,.2f}B (Source: {r['source_title']})"
        )

    logger.info("=== 4. VERIFYING CROSS-COMPANY COMPARISON (AAPL vs MSFT) ===")
    comparison = financial_graph.compare_company_metrics(["AAPL", "MSFT"], "Total Revenue")
    assert len(comparison) >= 8, f"Expected >= 8 comparison points, got {len(comparison)}"
    logger.info(f"Retrieved {len(comparison)} comparison data points between AAPL and MSFT.")

    logger.info("=== 5. VERIFYING SECTOR PEER DISCOVERY ===")
    peers = financial_graph.get_sector_peers("AAPL")
    assert len(peers) >= 2, f"Expected sector peers for AAPL in Technology, got {len(peers)}"
    peer_tickers = [p["ticker"] for p in peers]
    logger.info(f"AAPL Sector Peers in Technology: {peer_tickers}")
    assert "MSFT" in peer_tickers
    assert "NVDA" in peer_tickers

    logger.info("=== 6. VERIFYING EVIDENCE PROVENANCE PATH ===")
    evidence = financial_graph.trace_evidence_path("AAPL", "Total Revenue", 2024)
    assert evidence is not None, "Evidence path for AAPL FY2024 Total Revenue not found"
    logger.info(f"Evidence for AAPL FY2024 Revenue: Value = ${evidence['value']/1e9:,.2f}B")
    logger.info(f"  Source Document: {evidence['document_title']}")
    logger.info(f"  Public URL: {evidence['document_url']}")
    logger.info(f"  Full Provenance Path: {evidence['evidence_path']}")

    logger.info("=== 7. VERIFYING PEER METRIC COMPARISON (Operating Margin) ===")
    peer_margins = financial_graph.get_peer_metric_comparison(
        "AAPL", "Operating Margin", fiscal_year=2024
    )
    assert len(peer_margins) >= 3, f"Expected peer margin comparisons, got {len(peer_margins)}"
    for peer_margin in peer_margins:
        target_mark = " (TARGET)" if peer_margin["is_target"] else ""
        logger.info(f"  {peer_margin['ticker']}: {peer_margin['value']:.1f}%{target_mark}")

    logger.info("✅ ALL GRAPH VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    return True


if __name__ == "__main__":
    verify()
