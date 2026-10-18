"""Validation tests for ChatGPT-style conversation threads, shared knowledge store,

Excel cleanup, and username removal.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
from threading import Lock

import pytest
from streamlit.testing.v1 import AppTest

from falkor_memory import FalkorMemory


class FakeEmbedding:
    def embed_query(self, text):
        return [float(len(text) % 10), 0.1, 0.2]


class FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class MockFalkorGraph:
    """Mock graph providing thread, handoff, and document storage in-memory for unit testing."""
    def __init__(self):
        self.calls = []
        self.index_calls = []
        self.docs = []
        self.memories = []
        self.threads = {}
        self.handoffs = {}
        self.lock = Lock()

    def create_node_vector_index(self, label, prop, **kwargs):
        self.index_calls.append((label, prop, kwargs))

    def query(self, cypher, params=None):
        params = params or {}
        with self.lock:
            self.calls.append(("write", cypher, params))
            if "FinancialChunk" in cypher:
                self.docs.append(params)
            elif "MERGE (m:Memory" in cypher:
                self.memories.append(params)
            elif "DETACH DELETE" in cypher:
                th_id = params.get("thread_id")
                if th_id:
                    self.threads.pop(th_id, None)
            elif "Thread" in cypher:
                th_id = params.get("thread_id", "")
                if th_id:
                    self.threads[th_id] = {**self.threads.get(th_id, {}), **params}
            elif "h.status='claimed'" in cypher and "RETURN h.payload" in cypher:
                hid = params.get("handoff_id")
                payload = self.handoffs.get(hid, {}).get("payload", "{}")
                return FakeResult([[payload]])
            elif "CREATE (h:Handoff" in cypher or "MERGE (h:Handoff" in cypher:
                self.handoffs[params.get("handoff_id", "h1")] = params
            return FakeResult([[params.get("memory_id", params.get("doc_id", params.get("thread_id", "ok")))]])

    def ro_query(self, cypher, params=None):
        params = params or {}
        with self.lock:
            self.calls.append(("read", cypher, params))
            if "RETURN th.ticker" in cypher:
                th_id = params.get("thread_id", "")
                ticker = self.threads.get(th_id, {}).get("ticker", "")
                return FakeResult([[ticker]])
            elif "MATCH (u:User {id:$user_id})-[:HAS_THREAD]->(th:Thread)" in cypher and "RETURN th.id" in cypher:
                rows = []
                for tid, data in self.threads.items():
                    rows.append([tid, data.get("title", "New Chat"), data.get("now", ""), data.get("now", ""), data.get("ticker", "")])
                return FakeResult(rows)
            elif "CALL db.idx.vector.queryNodes('FinancialChunk'" in cypher:
                ticker = params.get("ticker", "")
                res = []
                for d in self.docs:
                    if d.get("ticker") == ticker:
                        res.append([d.get("content", ""), d.get("source", ""), ticker, 0.95])
                return FakeResult(res)
            elif "CALL db.idx.vector.queryNodes('Memory'" in cypher:
                ticker = params.get("ticker", "")
                th_id = params.get("thread_id", "")
                res = []
                for m in self.memories:
                    m_kind = m.get("kind", "")
                    m_th = m.get("thread_id", "")
                    if m_kind == "episodic" and th_id and m_th and m_th != th_id:
                        continue
                    if not m.get("ticker") or m.get("ticker") == ticker:
                        res.append([m_kind, m.get("key", ""), m.get("content", ""), m.get("ticker", ""), 0.92])
                return FakeResult(res)
            elif "MATCH (u:User {id:$user_id})-[:HAS_HANDOFF]->(h:Handoff)" in cypher:
                th_id = params.get("thread_id", "")
                res = []
                for hid, h in self.handoffs.items():
                    if th_id and h.get("thread_id") and h.get("thread_id") != th_id:
                        continue
                    res.append([hid, h.get("thread_id", ""), h.get("payload", "{}"), h.get("from_agent", "")])
                return FakeResult(res)
        return FakeResult([])


@pytest.fixture
def mem():
    g = MockFalkorGraph()
    return FalkorMemory("test_tenant", "user_123", FakeEmbedding(), graph=g, vector_dimension=3)


def test_1_thread_creation(mem):
    """TEST 1: Creating a thread returns id, default title, and timestamps."""
    th = mem.create_thread("Analysis Thread")
    assert th["id"]
    assert th["title"] == "Analysis Thread"
    assert "created_at" in th
    assert "updated_at" in th

    th_default = mem.create_thread()
    assert th_default["title"] == "New Chat"


def test_2_thread_isolation(mem):
    """TEST 2: Separate threads maintain strictly isolated message histories."""
    th1 = mem.create_thread("Thread 1")["id"]
    th2 = mem.create_thread("Thread 2")["id"]

    mem.append_turn(th1, "user", "Hello in thread 1")
    mem.append_turn(th1, "assistant", "Response in thread 1")

    mem.append_turn(th2, "user", "Question in thread 2")

    turns1 = mem.recent_turns(th1)
    turns2 = mem.recent_turns(th2)

    assert len(turns1) == 2
    assert [t["content"] for t in turns1] == ["Hello in thread 1", "Response in thread 1"]

    assert len(turns2) == 1
    assert [t["content"] for t in turns2] == ["Question in thread 2"]

    # No leakage between threads
    assert "Question in thread 2" not in [t["content"] for t in turns1]
    assert "Hello in thread 1" not in [t["content"] for t in turns2]


def test_3_thread_listing_and_sorting(mem):
    """TEST 3: Threads list is sorted by recent activity."""
    th1 = mem.create_thread("Old Thread")["id"]
    th2 = mem.create_thread("Recent Thread")["id"]

    # Updating th1 by appending a turn moves it to most recent
    mem.append_turn(th1, "user", "New activity in old thread")

    threads = mem.list_threads()
    assert len(threads) >= 2
    assert threads[0]["id"] == th1


def test_4_thread_rename_and_auto_title(mem):
    """TEST 4: Thread can be renamed explicitly or auto-titled from user prompt."""
    th = mem.create_thread("New Chat")["id"]

    mem.rename_thread(th, "Custom Analysis")
    threads = mem.list_threads()
    target = next(t for t in threads if t["id"] == th)
    assert target["title"] == "Custom Analysis"

    # Auto-title from user prompt
    generated = mem.auto_title_thread(th, "What was Reliance Q3 operating margin?")
    assert "Reliance" in generated or "Margin" in generated
    assert len(generated) <= 35


def test_5_shared_knowledge_store_across_threads(mem):
    """TEST 5: All threads access the same shared FalkorDB financial knowledge store."""
    th1 = mem.create_thread("Research Thread")["id"]
    th2 = mem.create_thread("Review Thread")["id"]

    # Index document in the shared store
    doc_id = mem.upsert_document(
        "Reliance Industries operating revenue reached 10,57,000 Cr in FY24.",
        ticker="RELIANCE.NS",
        source="annual_report",
        thread_id=th1,
    )
    assert doc_id

    # Store a memory in the shared store
    mem.remember(
        "semantic",
        "Prefers consolidated figures for Reliance.",
        key="reliance_pref",
        ticker="RELIANCE.NS",
        thread_id=th1,
    )

    # In Thread 2 (completely separate conversation), retrieve financial facts and memories
    retrieved_docs = mem.search_documents("What was operating revenue?", ticker="RELIANCE.NS")
    assert len(retrieved_docs) >= 1
    assert any("10,57,000 Cr" in d["content"] for d in retrieved_docs)

    retrieved_mems = mem.search_memories("figures preference", ticker="RELIANCE.NS")
    assert len(retrieved_mems) >= 1
    assert any("consolidated figures" in m["content"] for m in retrieved_mems)


def test_6_thread_deletion_preserves_shared_knowledge(mem):
    """TEST 6: Deleting a thread deletes its conversation turns but leaves shared documents and memories intact."""
    th1 = mem.create_thread("Temporary Thread")["id"]
    th2 = mem.create_thread("Permanent Thread")["id"]

    # Append turns in Thread 1
    mem.append_turn(th1, "user", "Ephemeral user query")
    mem.append_turn(th1, "assistant", "Ephemeral answer")

    # Add shared financial knowledge
    mem.upsert_document(
        "TCS recorded net profit of 46,000 Cr in FY24.",
        ticker="TCS.NS",
        source="financial_statement",
        thread_id=th1,
    )
    mem.remember(
        "semantic",
        "TCS fiscal year closes in March.",
        key="tcs_fiscal_close",
        ticker="TCS.NS",
        thread_id=th1,
    )

    # Delete Thread 1
    deleted = mem.delete_thread(th1)
    assert deleted is True

    # Thread 1 turns are gone
    assert mem.recent_turns(th1) == []
    threads = mem.list_threads()
    assert th1 not in [t["id"] for t in threads]

    # Thread 2 still exists
    assert th2 in [t["id"] for t in threads]

    # Shared financial chunk and memory for TCS are STILL PRESENT and accessible by Thread 2
    docs = mem.search_documents("TCS profit", ticker="TCS.NS")
    assert len(docs) >= 1
    assert any("46,000 Cr" in d["content"] for d in docs)

    mems = mem.search_memories("fiscal year close", ticker="TCS.NS")
    assert len(mems) >= 1
    assert any("March" in m["content"] for m in mems)


def test_7_concurrent_thread_turns(mem):
    """TEST 7: Concurrent turns across multiple threads remain isolated and uncorrupted."""
    th_ids = [mem.create_thread(f"Thread {i}")["id"] for i in range(5)]

    def add_turns(idx):
        th = th_ids[idx]
        for step in range(3):
            mem.append_turn(th, "user", f"Turn {step} for thread {idx}")

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(add_turns, range(5)))

    for idx, th in enumerate(th_ids):
        turns = mem.recent_turns(th, limit=10)
        assert len(turns) == 3
        for step, t in enumerate(turns):
            assert t["content"] == f"Turn {step} for thread {idx}"


def test_8_excel_section_removed():
    """TEST 8: Verify all Excel upload UI, parsing functions, and dependencies are completely removed."""
    # Check deleted files
    assert not os.path.exists("data_frame_functions.py")
    assert not os.path.exists("date_functions.py")

    # Check final.py content
    with open("final.py", "r", encoding="utf-8") as f:
        final_code = f.read()

    forbidden_tokens = [
        "data_frame_functions",
        "date_functions",
        "file_uploader",
        "BalanceSheetColumnMap",
        "read_excel",
        "SQLDatabaseToolkit",
        "Upload Excel/CSV",
        "quaterly_report_file",
        "sqlite3",
    ]
    for token in forbidden_tokens:
        assert token not in final_code, f"Found forbidden Excel token in final.py: {token}"

    # Check requirements.txt
    with open("requirements.txt", "r", encoding="utf-8") as f:
        reqs = f.read().lower()
    assert "openpyxl" not in reqs
    assert "xlrd" not in reqs


def test_9_username_display_removed():
    """TEST 9: Verify username is not displayed on the user interface."""
    with open("final.py", "r", encoding="utf-8") as f:
        final_code = f.read()

    # Should not have UI input asking for User ID
    assert 'st.text_input("User ID"' not in final_code
    assert "st.text_input('User ID'" not in final_code
    assert "st.caption(\"User ID" not in final_code


def test_10_final_app_renders_cleanly():
    """TEST 10: AppTest verifies final.py renders with full-width conversation navigation and dashboard."""
    at = AppTest.from_file("../final.py", default_timeout=45)
    at.run()
    assert len(at.exception) == 0, f"App execution failed: {[e.value for e in at.exception]}"

    # Verify sidebar is completely removed (TEST 18)
    assert len(at.sidebar.button) == 0

    # Verify main page conversation controls
    buttons = [b.label for b in at.button]
    assert "➕ New Chat" in buttons

    # Verify main page headers
    subheaders = [s.value for s in at.subheader]
    assert "Selected Stock Context" in subheaders
    assert "Stock Price" in subheaders
    assert "Financial Statements" in subheaders


class MockChatLLM:
    """Mock LLM for conversation thread testing."""
    def __init__(self):
        self.calls = []

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        user_msg = messages[-1].content if hasattr(messages[-1], "content") else str(messages[-1])
        sys_msg = messages[0].content if hasattr(messages[0], "content") else str(messages[0])

        class MockResp:
            def __init__(self, content):
                self.content = content
                self.tool_calls = []

        if "Apple" in user_msg or "AAPL" in sys_msg:
            return MockResp("Apple reported revenue and net profit growth in 2023 and 2024.")
        elif "Microsoft" in user_msg or "MSFT" in sys_msg:
            return MockResp("Microsoft demonstrated strong cloud earnings in 2023 and 2024.")
        return MockResp("General financial response.")


def test_11_new_chat_memory_isolation_and_restoration(mem):
    """TEST 11:
    1. Chat 1 discusses Apple -> establishes Apple ticker in Chat 1.
    2. New Chat (Chat 2) started -> asks 'What was its net profit in 2023?' ->
       Zero Apple leakage; prompts user 'No valid stock is currently selected. Which company are you referring to?'.
    3. In Chat 2, user asks 'Analyze Microsoft' -> establishes Microsoft ticker in Chat 2.
    4. Switching back to Chat 1 -> asking 'What about 2024?' preserves and answers for Apple.
    5. Switching back to Chat 2 -> asking 'What about 2024?' preserves and answers for Microsoft.
    6. All conversation records and memories for both threads remain preserved in FalkorDB.
    """
    from memory_agents import FinanceAgentTeam
    mock_llm = MockChatLLM()
    team = FinanceAgentTeam(mem, mock_llm)

    # 1. Chat 1 discusses Apple
    th1 = mem.create_thread("Chat 1")["id"]
    res1 = team.ask("Analyze Apple", thread_id=th1)
    assert "Apple" in res1["answer"] or "AAPL" in res1["answer"]
    assert res1.get("ticker") == "AAPL"
    assert mem.get_thread_ticker(th1) == "AAPL"

    # 2. User starts a fresh "New Chat" (Chat 2)
    th2 = mem.create_thread("New Chat", ticker="")["id"]
    assert mem.get_thread_ticker(th2) == ""

    # User asks follow-up referencing 'its' in Chat 2
    res2 = team.ask("What was its net profit in 2023?", thread_id=th2)
    # Zero memory leakage: Agent must NOT answer with Apple!
    assert "no valid stock is currently selected" in res2["answer"].lower()
    assert "which company are you referring to" in res2["answer"].lower()
    assert "AAPL" not in res2["answer"]
    assert "Apple" not in res2["answer"]

    # 3. User discusses Microsoft in Chat 2
    res3 = team.ask("Analyze Microsoft", thread_id=th2)
    assert "Microsoft" in res3["answer"] or "MSFT" in res3["answer"]
    assert res3.get("ticker") == "MSFT"
    assert mem.get_thread_ticker(th2) == "MSFT"

    # 4. User switches back to Chat 1 and asks follow-up
    assert mem.get_thread_ticker(th1) == "AAPL"
    res4 = team.ask("What about 2024?", thread_id=th1)
    assert res4.get("ticker") == "AAPL"
    assert "Apple" in res4["answer"] or "AAPL" in res4["answer"]
    assert "MSFT" != res4.get("ticker")

    # 5. User switches back to Chat 2 and asks follow-up
    assert mem.get_thread_ticker(th2) == "MSFT"
    res5 = team.ask("What about 2024?", thread_id=th2)
    assert res5.get("ticker") == "MSFT"
    assert "Microsoft" in res5["answer"] or "MSFT" in res5["answer"]
    assert "AAPL" != res5.get("ticker")

    # 6. Verify full persistence in FalkorDB (no deletions)
    turns1 = mem.recent_turns(th1)
    assert len(turns1) >= 4
    turns2 = mem.recent_turns(th2)
    assert len(turns2) >= 6

    # Both threads exist in list_threads
    threads = mem.list_threads()
    assert any(t["id"] == th1 for t in threads)
    assert any(t["id"] == th2 for t in threads)

