import os
import pytest
from stock_api import (
    get_selected_stock_context,
    resolve_ticker,
    get_stock_price,
    get_historical_prices,
    get_income_statement,
    FinancialAPIError,
)
from memory_agents import FinanceAgentTeam
from falkor_memory import FalkorMemory


class FakeGraph:
    """Mock graph for FalkorMemory in unit tests."""
    def __init__(self):
        self.handoffs = {}
        self.turns = []
        self.memories = []

    def create_node_vector_index(self, *a, **kw):
        pass

    def query(self, cypher: str, params=None):
        params = params or {}
        class FakeResult:
            def __init__(self, rows):
                self.result_set = rows

        if "CREATE (h:Handoff" in cypher:
            hid = params.get("handoff_id")
            self.handoffs[hid] = params.get("payload", "{}")
            return FakeResult([[hid]])
        elif "h.status='pending'" in cypher and "RETURN h.payload" in cypher:
            hid = params.get("handoff_id")
            payload = self.handoffs.get(hid, "{}")
            return FakeResult([[payload]])
        elif "RETURN h.id" in cypher or "h.status='claimed'" in cypher:
            return FakeResult([[params.get("handoff_id", "ok")]])
        elif "CREATE (t:Turn" in cypher:
            self.turns.append(params)
            return FakeResult([[params.get("turn_id", "turn1")]])
        elif "CREATE (m:Memory" in cypher or "MERGE (m:Memory" in cypher:
            self.memories.append(params)
            return FakeResult([[params.get("memory_id", "mem1")]])
        return FakeResult([[params.get("doc_id", "ok")]])

    def ro_query(self, cypher: str, params=None):
        class FakeResult:
            result_set = []
        return FakeResult()


class DummyEmbeddings:
    def embed_query(self, text: str):
        return [0.0] * 384


class MockLLMWithTools:
    """Mock LLM to test agent tool calls and prompt handling deterministically."""
    def __init__(self):
        self.calls = []

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = {t.name: t for t in tools}
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        sys_msg = messages[0].content
        user_msg = messages[-1].content

        class MockResponse:
            def __init__(self, content="", tool_calls=None):
                self.content = content
                self.tool_calls = tool_calls or []

        # Check if last message was a ToolMessage
        if any(m.__class__.__name__ == "ToolMessage" for m in messages):
            # Synthesize answer from tool message
            tool_msg = [m for m in messages if m.__class__.__name__ == "ToolMessage"][-1]
            return MockResponse(content=f"Reported data from tool: {tool_msg.content[:150]}")

        # Check Test 7: No active stock selected
        if ("Ticker Symbol: NONE" in sys_msg or "Symbol: NONE" in sys_msg) and "Apple" not in user_msg and "TCS" not in user_msg and "Reliance" not in user_msg:
            return MockResponse(content="No valid stock is currently selected. Please select a stock ticker from the dashboard or specify a company.")

        # Check Test 6: Explicitly named company different from selected stock
        if "Apple" in user_msg or "AAPL" in user_msg:
            return MockResponse(
                content="",
                tool_calls=[{"name": "tool_stock_price", "args": {"symbol": "AAPL"}, "id": "call_aapl_1"}]
            )

        # Check Test 1: Reliance annual revenue
        if "RELIANCE.NS" in sys_msg and "revenue" in user_msg.lower():
            return MockResponse(
                content="",
                tool_calls=[{"name": "tool_income_statement", "args": {"symbol": "RELIANCE.NS", "period": "annual"}, "id": "call_rel_1"}]
            )

        # Check Test 2: TCS net profit
        if "TCS.NS" in sys_msg and "profit" in user_msg.lower():
            return MockResponse(
                content="",
                tool_calls=[{"name": "tool_income_statement", "args": {"symbol": "TCS.NS", "period": "annual"}, "id": "call_tcs_1"}]
            )

        # Check Test 3: Apple quarterly revenue
        if "AAPL" in sys_msg and "quarterly" in user_msg.lower():
            return MockResponse(
                content="",
                tool_calls=[{"name": "tool_income_statement", "args": {"symbol": "AAPL", "period": "quarterly"}, "id": "call_aapl_q1"}]
            )

        return MockResponse(content="General response")


@pytest.fixture
def fake_memory():
    return FalkorMemory("test_ws", "test_user", DummyEmbeddings(), graph=FakeGraph())


def test_1_select_reliance_ask_annual_revenue(fake_memory):
    """TEST 1: Select RELIANCE.NS, ask annual revenue -> agent requests Reliance financial data."""
    ctx = get_selected_stock_context("RELIANCE", "NSE")
    assert ctx["symbol"] == "RELIANCE.NS"
    assert ctx["currency"] == "INR"

    mock_llm = MockLLMWithTools()
    team = FinanceAgentTeam(fake_memory, mock_llm)
    res = team.ask("What is the latest available annual revenue of this company?", stock_context=ctx)
    assert "RELIANCE.NS" in str(mock_llm.calls[0])
    assert "Reliance" in res["answer"] or "RELIANCE.NS" in res["answer"]


def test_2_switch_to_tcs_ask_net_profit(fake_memory):
    """TEST 2: Switch to TCS.NS, ask net profit -> agent requests TCS data rather than Reliance data."""
    ctx = get_selected_stock_context("TCS", "NSE")
    assert ctx["symbol"] == "TCS.NS"
    assert ctx["currency"] == "INR"

    mock_llm = MockLLMWithTools()
    team = FinanceAgentTeam(fake_memory, mock_llm)
    res = team.ask("What is the net profit of this company?", stock_context=ctx)
    assert "TCS.NS" in str(mock_llm.calls[0])
    assert "RELIANCE.NS" not in str(mock_llm.calls[0][0].content)  # Should not be in active context
    assert "TCS.NS" in res["answer"] or "tool:" in res["answer"]


def test_3_switch_to_aapl_ask_quarterly_revenue(fake_memory):
    """TEST 3: Switch to AAPL, ask quarterly revenue -> agent requests Apple's quarterly data."""
    ctx = get_selected_stock_context("AAPL", "US")
    assert ctx["symbol"] == "AAPL"
    assert ctx["currency"] == "USD"

    mock_llm = MockLLMWithTools()
    team = FinanceAgentTeam(fake_memory, mock_llm)
    res = team.ask("Show the latest available quarterly revenue.", stock_context=ctx)
    assert "AAPL" in str(mock_llm.calls[0])
    assert "quarterly" in str(mock_llm.calls[1]) or "AAPL" in res["answer"]


def test_4_reliance_stock_price_graph_data():
    """TEST 4: Select RELIANCE.NS -> historical prices requested for RELIANCE.NS."""
    ctx = get_selected_stock_context("RELIANCE", "NSE")
    data = get_historical_prices(ctx["symbol"], range="1mo")
    assert data["symbol"] == "RELIANCE.NS"
    assert data["currency"] == "INR"
    assert data["dataPoints"] > 0
    assert data["latestClose"] is not None


def test_5_switch_to_msft_graph_updates():
    """TEST 5: Switch to MSFT -> graph updates to Microsoft's historical prices."""
    ctx = get_selected_stock_context("MSFT", "US")
    data = get_historical_prices(ctx["symbol"], range="1mo")
    assert data["symbol"] == "MSFT"
    assert data["currency"] == "USD"
    assert data["dataPoints"] > 0
    assert data["latestClose"] is not None


def test_6_explicitly_named_company_different_from_selected(fake_memory):
    """TEST 6: Ask about an explicitly named company (Apple) while Reliance is selected -> resolves Apple."""
    ctx = get_selected_stock_context("RELIANCE", "NSE")
    mock_llm = MockLLMWithTools()
    team = FinanceAgentTeam(fake_memory, mock_llm)
    res = team.ask("What is Apple's current stock price?", stock_context=ctx)
    # The active context remains Reliance, but user asked about Apple
    assert "RELIANCE.NS" in mock_llm.calls[0][0].content
    # The tool was called for AAPL
    assert "call_aapl_1" in str(mock_llm.calls[1]) or "AAPL" in res["answer"]


def test_7_no_selected_ticker_reports_no_valid_stock(fake_memory):
    """TEST 7: Remove selected ticker -> agent reports no valid stock selected and does NOT default to AAPL."""
    mock_llm = MockLLMWithTools()
    team = FinanceAgentTeam(fake_memory, mock_llm)
    res = team.ask("What is its revenue?", stock_context=None)
    assert "no valid stock is currently selected" in res["answer"].lower()
    assert "AAPL" not in res["answer"]
