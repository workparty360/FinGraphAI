import json
import pytest
from langchain_core.messages import AIMessage

from falkor_memory import FalkorMemory
from memory_agents import (
    FinanceAgentTeam,
    deterministic_fetch_financial_data,
    tool_stock_price,
    tool_historical_prices,
    tool_income_statement,
    tool_balance_sheet,
    tool_cash_flow,
    tool_cash_flow_statement,
    tool_company_overview,
)
from stock_api import get_stock_price, get_income_statement, get_historical_prices


class MockGraph:
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
            return FakeResult([[self.handoffs.get(hid, "{}")]])
        elif "RETURN h.id" in cypher or "h.status='claimed'" in cypher:
            return FakeResult([[params.get("handoff_id", "ok")]])
        elif "CREATE (t:Turn" in cypher:
            self.turns.append(params)
            return FakeResult([[params.get("turn_id", "t1")]])
        elif "CREATE (m:Memory" in cypher or "MERGE (m:Memory" in cypher:
            self.memories.append(params)
            return FakeResult([[params.get("memory_id", "m1")]])
        return FakeResult([[params.get("doc_id", "ok")]])

    def ro_query(self, cypher: str, params=None):
        class FakeResult:
            result_set = []
        return FakeResult()


class DummyEmbeddings:
    def embed_query(self, text: str):
        return [0.0] * 384


class MockToolCallingLLM:
    """Mock LLM simulating tool calling protocol with bind_tools."""
    def __init__(self, tool_to_call: str, tool_args: dict, final_answer: str):
        self.tool_to_call = tool_to_call
        self.tool_args = tool_args
        self.final_answer = final_answer
        self.call_count = 0
        self.bound_kwargs = {}

    def bind_tools(self, tools, **kwargs):
        clone = MockToolCallingLLM(self.tool_to_call, self.tool_args, self.final_answer)
        clone.bound_kwargs = kwargs
        return clone

    def invoke(self, messages):
        self.call_count += 1
        # First turn: emit tool call
        if self.call_count == 1 and self.tool_to_call:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": self.tool_to_call,
                    "args": self.tool_args,
                    "id": f"call_{self.tool_to_call}_1"
                }]
            )
        # Subsequent turns: output final answer
        return AIMessage(content=self.final_answer)


def test_a_hdfc_stock_price():
    """TEST A: HDFCBANK.NS stock price tool execution."""
    memory = FalkorMemory("ws", "user", DummyEmbeddings(), graph=MockGraph())
    llm = MockToolCallingLLM(
        tool_to_call="tool_stock_price",
        tool_args={"symbol": "HDFCBANK.NS"},
        final_answer="HDFC Bank (HDFCBANK.NS) current price is ₹728.85."
    )
    team = FinanceAgentTeam(memory, llm)
    ctx = {"symbol": "HDFCBANK.NS", "companyName": "HDFC Bank", "exchange": "NSE", "currency": "INR"}
    res = team.ask("What is the current stock price?", stock_context=ctx)

    assert "728.85" in res["answer"]
    assert "HDFC" in res["answer"]


def test_b_reliance_annual_revenue():
    """TEST B: RELIANCE.NS annual revenue."""
    memory = FalkorMemory("ws", "user", DummyEmbeddings(), graph=MockGraph())
    llm = MockToolCallingLLM(
        tool_to_call="tool_income_statement",
        tool_args={"symbol": "RELIANCE.NS", "period": "annual"},
        final_answer="Reliance Industries (RELIANCE.NS) reported revenue of ₹10,57,000 Crore."
    )
    team = FinanceAgentTeam(memory, llm)
    ctx = {"symbol": "RELIANCE.NS", "companyName": "Reliance Industries", "exchange": "NSE", "currency": "INR"}
    res = team.ask("What was this company's annual revenue?", stock_context=ctx)

    assert "10,57,000" in res["answer"]


def test_c_tcs_quarterly_net_income():
    """TEST C: TCS.NS quarterly net income."""
    memory = FalkorMemory("ws", "user", DummyEmbeddings(), graph=MockGraph())
    llm = MockToolCallingLLM(
        tool_to_call="tool_income_statement",
        tool_args={"symbol": "TCS.NS", "period": "quarterly"},
        final_answer="TCS (TCS.NS) quarterly net income was ₹12,075 Crore."
    )
    team = FinanceAgentTeam(memory, llm)
    ctx = {"symbol": "TCS.NS", "companyName": "Tata Consultancy Services", "exchange": "NSE", "currency": "INR"}
    res = team.ask("What is its quarterly net income?", stock_context=ctx)

    assert "12,075" in res["answer"]


def test_d_aapl_historical_stock_prices():
    """TEST D: AAPL historical stock prices."""
    memory = FalkorMemory("ws", "user", DummyEmbeddings(), graph=MockGraph())
    llm = MockToolCallingLLM(
        tool_to_call="tool_historical_prices",
        tool_args={"symbol": "AAPL", "range": "1mo"},
        final_answer="Apple Inc (AAPL) 1-month range is $220.50 - $235.00."
    )
    team = FinanceAgentTeam(memory, llm)
    ctx = {"symbol": "AAPL", "companyName": "Apple Inc", "exchange": "US", "currency": "USD"}
    res = team.ask("What are the historical prices for AAPL?", stock_context=ctx)

    assert "$220.50" in res["answer"]


def test_e_consecutive_questions_same_stock():
    """TEST E: Ask two consecutive questions about the same stock."""
    memory = FalkorMemory("ws", "user", DummyEmbeddings(), graph=MockGraph())
    llm = MockToolCallingLLM(
        tool_to_call="tool_stock_price",
        tool_args={"symbol": "HDFCBANK.NS"},
        final_answer="HDFC Bank price is ₹728.85."
    )
    team = FinanceAgentTeam(memory, llm)
    ctx = {"symbol": "HDFCBANK.NS", "companyName": "HDFC Bank", "exchange": "NSE", "currency": "INR"}

    res1 = team.ask("What is the stock price?", stock_context=ctx, thread_id="thread_e")
    assert "₹728.85" in res1["answer"]

    # Second question on same thread
    res2 = team.ask("What was yesterday close?", stock_context=ctx, thread_id="thread_e")
    assert res2["answer"] is not None


def test_f_switch_from_hdfc_to_aapl():
    """TEST F: Switch from HDFCBANK.NS to AAPL and ask another question."""
    memory = FalkorMemory("ws", "user", DummyEmbeddings(), graph=MockGraph())
    llm = MockToolCallingLLM(
        tool_to_call="tool_stock_price",
        tool_args={"symbol": "AAPL"},
        final_answer="Apple (AAPL) price is $232.50."
    )
    team = FinanceAgentTeam(memory, llm)

    ctx_hdfc = {"symbol": "HDFCBANK.NS", "companyName": "HDFC Bank", "exchange": "NSE", "currency": "INR"}
    team.ask("Price of HDFC?", stock_context=ctx_hdfc)

    # Switch to Apple
    ctx_aapl = {"symbol": "AAPL", "companyName": "Apple Inc", "exchange": "US", "currency": "USD"}
    res_aapl = team.ask("What is the price of this company?", stock_context=ctx_aapl)

    assert "$232.50" in res_aapl["answer"]
    assert res_aapl["ticker"] == "AAPL"


def test_g_question_requiring_no_financial_api():
    """TEST G: Verify a question that requires no financial API."""
    memory = FalkorMemory("ws", "user", DummyEmbeddings(), graph=MockGraph())
    llm = MockToolCallingLLM(
        tool_to_call=None,  # No tool call needed
        tool_args={},
        final_answer="Compound interest is interest calculated on the initial principal and accumulated interest."
    )
    team = FinanceAgentTeam(memory, llm)
    ctx = {"symbol": "HDFCBANK.NS", "companyName": "HDFC Bank", "exchange": "NSE", "currency": "INR"}
    res = team.ask("How does compound interest work?", stock_context=ctx)

    assert "Compound interest" in res["answer"]


def test_h_simulated_unavailable_financial_data():
    """TEST H: Simulate unavailable financial data."""
    # When tool returns error JSON, agent gracefully handles without crashing
    res_json = tool_stock_price.invoke({"symbol": "NONEXISTENT_TICKER_12345"})
    parsed = json.loads(res_json)
    assert "error" in parsed or "symbol" in parsed


def test_i_errors_do_not_crash_workflow():
    """TEST I: Verify errors (like 400 tool_use_failed) do not crash the entire agent workflow."""
    class FailingToolLLM:
        def __init__(self):
            self.attempt = 0

        def bind_tools(self, tools, **kwargs):
            return self

        def invoke(self, messages):
            self.attempt += 1
            if self.attempt == 1:
                # Simulate the exact 400 error reported by user
                raise RuntimeError("Error code: 400 {'message': 'Tool choice is none, but model called a tool'}")
            return AIMessage(content="Fallback response: HDFC Bank price is ₹728.85 based on backend data.")

    memory = FalkorMemory("ws", "user", DummyEmbeddings(), graph=MockGraph())
    team = FinanceAgentTeam(memory, FailingToolLLM())
    ctx = {"symbol": "HDFCBANK.NS", "companyName": "HDFC Bank", "exchange": "NSE", "currency": "INR"}

    # Must NOT raise unhandled exception; must smoothly fall back to deterministic retrieval
    res = team.ask("What is the stock price?", stock_context=ctx)
    assert res["answer"] is not None
    assert "728.85" in res["answer"] or "price" in res["answer"].lower()


def test_j_no_sec_edgar_or_fmp_tools_registered():
    """TEST J: Verify no SEC EDGAR or FMP tools remain registered."""
    memory = FalkorMemory("ws", "user", DummyEmbeddings(), graph=MockGraph())
    team = FinanceAgentTeam(memory, None)

    registered_names = [t.name for t in team.tools]
    for name in registered_names:
        assert "sec" not in name.lower(), f"Unexpected SEC tool found: {name}"
        assert "edgar" not in name.lower(), f"Unexpected EDGAR tool found: {name}"
        assert "fmp" not in name.lower(), f"Unexpected FMP tool found: {name}"


def test_deterministic_backend_retrieval_functions():
    """Verify deterministic_fetch_financial_data correctly retrieves data for keywords."""
    res_price = deterministic_fetch_financial_data("What is the current stock price?", "HDFCBANK.NS")
    assert "current_price" in res_price
    assert res_price["current_price"]["symbol"] == "HDFCBANK.NS"

    res_inc = deterministic_fetch_financial_data("What is the annual revenue?", "RELIANCE.NS")
    assert "annual_income_statement" in res_inc
