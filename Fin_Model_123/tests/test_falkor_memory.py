"""Contract tests run without a network, real LLM, or FalkorDB server."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pytest

from falkor_memory import FalkorMemory, _graph_name


class FakeEmbedding:
    def embed_query(self, text):
        return [float(len(text) % 10), 0.1, 0.2]


class FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class FakeGraph:
    def __init__(self):
        self.calls = []
        self.index_calls = []
        self.claims = {}
        self.lock = Lock()

    def create_node_vector_index(self, label, prop, **kwargs):
        self.index_calls.append((label, prop, kwargs))

    def query(self, cypher, params=None):
        params = params or {}
        with self.lock:
            self.calls.append(("write", cypher, params))
            if "h.status='pending'" in cypher and "claimed_at" in cypher:
                handoff_id = params["handoff_id"]
                if handoff_id not in self.claims:
                    self.claims[handoff_id] = True
                    return FakeResult([['{"question": "sample"}']])
                return FakeResult([])
            if "RETURN h.id" in cypher and "h.status='claimed'" in cypher:
                return FakeResult([[params["handoff_id"]]])
            return FakeResult([[params.get("memory_id", params.get("doc_id", "ok"))]])

    def ro_query(self, cypher, params=None):
        with self.lock:
            self.calls.append(("read", cypher, params or {}))
        return FakeResult([])


@pytest.fixture()
def memory():
    g = FakeGraph()
    return FalkorMemory("workspace_a", "alice", FakeEmbedding(),
                        graph=g, vector_dimension=3)


def test_workspace_isolation_and_safe_graph_name():
    assert _graph_name("workspace_a") != _graph_name("workspace_b")
    assert _graph_name("bad-name\" <> !")[:15] == "finance_tenant_"
    assert len(_graph_name("workspace_a")) == 39


def test_vector_indexes_and_parameters(memory):
    assert [r[0] for r in memory.graph.index_calls] == ["Memory", "FinancialChunk"]
    id1 = memory.upsert_document("Sales: 10", ticker="TCS", source="annual")
    id2 = memory.upsert_document("Sales: 10", ticker="TCS", source="annual")
    assert id1 == id2
    _, statement, params = memory.graph.calls[-1]
    assert "$user_id" in statement and "$embedding" in statement
    assert "Sales: 10" not in statement  # Parameterized, not interpolated Cypher
    assert params["user_id"] == "alice"
    assert params["ticker"] == "TCS"
    assert len(params["embedding"]) == 3


def test_semantic_updates_use_stable_id_and_revisions(memory):
    a = memory.remember("semantic", "Prefer ratios", key="preference", ticker="tcs")
    b = memory.remember("semantic", "Prefer margins", key="preference", ticker="TCS")
    assert a == b
    statements = [c[1] for c in memory.graph.calls]
    assert any("MemoryRevision" in s for s in statements)
    c = memory.remember("episodic", "Met analyst")
    d = memory.remember("episodic", "Met analyst")
    assert c != d
    with pytest.raises(ValueError):
        memory.remember("procedural", "Do X")


def test_history_and_retrieval_restrict_user_and_ticker(memory):
    memory.append_turn("thread-1", "user", "hello")
    memory.recent_turns("thread-1")
    memory.search_documents("risk", ticker="TCS")
    memory.search_memories("preference", ticker="TCS")
    for direction, query, params in memory.graph.calls:
        assert "$user_id" in query and params["user_id"] == "alice"
    assert any("node.ticker=$ticker" in q for _, q, _ in memory.graph.calls)


def test_handoffs_only_one_claim_under_concurrent_calls(memory):
    hid = memory.create_handoff(from_agent="researcher", to_agent="analyst",
                                thread_id="thread-1", payload={"question": "sample"})
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: memory.claim_handoff(hid, "analyst"), range(100)))
    assert sum(x is not None for x in results) == 1
    assert memory.complete_handoff(hid, "analyst")


def test_empty_user_and_tenant_rejected():
    with pytest.raises(ValueError):
        FalkorMemory("", "bob", FakeEmbedding(), graph=FakeGraph(), vector_dimension=3)
    with pytest.raises(ValueError):
        FalkorMemory("workspace", "", FakeEmbedding(), graph=FakeGraph(), vector_dimension=3)


def test_embedding_dimension_mismatch():
    g = FakeGraph()
    db = FalkorMemory("workspace", "bob", FakeEmbedding(), graph=g, vector_dimension=384)
    with pytest.raises(ValueError, match="dimensions"):
        db.upsert_document("doc", ticker="ABC", source="uploaded")
