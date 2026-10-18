"""Automated test suite for the Groq integration, structured prompt builder,
and execution tracing pipeline.
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from execution_tracer import (
    STAGE_REQUEST_RECEIVED,
    STAGE_QUERY_PLANNED,
    STAGE_COMPANY_RESOLVED,
    STAGE_RETRIEVAL_STARTED,
    STAGE_RETRIEVAL_COMPLETED,
    STAGE_PROMPT_CONSTRUCTED,
    STAGE_GROQ_REQUEST_STARTED,
    STAGE_GROQ_RESPONSE_RECEIVED,
    STAGE_ANALYST_COMPLETED,
    STAGE_MEMORY_PERSISTED,
    STAGE_RESPONSE_RENDERED,
    GROQ_AUTH_ERROR,
    GROQ_RATE_LIMIT,
    GROQ_MODEL_ERROR,
    GROQ_NETWORK_ERROR,
    GROQ_TOOL_ERROR,
    FALKORDB_CONNECTION_ERROR,
    FALKORDB_QUERY_ERROR,
    SEC_API_ERROR,
    FINANCIAL_DATA_MISSING,
    classify_app_error,
    clear_traces,
    generate_request_id,
    get_traces_for_request,
    record_trace,
)
from prompt_builder import (
    build_analyst_prompt_package,
    is_general_financial_query,
    is_unspecified_company_follow_up,
)


def test_1_general_financial_query_detection():
    """Verify general financial concepts are recognized without requiring a stock ticker."""
    assert is_general_financial_query("What is return on equity?") is True
    assert is_general_financial_query("What is ROE?") is True
    assert is_general_financial_query("What is a balance sheet?") is True
    assert is_general_financial_query("Explain operating margin.") is True
    assert is_general_financial_query("Define EBITDA and why it matters") is True
    assert is_general_financial_query("How to calculate debt to equity ratio") is True

    # Company specific or year specific questions are NOT general
    assert is_general_financial_query("What was Apple's net income in fiscal 2023?") is False
    assert is_general_financial_query("What was Microsoft's revenue in 2024?") is False


def test_2_unspecified_company_follow_up_detection():
    """Verify follow-up questions with no active company prompt for company clarification."""
    # Empty ticker + follow-up phrase / year
    assert is_unspecified_company_follow_up("What about 2024?", target_ticker="", conversation_history=[]) is True
    assert is_unspecified_company_follow_up("How much was its revenue?", target_ticker="", conversation_history=[]) is True

    # Active ticker present -> NOT unspecified
    assert is_unspecified_company_follow_up("What about 2024?", target_ticker="AAPL", conversation_history=[]) is False

    # General concept -> NOT unspecified
    assert is_unspecified_company_follow_up("What is return on equity?", target_ticker="", conversation_history=[]) is False


def test_3_prompt_builder_general_financial_question():
    """Verify prompt builder creates educator prompt for general financial inquiries."""
    pkg = build_analyst_prompt_package(
        question="What is return on equity?",
        plan={"intent": "general_knowledge", "target_ticker": ""},
        retrieved_data={"target_ticker": ""},
        conversation_history=[],
    )

    assert pkg["is_general_knowledge"] is True
    assert pkg["is_unspecified_company"] is False
    assert len(pkg["messages"]) >= 2
    assert "senior financial analyst and educator" in pkg["system_prompt"]
    assert "What is return on equity?" in pkg["messages"][-1].content
    assert pkg["approx_prompt_tokens"] > 10


def test_4_prompt_builder_unspecified_company_clarification():
    """Verify prompt builder flags unspecified company follow-up."""
    pkg = build_analyst_prompt_package(
        question="What about 2024?",
        plan={"intent": "follow_up", "target_ticker": ""},
        retrieved_data={"target_ticker": ""},
        conversation_history=[],
    )

    assert pkg["is_unspecified_company"] is True
    assert "No valid stock is currently selected" in pkg["clarification_message"]
    assert "Which company are you referring to?" in pkg["clarification_message"]


def test_5_prompt_builder_synthetic_company_facts():
    """Verify prompt builder bounds exact financial facts and sources for synthetic company."""
    plan = {
        "intent": "single_year_lookup",
        "target_ticker": "DEMO",
        "target_company": "Demo Corp",
        "target_currency": "USD",
        "target_exchange": "NASDAQ",
    }
    retrieved_data = {
        "target_ticker": "DEMO",
        "target_company": "Demo Corp",
        "target_currency": "USD",
        "target_exchange": "NASDAQ",
        "exact_metric": {
            "ticker": "DEMO",
            "company_name": "Demo Corp",
            "fiscal_year": 2023,
            "metric": "Total Revenue",
            "value": 100000000.0,
            "currency": "USD",
            "source_title": "Demo Corp Form 10-K FY2023",
            "source_url": "https://www.sec.gov/edgar/demo/10k2023.htm",
            "filing_type": "Form 10-K",
        },
        "single_year_overview": {
            "year": 2023,
            "currency": "USD",
            "metrics": {
                "Total Revenue": 100000000.0,
                "Net Income": 20000000.0,
                "Operating Margin": 20.0,
            },
        },
        "best_year_analysis": {
            "best_composite_year": 2023,
            "leaders": {
                "highest_revenue": {"year": 2023, "formatted": "$100.00M"},
                "highest_net_income": {"year": 2023, "formatted": "$20.00M"},
                "highest_operating_margin": {"year": 2023, "formatted": "20.00%"},
            },
            "summary_text": "Demo Corp recorded its strongest performance in FY2023 with $100M revenue.",
        },
    }

    pkg = build_analyst_prompt_package(
        question="What was Demo Corp's net income in 2023?",
        plan=plan,
        retrieved_data=retrieved_data,
        conversation_history=[],
    )

    assert pkg["is_general_knowledge"] is False
    assert pkg["is_unspecified_company"] is False

    user_msg_content = pkg["messages"][-1].content
    assert "Active Ticker: DEMO" in user_msg_content
    assert "Demo Corp" in user_msg_content
    assert "$100.00M" in user_msg_content or "$100" in user_msg_content
    assert "$20.00M" in user_msg_content or "$20" in user_msg_content
    assert "Demo Corp Form 10-K FY2023" in user_msg_content or "10k2023.htm" in user_msg_content

    facts = pkg["context_package"]["financial_facts"]
    assert any(f["metric"] == "Total Revenue" and f["value"] == 100000000.0 for f in facts)
    assert any(f["metric"] == "Net Income" and f["value"] == 20000000.0 for f in facts)


def test_6_execution_tracer_pipeline_and_error_classification():
    """Verify execution tracer records all 11 stages and classifies errors accurately."""
    clear_traces()
    req_id = generate_request_id()

    stages = [
        STAGE_REQUEST_RECEIVED,
        STAGE_QUERY_PLANNED,
        STAGE_COMPANY_RESOLVED,
        STAGE_RETRIEVAL_STARTED,
        STAGE_RETRIEVAL_COMPLETED,
        STAGE_PROMPT_CONSTRUCTED,
        STAGE_GROQ_REQUEST_STARTED,
        STAGE_GROQ_RESPONSE_RECEIVED,
        STAGE_ANALYST_COMPLETED,
        STAGE_MEMORY_PERSISTED,
        STAGE_RESPONSE_RENDERED,
    ]

    for stage in stages:
        record_trace(req_id, stage, "test_agent", status="SUCCESS", duration_secs=0.05)

    traces = get_traces_for_request(req_id)
    assert len(traces) == 11
    recorded_stages = [t["stage"] for t in traces]
    assert recorded_stages == stages

    # Test error categorization (Section 15)
    assert classify_app_error("401 Unauthorized invalid_api_key") == GROQ_AUTH_ERROR
    assert classify_app_error("429 Rate limit exceeded tokens per day") == GROQ_RATE_LIMIT
    assert classify_app_error("model_not_found llama-3.3-70b-versatile") == GROQ_MODEL_ERROR
    assert classify_app_error("Connection refused host unreachable") == GROQ_NETWORK_ERROR
    assert classify_app_error("tool choice failed invalid tool call") == GROQ_TOOL_ERROR
    assert classify_app_error("FalkorDB connection refused on port 6379") == FALKORDB_CONNECTION_ERROR
    assert classify_app_error("CypherSyntaxError: invalid query syntax") == FALKORDB_QUERY_ERROR
    assert classify_app_error("SEC EDGAR HTTP 403 Forbidden") == SEC_API_ERROR
    assert classify_app_error("Financial data missing for FY2020") == FINANCIAL_DATA_MISSING
