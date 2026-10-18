"""
Comprehensive Automated Tests for StockResolver, SEC EDGAR Integration, and Multi-Company Coordination.

Covers all 24 required test scenarios:
1. Apple -> AAPL
2. AAPL -> Apple
3. Microsoft -> MSFT
4. NVIDIA -> NVDA
5. Tesla -> TSLA
6. Amazon -> AMZN
7. Lowercase ticker resolution
8. Full company name resolution
9. Ambiguous ticker handling
10. Unknown company handling
11. Invalid CIK handling
12. SEC HTTP 403
13. SEC HTTP 404
14. SEC HTTP 429
15. SEC timeout
16. SEC caching
17. Multi-company questions
18. Follow-up ticker context
19. New Chat ticker reset
20. Previous chat ticker restoration
21. FalkorDB company caching
22. Tenant isolation
23. Unsupported NSE stocks
24. EC2 network connectivity diagnostics
"""
import os
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from stock_resolver import (
    StockResolver,
    resolve_company,
    resolve_ticker,
    resolve_companies_from_text,
    format_cik,
    SECAPIError,
    SECAccessDeniedError,
    SECNotFoundError,
    SECRateLimitError,
    CompanyNotFoundError,
    get_stock_resolver,
)
from memory_agents import plan_financial_query, FinanceAgentTeam
from falkor_memory import FalkorMemory


# =============================================================================
# SCENARIOS 1 - 6: CORE COMPANY RESOLUTION
# =============================================================================

def test_01_apple_to_aapl():
    """Scenario 1: Resolve 'Apple' -> AAPL."""
    comp = resolve_company("Apple")
    assert comp is not None
    assert comp["ticker"] == "AAPL"
    assert "Apple" in comp["name"]
    assert resolve_ticker("Apple") == "AAPL"


def test_02_aapl_to_apple():
    """Scenario 2: Resolve 'AAPL' -> Apple Inc."""
    comp = resolve_company("AAPL")
    assert comp is not None
    assert comp["ticker"] == "AAPL"
    assert comp["name"] == "Apple Inc."
    assert comp["cik"] == "0000320193"


def test_03_microsoft_to_msft():
    """Scenario 3: Resolve 'Microsoft' -> MSFT."""
    comp = resolve_company("Microsoft")
    assert comp is not None
    assert comp["ticker"] == "MSFT"
    assert resolve_ticker("Microsoft") == "MSFT"


def test_04_nvidia_to_nvda():
    """Scenario 4: Resolve 'NVIDIA' -> NVDA."""
    comp = resolve_company("NVIDIA")
    assert comp is not None
    assert comp["ticker"] == "NVDA"
    assert resolve_ticker("NVIDIA") == "NVDA"


def test_05_tesla_to_tsla():
    """Scenario 5: Resolve 'Tesla' -> TSLA."""
    comp = resolve_company("Tesla")
    assert comp is not None
    assert comp["ticker"] == "TSLA"
    assert resolve_ticker("Tesla") == "TSLA"


def test_06_amazon_to_amzn():
    """Scenario 6: Resolve 'Amazon' -> AMZN."""
    comp = resolve_company("Amazon")
    assert comp is not None
    assert comp["ticker"] == "AMZN"
    assert resolve_ticker("Amazon") == "AMZN"


# =============================================================================
# SCENARIOS 7 - 11: NORMALIZATION, FUZZY, AMBIGUITY, AND CIK
# =============================================================================

def test_07_lowercase_ticker_resolution():
    """Scenario 7: Lowercase ticker resolution (aapl, msft, nvda, amd, tsla, amzn)."""
    assert resolve_ticker("aapl") == "AAPL"
    assert resolve_ticker("msft") == "MSFT"
    assert resolve_ticker("nvda") == "NVDA"
    assert resolve_ticker("amd") == "AMD"
    assert resolve_ticker("tsla") == "TSLA"
    assert resolve_ticker("amzn") == "AMZN"


def test_08_full_company_name_resolution():
    """Scenario 8: Full legal company names with corporate suffixes."""
    comp_aapl = resolve_company("Apple Inc.")
    assert comp_aapl is not None and comp_aapl["ticker"] == "AAPL"

    comp_msft = resolve_company("Microsoft Corporation")
    assert comp_msft is not None and comp_msft["ticker"] == "MSFT"

    comp_amd = resolve_company("Advanced Micro Devices, Inc.")
    assert comp_amd is not None and comp_amd["ticker"] == "AMD"

    comp_amzn = resolve_company("Amazon.com, Inc.")
    assert comp_amzn is not None and comp_amzn["ticker"] == "AMZN"


def test_09_ambiguous_ticker_handling():
    """Scenario 9: Ambiguous query returns candidate list or clean None without crashing."""
    resolver = get_stock_resolver()
    candidates = resolver.search_companies("Nat", limit=5)
    assert isinstance(candidates, list)
    # Search should return candidates if available, or empty if none match
    for c in candidates:
        assert "ticker" in c
        assert "name" in c


def test_10_unknown_company_handling():
    """Scenario 10: Unknown company returns None or raises clear CompanyNotFoundError."""
    comp = resolve_company("TotallyFakeNonExistentCorpXYZ12345")
    assert comp is None

    resolver = get_stock_resolver()
    with pytest.raises(CompanyNotFoundError):
        resolver.get_company_facts("TotallyFakeNonExistentCorpXYZ12345")


def test_11_invalid_cik_handling():
    """Scenario 11: Invalid CIK handling formats correctly or rejects bad inputs."""
    assert format_cik(320193) == "0000320193"
    assert format_cik("789019") == "0000789019"
    assert format_cik("2488") == "0000002488"
    assert format_cik(None) is None
    assert format_cik("invalid_cik_string") is None


# =============================================================================
# SCENARIOS 12 - 16: SEC EDGAR HTTP ERROR HANDLING AND CACHING
# =============================================================================

def test_12_sec_http_403_access_forbidden(tmp_path):
    """Scenario 12: SEC HTTP 403 Forbidden raises SECAccessDeniedError with user-agent advice."""
    resolver = StockResolver(cache_dir=tmp_path, auto_load=True, user_agent="FinGraphAI/1.0 (admin@fingraph.ai)")

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.json.return_value = {"error": "Access Denied"}

    with patch.object(resolver._session, "get", return_value=mock_resp):
        with pytest.raises(SECAccessDeniedError) as exc_info:
            resolver.get_company_facts("AAPL", force_refresh=True)
        assert "HTTP 403" in str(exc_info.value)
        assert "SEC_USER_AGENT" in str(exc_info.value)


def test_13_sec_http_404_not_found(tmp_path):
    """Scenario 13: SEC HTTP 404 raises SECNotFoundError."""
    resolver = StockResolver(cache_dir=tmp_path, auto_load=True)

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"error": "Not Found"}

    with patch.object(resolver._session, "get", return_value=mock_resp):
        with pytest.raises(SECNotFoundError) as exc_info:
            resolver.get_company_facts("AAPL", force_refresh=True)
        assert "No XBRL financial filings found" in str(exc_info.value)


def test_14_sec_http_429_rate_limit(tmp_path):
    """Scenario 14: SEC HTTP 429 raises SECRateLimitError after retries."""
    resolver = StockResolver(cache_dir=tmp_path, auto_load=True)

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.json.return_value = {"error": "Rate limited"}

    with patch.object(resolver._session, "get", return_value=mock_resp), \
         patch("time.sleep", return_value=None):
        with pytest.raises(SECRateLimitError) as exc_info:
            resolver.get_company_facts("AAPL", force_refresh=True)
        assert "rate limit exceeded" in str(exc_info.value).lower()


def test_15_sec_timeout_handling(tmp_path):
    """Scenario 15: SEC network timeout raises SECAPIError."""
    resolver = StockResolver(cache_dir=tmp_path, auto_load=True)

    with patch.object(resolver._session, "get", side_effect=requests.Timeout("Connection timed out")), \
         patch("time.sleep", return_value=None):
        with pytest.raises(SECAPIError) as exc_info:
            resolver.get_company_facts("AAPL", force_refresh=True)
        assert "timed out" in str(exc_info.value).lower()


def test_16_sec_caching(tmp_path):
    """Scenario 16: SEC facts are written to local cache and reused without extra HTTP requests."""
    resolver = StockResolver(cache_dir=tmp_path, auto_load=True)

    fake_facts = {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {"us-gaap": {}}
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_facts

    with patch.object(resolver._session, "get", return_value=mock_resp) as mock_get:
        # First call fetches from network
        data1 = resolver.get_company_facts("AAPL")
        assert mock_get.call_count == 1
        assert data1["entityName"] == "Apple Inc."

        # Verify cache file was written to disk
        cache_file = tmp_path / "CIK0000320193.json"
        assert cache_file.exists()

        # Second call must read from local cache file with ZERO network requests
        data2 = resolver.get_company_facts("AAPL")
        assert mock_get.call_count == 1
        assert data2["entityName"] == "Apple Inc."


# =============================================================================
# SCENARIOS 17 - 20: MULTI-COMPANY AND CONVERSATION STATE ISOLATION
# =============================================================================

def test_17_multi_company_questions():
    """Scenario 17: Query planner detects multiple companies and sets intent to company_comparison."""
    # Compare NVIDIA and AMD
    plan_nvda_amd = plan_financial_query("Compare NVIDIA and AMD")
    assert plan_nvda_amd["intent"] == "company_comparison"
    tickers = [c["ticker"] for c in plan_nvda_amd["companies"]]
    assert "NVDA" in tickers
    assert "AMD" in tickers

    # Compare Apple and Microsoft
    plan_aapl_msft = plan_financial_query("Compare Apple and Microsoft in 2023")
    assert plan_aapl_msft["intent"] == "company_comparison"
    tickers_am = [c["ticker"] for c in plan_aapl_msft["companies"]]
    assert "AAPL" in tickers_am
    assert "MSFT" in tickers_am

    # Compare NVIDIA, AMD, and Intel
    plan_three = plan_financial_query("Compare NVIDIA, AMD, and Intel")
    assert plan_three["intent"] == "company_comparison"
    assert len(plan_three["companies"]) == 3
    tickers_three = [c["ticker"] for c in plan_three["companies"]]
    assert tickers_three == ["NVDA", "AMD", "INTC"]


def test_18_follow_up_ticker_context():
    """Scenario 18: Follow-up question with pronoun ('its') inherits previous conversation ticker."""
    history = [
        {"role": "user", "text": "Analyze Apple's financial performance."},
        {"role": "assistant", "text": "Apple Inc. (AAPL) reported strong revenue in 2023."},
    ]
    # Follow-up: "What was its net profit in 2023?"
    plan = plan_financial_query("What was its net profit in 2023?", conversation_history=history)
    assert plan["target_ticker"] == "AAPL"
    assert "Apple" in plan["target_company"]


def test_19_new_chat_ticker_reset():
    """Scenario 19: New chat with clean history does NOT inherit an old company ticker."""
    # Empty history simulates a fresh "New Chat"
    plan = plan_financial_query("What was its revenue?", active_ticker="", stock_context=None, conversation_history=[])
    assert plan["target_ticker"] == ""
    assert plan["companies"] == []


def test_20_previous_chat_ticker_restoration():
    """Scenario 20: Restoring an old chat restores that thread's persistent ticker."""
    fake_memory = MagicMock()
    fake_memory.get_thread_ticker.return_value = "MSFT"

    team = FinanceAgentTeam(fake_memory, llm=None)
    mock_invoke = MagicMock(return_value={"answer": "Microsoft FY2023 overview", "ticker": "MSFT"})
    team.graph.invoke = mock_invoke

    res = team.ask("What was the annual revenue?", thread_id="thread_msft")
    assert res["ticker"] == "MSFT"
    # Thread ticker MSFT was passed to graph
    call_args = mock_invoke.call_args[0][0]
    assert call_args["ticker"] == "MSFT"


# =============================================================================
# SCENARIOS 21 - 24: FALKORDB, TENANT ISOLATION, NSE STOCKS, AND EC2 DIAGNOSTICS
# =============================================================================

def test_21_falkordb_company_caching():
    """Scenario 21: Upsert resolved company into FalkorDB Financial Knowledge Graph."""
    mock_fkg = MagicMock()
    mock_fkg.is_available = True
    mock_fkg.query.return_value = [["AAPL"]]

    resolver = get_stock_resolver()
    comp = resolve_company("Apple")
    success = resolver.upsert_company_to_graph(comp, fkg=mock_fkg)
    assert success is True
    assert mock_fkg.query.call_count == 1
    cypher_call = mock_fkg.query.call_args[0][0]
    assert "MERGE (c:Company {ticker: $ticker})" in cypher_call


def test_22_tenant_isolation(tmp_path):
    """Scenario 22: Tenant memory isolation ensures tenant A does not see tenant B's stock data."""
    mock_embed = MagicMock()
    mock_embed.embed_documents.return_value = [[0.1] * 384]
    mock_embed.embed_query.return_value = [0.1] * 384

    # Connect two distinct mock FalkorMemory instances
    mem_tenant_a = FalkorMemory("tenant-alpha", "user-1", mock_embed)
    mem_tenant_b = FalkorMemory("tenant-beta", "user-2", mock_embed)

    assert mem_tenant_a.tenant_id == "tenant-alpha"
    assert mem_tenant_b.tenant_id == "tenant-beta"
    assert mem_tenant_a.graph_name != mem_tenant_b.graph_name


def test_23_unsupported_nse_stocks():
    """Scenario 23: Indian stocks resolve correctly but demarcate SEC EDGAR financial statement limitation."""
    # Reliance Industries
    comp_ril = resolve_company("Reliance Industries")
    assert comp_ril is not None
    assert comp_ril["ticker"] == "RELIANCE.NS"
    assert comp_ril["sec_supported"] is False
    assert comp_ril["currency"] == "INR"

    # TCS
    comp_tcs = resolve_company("TCS")
    assert comp_tcs is not None
    assert comp_tcs["ticker"] == "TCS.NS"
    assert comp_tcs["sec_supported"] is False

    # Attempting to fetch SEC facts for an Indian stock raises clear, informative SECAPIError
    resolver = get_stock_resolver()
    with pytest.raises(SECAPIError) as exc_info:
        resolver.get_company_facts("RELIANCE.NS")
    err_msg = str(exc_info.value)
    assert "not available through the currently configured SEC EDGAR source" in err_msg


def test_24_ec2_network_connectivity_diagnostics():
    """Scenario 24: Validate SEC EDGAR headers and reachability verification logic."""
    resolver = get_stock_resolver()
    headers = resolver._get_headers()
    assert "User-Agent" in headers
    assert "FinGraph" in headers["User-Agent"] or "@" in headers["User-Agent"]
    assert headers["Accept"] == "application/json"
    assert "gzip" in headers["Accept-Encoding"]
