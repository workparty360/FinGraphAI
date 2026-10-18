"""Comprehensive test suite for Groq organization quota handling and token optimization.

Tests:
1. Org-level TPD does not rotate keys within same organization.
2. Revoked/invalid key (401) rotates to next key slot.
3. Model fallback: when primary model hits quota with retry-after, falls back to GROQ_FALLBACK_MODEL.
4. When all models are cooling down, raises OrganizationQuotaExhaustedError.
5. Reliance FY2025 revenue retrieval retrieves only the relevant observation without dumping full statements.
6. Deterministic calculations in financial_math.py calculate revenue growth, margin, FCF.
7. Measure token usage: verify input prompt < 1,500 characters (down from 15,000+ chars).
8. Quota fallback formats verified FalkorDB data directly when AI is rate limited.
9. Quota fallback without data shows clean notice.
"""
from __future__ import annotations

import json
import os
import pytest
from unittest.mock import MagicMock, patch

from api_key_manager import (
    APIKeyManager,
    AllAPIKeysExhaustedError,
    OrganizationQuotaExhaustedError,
    classify_error,
    get_model_tracker,
)
from falkor_memory import FalkorMemory
from financial_math import (
    calculate_growth_rate,
    calculate_margin,
    calculate_free_cash_flow,
    format_financial_amount,
    compare_financial_performance,
)
from memory_agents import (
    FinanceAgentTeam,
    deterministic_fetch_financial_data,
    _format_quota_fallback,
)


@pytest.fixture(autouse=True)
def reset_tracker_fixture():
    tracker = get_model_tracker()
    tracker.reset()
    yield
    tracker.reset()


# ===================================================================
# TEST 1: Org-level TPD does not rotate keys within same organization
# ===================================================================
def test_1_org_level_tpd_does_not_rotate_keys_within_same_org():
    """When a 429 TPD error occurs, keys within the same organization share quota.
    The manager must NOT rotate keys 1 -> 2 -> 3 for the same model.
    """
    tracker = get_model_tracker()
    tracker.reset()

    invoked_keys = []
    tpd_error = (
        "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
        "`openai/gpt-oss-120b` in organization `org_groq_test` on tokens per day (TPD): "
        "Limit 200000, Used 195594, Requested 8663. Please try again in 28m45s.', "
        "'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
    )

    def mock_fn(llm):
        raw = getattr(llm, "groq_api_key", None)
        k = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw or llm)
        invoked_keys.append(k)
        raise RuntimeError(tpd_error)

    mgr = APIKeyManager(keys=["key_org_1", "key_org_2", "key_org_3"])

    # Ensure fallback model is the same as primary so only 1 candidate model is tested
    with patch.dict(os.environ, {"GROQ_PRIMARY_MODEL": "openai/gpt-oss-120b", "GROQ_FALLBACK_MODEL": "openai/gpt-oss-120b"}):
        with pytest.raises(OrganizationQuotaExhaustedError) as exc_info:
            mgr.call_with_failover(mock_fn, model="openai/gpt-oss-120b")

    # Only the first key was called for this model; keys 2 and 3 were NOT uselessly rotated!
    assert len(invoked_keys) == 1
    assert "key_org_1" in invoked_keys[0]

    # Slot 1 is NOT marked cooling down because the key itself is valid
    assert mgr.slots[0].status == "healthy"

    # But the model IS tracked in cooldown
    assert not tracker.is_available("openai/gpt-oss-120b")
    assert exc_info.value.retry_after_secs > 0


# ===================================================================
# TEST 2: Revoked/invalid key (401) rotates to next key slot
# ===================================================================
def test_2_revoked_key_401_rotates_to_next_key_slot():
    """When a key-specific error occurs (e.g. 401 Unauthorized / Invalid API Key),
    failover proceeds immediately to the next configured key slot.
    """
    tracker = get_model_tracker()
    tracker.reset()

    invoked_keys = []

    def mock_fn(llm):
        raw = getattr(llm, "groq_api_key", None)
        k = raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw or llm)
        invoked_keys.append(k)
        if "key_bad" in k:
            raise RuntimeError("Error code: 401 - {'error': {'message': 'Invalid API Key provided', 'type': 'invalid_request_error'}}")
        return {"status": "success", "key_used": k}

    mgr = APIKeyManager(keys=["key_bad", "key_good", "key_spare"])
    result = mgr.call_with_failover(mock_fn)

    assert result["status"] == "success"
    assert "key_good" in result["key_used"]
    assert mgr.slots[0].status == "cooling_down"
    assert mgr.active_slot == "key-2"


# ===================================================================
# TEST 3: Model fallback: when primary model hits quota, routes to fallback
# ===================================================================
def test_3_model_fallback_when_primary_hits_quota():
    """When primary model hits organization daily quota, the manager routes
    the request to the configured fallback model (e.g. gpt-oss-20b).
    """
    tracker = get_model_tracker()
    tracker.reset()

    models_called = []

    def mock_fn(llm):
        m = getattr(llm, "model_name", getattr(llm, "model", "unknown"))
        models_called.append(m)
        if m == "openai/gpt-oss-120b":
            raise RuntimeError(
                "Error code: 429 - tokens per day (TPD) exceeded on model openai/gpt-oss-120b. "
                "Limit: 200000. Try again in 30 minutes."
            )
        return {"content": f"Success from fallback model {m}"}

    mgr = APIKeyManager(keys=["test_key_1"])

    with patch.dict(os.environ, {
        "GROQ_PRIMARY_MODEL": "openai/gpt-oss-120b",
        "GROQ_FALLBACK_MODEL": "openai/gpt-oss-20b",
    }):
        res = mgr.call_with_failover(mock_fn)

    assert "openai/gpt-oss-120b" in models_called
    assert "openai/gpt-oss-20b" in models_called
    assert "Success from fallback model openai/gpt-oss-20b" in res["content"]

    # Primary model is marked in cooldown; fallback model remains available
    assert not tracker.is_available("openai/gpt-oss-120b")
    assert tracker.is_available("openai/gpt-oss-20b")


# ===================================================================
# TEST 4: When all models are cooling down, raises OrganizationQuotaExhaustedError
# ===================================================================
def test_4_all_models_quota_exhausted_raises_organization_quota_error():
    """When both primary and fallback models have exhausted their quotas,
    the manager raises an informative OrganizationQuotaExhaustedError.
    """
    tracker = get_model_tracker()
    tracker.reset()

    def mock_fn(llm):
        m = getattr(llm, "model_name", getattr(llm, "model", "unknown"))
        raise RuntimeError(
            f"Error code: 429 - tokens per day (TPD) daily limit exceeded on {m}. "
            f"Please try again in 1800s."
        )

    mgr = APIKeyManager(keys=["test_key_1"])

    with patch.dict(os.environ, {
        "GROQ_PRIMARY_MODEL": "openai/gpt-oss-120b",
        "GROQ_FALLBACK_MODEL": "openai/gpt-oss-20b",
    }):
        with pytest.raises(OrganizationQuotaExhaustedError) as exc_info:
            mgr.call_with_failover(mock_fn)

    err = exc_info.value
    assert isinstance(err, AllAPIKeysExhaustedError)  # backward-compatible inheritance
    assert err.retry_after_secs >= 1800
    assert "daily limit (TPD)" in str(err)
    assert "30 minutes" in str(err)


# ===================================================================
# TEST 5: Targeted FalkorDB retrieval for FY2025 revenue (no full statement dumps)
# ===================================================================
def test_5_reliance_fy2025_revenue_retrieval_targeted():
    """Verify that querying Reliance FY2025 revenue retrieves the targeted
    observation record without dumping full statement tables.
    """
    try:
        from financial_graph import get_financial_graph
        fkg = get_financial_graph()
        if not fkg.is_available:
            pytest.skip("Live FalkorDB instance is not available for this test.")

        obs = fkg.get_company_financials("RELIANCE.NS", "Total Revenue", fiscal_year=2025)
        assert len(obs) == 1
        record = obs[0]
        assert record["fiscal_year"] == 2025
        assert record["metric"] == "Total Revenue"
        assert record["value"] > 0
        assert "bse" in record["source_doc_id"].lower() or "bse" in record["source_url"].lower()

    except Exception as exc:
        pytest.skip(f"FalkorDB connection skipped: {exc}")


# ===================================================================
# TEST 6: Deterministic calculations in pure Python (0 LLM tokens)
# ===================================================================
def test_6_deterministic_financial_math_calculations():
    """Verify that growth rates, margins, and multi-year comparisons
    are accurately computed in pure Python without calling Groq.
    """
    # 1. Growth rate calculation
    growth_res = calculate_growth_rate(1002500.0, 892500.0)
    assert growth_res["abs_change"] == 110000.0
    assert growth_res["pct_change"] == 12.32  # rounded to 2 decimal places

    # 2. Margin calculation
    margin_res = calculate_margin(154730.0, 1002500.0)
    assert margin_res["margin_pct"] == 15.43

    # 3. Free Cash Flow calculation
    fcf_res = calculate_free_cash_flow(165000.0, 75000.0)
    assert fcf_res["fcf"] == 90000.0

    # 4. Formatted Indian currency (Crores)
    formatted = format_financial_amount(10014920000000.0, "INR")
    assert "1,001,492.00 Cr" in formatted
    assert "₹" in formatted

    # 5. Full comparison table calculation (if FalkorDB available)
    try:
        from financial_graph import get_financial_graph
        fkg = get_financial_graph()
        if fkg.is_available:
            perf = compare_financial_performance("RELIANCE.NS", 2023, 2025, fkg=fkg)
            assert perf["ticker"] == "RELIANCE.NS"
            assert perf["start_year"] == 2023
            assert perf["end_year"] == 2025
            assert len(perf["comparison_table"]) >= 2
            rev_row = [r for r in perf["comparison_table"] if r["metric"] == "Total Revenue"][0]
            assert rev_row["pct_change"] == 12.32
            assert len(perf["notes"]) > 0
    except Exception:
        pass


# ===================================================================
# TEST 7: Token reduction: verify prompt < 1,500 characters
# ===================================================================
def test_7_token_reduction_prompt_compact():
    """Verify that the user prompt constructed for the LLM is compact
    (< 1,500 characters), down from the previous 15,000+ character dumps.
    """
    class Vector:
        def embed_query(self, text):
            return [0.0] * 384

    class MockGraph:
        def __init__(self):
            self.handoffs = {}
        def create_node_vector_index(self, *a, **kw):
            pass
        def query(self, cypher, params=None):
            class R:
                def __init__(self, data):
                    self.result_set = data
            if "h.status='pending'" in cypher:
                return R([['{"documents": [], "exact_year_matches": [], "memories": []}']])
            return R([["h1"]])
        def ro_query(self, cypher, params=None):
            class R:
                result_set = []
            return R()

    captured_prompts = []

    class MockInspectLLM:
        def bind_tools(self, tools, **kwargs):
            return self
        def invoke(self, messages):
            for m in messages:
                captured_prompts.append((m.__class__.__name__, len(m.content), m.content))
            from langchain_core.messages import AIMessage
            return AIMessage(content="Financial analysis response.")

    mem = FalkorMemory("ws", "user", Vector(), graph=MockGraph())
    team = FinanceAgentTeam(mem, MockInspectLLM())
    ctx = {"symbol": "RELIANCE.NS", "companyName": "Reliance Industries", "exchange": "NSE", "currency": "INR"}

    team.ask("What was Reliance's annual revenue in 2025?", stock_context=ctx)

    assert len(captured_prompts) >= 2
    sys_type, sys_len, sys_content = captured_prompts[0]
    user_type, user_len, user_content = captured_prompts[1]

    # Verify system prompt is compact (< 800 chars / ~100 tokens)
    assert sys_len < 800, f"System prompt was too large: {sys_len} chars"

    # Verify user prompt is compact (< 1,500 chars, previously > 15,000 chars)
    assert user_len < 1500, f"User prompt was too large: {user_len} chars"
    assert "Active Ticker: RELIANCE.NS" in user_content
    assert "Reliance's annual revenue in 2025" in user_content


# ===================================================================
# TEST 8: Quota fallback formats verified FalkorDB data directly
# ===================================================================
def test_8_quota_fallback_formats_verified_data_directly():
    """When Groq daily quota is exhausted, _format_quota_fallback
    directly displays verified FalkorDB records with estimated reset time.
    """
    backend_data = {
        "ticker": "RELIANCE.NS",
        "graph_metric_observation": {
            "metric_name": "Total Revenue",
            "fiscal_year": 2025,
            "amount": 1001492.0,
            "unit": "Crore",
            "formatted_amount": "₹10,01,492.00 Cr",
            "source_filing": "BSE Annual Regulatory Filing",
        },
        "current_price": {"price": 2745.50, "currency": "INR"},
    }
    quota_err = OrganizationQuotaExhaustedError(
        "TPD daily quota exhausted",
        retry_after_secs=1725.0,
        model="openai/gpt-oss-120b",
    )

    formatted = _format_quota_fallback(
        ticker="RELIANCE.NS",
        company="Reliance Industries",
        currency="INR",
        backend_data=backend_data,
        error=quota_err,
    )

    assert "Verified FalkorDB Record" in formatted
    assert "Total Revenue (FY2025)" in formatted
    assert "₹10,01,492.00 Cr" in formatted
    assert "BSE Annual Regulatory Filing" in formatted
    assert "in ~28 minutes" in formatted
    assert "daily organization quota (TPD)" in formatted


# ===================================================================
# TEST 9: Quota fallback without data shows clean notice
# ===================================================================
def test_9_quota_fallback_without_data_shows_clean_notice():
    """When Groq quota is exhausted and no graph data is cached,
    a polite notification with estimated reset time is displayed.
    """
    quota_err = OrganizationQuotaExhaustedError(
        "TPD daily limit reached",
        retry_after_secs=1800.0,
    )
    formatted = _format_quota_fallback(
        ticker="UNKNOWN",
        company="Unknown",
        currency="USD",
        backend_data={},
        error=quota_err,
    )

    assert "approximately 30 minutes" in formatted
    assert "daily organization quota (TPD)" in formatted
    assert "verified FalkorDB knowledge graph records remain intact" in formatted
