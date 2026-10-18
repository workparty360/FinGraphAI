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
    fkg = get_financial_graph()
    if not fkg.is_available:
        logger.error(f"Cannot connect to FalkorDB at {fkg.host}:{fkg.port}. Verification failed.")
        sys.exit(1)

    logger.info("=== 1. VERIFYING GRAPH STATISTICS ===")
    stats = fkg.get_graph_statistics()
    for entity, count in stats.items():
        logger.info(f"  {entity}: {count}")

    # Check minimum thresholds
    assert stats.get("Company", 0) >= 7, f"Expected at least 7 Company nodes, got {stats.get('Company')}"
    assert stats.get("FinancialObservation", 0) >= 30, f"Expected at least 30 FinancialObservation nodes, got {stats.get('FinancialObservation')}"
    assert stats.get("SourceDocument", 0) >= 10, f"Expected at least 10 SourceDocument nodes, got {stats.get('SourceDocument')}"
    assert stats.get("REL_HAS_OBSERVATION", 0) >= 30, "Missing HAS_OBSERVATION relationships"
    assert stats.get("REL_SUPPORTED_BY", 0) >= 30, "Missing SUPPORTED_BY relationships"
    assert stats.get("REL_BELONGS_TO", 0) >= 7, "Missing BELONGS_TO relationships"

    logger.info("=== 2. VERIFYING COMPANY LOOKUP ===")
    aapl = fkg.find_company("AAPL")
    assert aapl is not None, "Failed to find company AAPL"
    logger.info(f"Found AAPL: {aapl['name']} | Sector: {aapl['sector']} | Country: {aapl['country']}")

    reliance = fkg.find_company("RELIANCE.NS")
    assert reliance is not None, "Failed to find company RELIANCE.NS"
    logger.info(f"Found RELIANCE.NS: {reliance['name']} | Sector: {reliance['sector']}")

    logger.info("=== 3. VERIFYING HISTORICAL REVENUE RETRIEVAL ===")
    aapl_revs = fkg.get_company_financials("AAPL", "Total Revenue")
    assert len(aapl_revs) >= 4, f"Expected >= 4 revenue observations for AAPL, got {len(aapl_revs)}"
    for r in aapl_revs:
        logger.info(f"  AAPL FY{r['fiscal_year']}: ${r['value']/1e9:,.2f}B (Source: {r['source_title']})")

    logger.info("=== 4. VERIFYING CROSS-COMPANY COMPARISON (AAPL vs MSFT) ===")
    comp = fkg.compare_company_metrics(["AAPL", "MSFT"], "Total Revenue")
    assert len(comp) >= 8, f"Expected >= 8 comparison points, got {len(comp)}"
    logger.info(f"Retrieved {len(comp)} comparison data points between AAPL and MSFT.")

    logger.info("=== 5. VERIFYING SECTOR PEER DISCOVERY ===")
    peers = fkg.get_sector_peers("AAPL")
    assert len(peers) >= 2, f"Expected sector peers for AAPL in Technology, got {len(peers)}"
    peer_tickers = [p["ticker"] for p in peers]
    logger.info(f"AAPL Sector Peers in Technology: {peer_tickers}")
    assert "MSFT" in peer_tickers
    assert "NVDA" in peer_tickers

    logger.info("=== 6. VERIFYING EVIDENCE PROVENANCE PATH ===")
    ev = fkg.trace_evidence_path("AAPL", "Total Revenue", 2024)
    assert ev is not None, "Evidence path for AAPL FY2024 Total Revenue not found"
    logger.info(f"Evidence for AAPL FY2024 Revenue: Value = ${ev['value']/1e9:,.2f}B")
    logger.info(f"  Source Document: {ev['document_title']}")
    logger.info(f"  Public URL: {ev['document_url']}")
    logger.info(f"  Full Provenance Path: {ev['evidence_path']}")

    logger.info("=== 7. VERIFYING PEER METRIC COMPARISON (Operating Margin) ===")
    peer_margins = fkg.get_peer_metric_comparison("AAPL", "Operating Margin", fiscal_year=2024)
    assert len(peer_margins) >= 3, f"Expected peer margin comparisons, got {len(peer_margins)}"
    for pm in peer_margins:
        target_mark = " (TARGET)" if pm["is_target"] else ""
        logger.info(f"  {pm['ticker']}: {pm['value']:.1f}%{target_mark}")

    logger.info("✅ ALL GRAPH VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    return True


if __name__ == "__main__":
    verify()
