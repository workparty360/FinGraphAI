from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any, Literal
from uuid import uuid4

MemoryKind = Literal["episodic", "semantic", "procedural"]

# Configurable memory retention limits
MAX_RECENT_CHAT_TURNS = int(os.getenv("MAX_RECENT_CHAT_TURNS", "20"))
MAX_EPISODIC_MEMORIES = int(os.getenv("MAX_EPISODIC_MEMORIES", "100"))
MAX_SEMANTIC_MEMORIES = int(os.getenv("MAX_SEMANTIC_MEMORIES", "100"))
MAX_PROCEDURAL_MEMORIES = int(os.getenv("MAX_PROCEDURAL_MEMORIES", "30"))

# Content-based hash cache to prevent re-generating embeddings for identical text
_embed_cache: dict[str, list[float]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scoped_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _graph_name(tenant_id: str) -> str:
    if not tenant_id.strip():
        raise ValueError("tenant_id is required")
    return "finance_tenant_" + _scoped_id(tenant_id)[:24]


class FalkorMemory:
    def __init__(
        self,
        tenant_id: str,
        user_id: str,
        embeddings: Any,
        *,
        graph: Any | None = None,
        vector_dimension: int = 384,
    ):
        if not user_id.strip():
            raise ValueError("user_id is required")
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.embeddings = embeddings
        self.vector_dimension = vector_dimension
        self.graph_name = _graph_name(tenant_id)

        if graph is None:
            from falkordb import FalkorDB

            falkor_ssl = os.getenv("FALKORDB_SSL", "false").lower() in ("true", "1", "yes")
            client = FalkorDB(
                host=os.getenv("FALKORDB_HOST", "localhost"),
                port=int(os.getenv("FALKORDB_PORT", "6379")),
                username=os.getenv("FALKORDB_USERNAME") or None,
                password=os.getenv("FALKORDB_PASSWORD") or None,
                ssl=falkor_ssl,
            )
            graph = client.select_graph(self.graph_name)

        self.graph = graph
        self._ensure_indexes()
        self._local_threads: dict[str, dict] = {}
        self._local_turns: dict[str, list[dict]] = {}

        # Initialize service dependency graph on the same FalkorDB connection
        # Uses a separate graph namespace (FALKORDB_GRAPH env var, default: service_dependencies)
        try:
            from service_graph import get_service_graph

            if graph is None:
                # graph was set from client above — reinitialise service graph with same client
                svc_graph_name = os.getenv("FALKORDB_GRAPH", "service_dependencies")
                svc_graph = client.select_graph(svc_graph_name)  # type: ignore[possibly-undefined]
            else:
                svc_graph = None  # test/mock mode — no real graph
            self.service_graph = get_service_graph(graph=svc_graph)
        except Exception:
            # ServiceGraph is non-critical — FalkorMemory continues without it
            from service_graph import get_service_graph

            self.service_graph = get_service_graph(graph=None)

    def _ensure_indexes(self):
        for label in ("Memory", "FinancialChunk"):
            try:
                self.graph.create_node_vector_index(
                    label,
                    "embedding",
                    dim=self.vector_dimension,
                    similarity_function="cosine",
                )
            except Exception as exc:
                msg = str(exc).lower()
                if not any(
                    token in msg
                    for token in ("already exists", "already indexed", "already has an index")
                ):
                    raise

        if hasattr(self.graph, "create_node_range_index"):
            range_indexes = [
                ("User", "id"),
                ("Thread", "id"),
                ("Session", "id"),
                ("Memory", "id"),
                ("Memory", "user_id"),
                ("Memory", "ticker"),
                ("FinancialChunk", "id"),
                ("FinancialChunk", "ticker"),
                ("FinancialChunk", "period"),
                ("Handoff", "id"),
                ("Handoff", "to_agent"),
            ]
            for label, prop in range_indexes:
                try:
                    self.graph.create_node_range_index(label, prop)
                except Exception:
                    pass

    def _query(self, cypher: str, **params: Any) -> list:
        return self.graph.query(cypher, params=params).result_set

    def _read(self, cypher: str, **params: Any) -> list:
        return self.graph.ro_query(cypher, params=params).result_set

    def _embed(self, text: str) -> list[float]:
        if self.embeddings is None:
            return [0.0] * self.vector_dimension
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if content_hash in _embed_cache:
            return _embed_cache[content_hash]
        vector = [float(v) for v in self.embeddings.embed_query(text)]
        if len(vector) != self.vector_dimension:
            raise ValueError(
                f"Embedding model produced {len(vector)} dimensions, expected {self.vector_dimension}"
            )
        if len(_embed_cache) < 2000:
            _embed_cache[content_hash] = vector
        return vector

    def remember(
        self,
        kind: MemoryKind,
        content: str,
        *,
        key: str = "",
        agent_id: str = "memory_curator",
        thread_id: str = "",
        ticker: str = "",
        source_turn_id: str = "",
    ) -> str:
        if kind not in ("episodic", "semantic", "procedural"):
            raise ValueError("Unknown memory kind")
        if not content.strip():
            raise ValueError("Cannot store empty memory")
        if kind != "episodic" and not key.strip():
            raise ValueError("semantic/procedural memories require a stable key")

        ticker = ticker.strip().upper()
        memory_id = (
            uuid4().hex
            if kind == "episodic"
            else _scoped_id(self.tenant_id, self.user_id, ticker, kind, key.strip().lower())
        )
        revision_id = uuid4().hex

        self._query(
            """
            MERGE (u:User {id:$user_id})
            MERGE (a:Agent {id:$agent_id})
            MERGE (m:Memory {id:$memory_id})
            ON CREATE SET m.created_at=$now, m.version=0
            SET m.user_id=$user_id, m.tenant_id=$tenant_id,
                m.kind=$kind, m.key=$key, m.content=$content,
                m.ticker=$ticker, m.thread_id=$thread_id,
                m.source_turn_id=$source_turn_id, m.updated_at=$now,
                m.embedding=vecf32($embedding), m.version=m.version+1
            MERGE (u)-[:OWNS_MEMORY]->(m)
            MERGE (a)-[:WROTE_MEMORY]->(m)
            CREATE (r:MemoryRevision {id:$revision_id, content:$content,
                updated_at:$now, agent_id:$agent_id, source_turn_id:$source_turn_id})
            CREATE (m)-[:HAS_REVISION]->(r)
            RETURN m.id
            """,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            agent_id=agent_id,
            memory_id=memory_id,
            kind=kind,
            key=key.strip().lower(),
            content=content.strip(),
            ticker=ticker,
            thread_id=thread_id,
            source_turn_id=source_turn_id,
            now=_now(),
            embedding=self._embed(content),
            revision_id=revision_id,
        )

        # Enforce memory retention limits per user and tenant
        max_limit = (
            MAX_EPISODIC_MEMORIES
            if kind == "episodic"
            else (MAX_SEMANTIC_MEMORIES if kind == "semantic" else MAX_PROCEDURAL_MEMORIES)
        )
        try:
            self._query(
                """
                MATCH (u:User {id:$user_id})-[:OWNS_MEMORY]->(m:Memory {kind:$kind, user_id:$user_id, tenant_id:$tenant_id})
                WITH m ORDER BY m.updated_at DESC
                SKIP $max_limit
                OPTIONAL MATCH (m)-[:HAS_REVISION]->(r:MemoryRevision)
                DETACH DELETE m, r
                """,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                kind=kind,
                max_limit=max_limit,
            )
        except Exception:
            pass

        return memory_id

    def create_thread(
        self,
        title: str = "New Chat",
        thread_id: str | None = None,
        ticker: str = "",
    ) -> dict:
        th_id = thread_id.strip() if thread_id and thread_id.strip() else uuid4().hex
        clean_title = (title.strip() if title else "") or "New Chat"
        clean_ticker = ticker.strip().upper() if ticker else ""
        now_ts = _now()
        self._local_threads[th_id] = {
            "id": th_id,
            "title": clean_title,
            "ticker": clean_ticker,
            "created_at": now_ts,
            "updated_at": now_ts,
        }
        try:
            self._query(
                """
                MERGE (u:User {id:$user_id})
                MERGE (th:Thread {id:$thread_id})
                ON CREATE SET th.created_at=$now
                SET th.user_id=$user_id, th.tenant_id=$tenant_id,
                    th.title=$title, th.ticker=$ticker, th.updated_at=$now
                MERGE (u)-[:HAS_THREAD]->(th)
                RETURN th.id
                """,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                thread_id=th_id,
                title=clean_title,
                ticker=clean_ticker,
                now=now_ts,
            )
        except Exception:
            pass
        return {
            "id": th_id,
            "title": clean_title,
            "ticker": clean_ticker,
            "created_at": now_ts,
            "updated_at": now_ts,
        }

    def get_thread_ticker(self, thread_id: str) -> str:
        if not thread_id.strip():
            return ""
        th_id = thread_id.strip()
        try:
            rows = self._read(
                """
                MATCH (u:User {id:$user_id})-[:HAS_THREAD]->(th:Thread {id:$thread_id})
                WHERE th.user_id=$user_id AND th.tenant_id=$tenant_id
                RETURN th.ticker
                """,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                thread_id=th_id,
            )
            if rows and rows[0] and rows[0][0]:
                return str(rows[0][0])
        except Exception:
            pass
        return self._local_threads.get(th_id, {}).get("ticker", "")

    def set_thread_ticker(self, thread_id: str, ticker: str) -> bool:
        if not thread_id.strip():
            return False
        th_id = thread_id.strip()
        clean_ticker = ticker.strip().upper() if ticker else ""
        now_ts = _now()
        if th_id in self._local_threads:
            self._local_threads[th_id]["ticker"] = clean_ticker
            self._local_threads[th_id]["updated_at"] = now_ts
        else:
            self._local_threads[th_id] = {
                "id": th_id,
                "title": "New Chat",
                "ticker": clean_ticker,
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        try:
            rows = self._query(
                """
                MATCH (u:User {id:$user_id})-[:HAS_THREAD]->(th:Thread {id:$thread_id})
                WHERE th.user_id=$user_id AND th.tenant_id=$tenant_id
                SET th.ticker=$ticker, th.updated_at=$now
                RETURN th.id
                """,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                thread_id=th_id,
                ticker=clean_ticker,
                now=now_ts,
            )
            return bool(rows)
        except Exception:
            return th_id in self._local_threads

    def list_threads(self, limit: int = 50) -> list[dict]:
        try:
            rows = self._read(
                """
                MATCH (u:User {id:$user_id})-[:HAS_THREAD]->(th:Thread)
                WHERE th.user_id=$user_id AND th.tenant_id=$tenant_id
                RETURN th.id, th.title, th.created_at, th.updated_at, th.ticker
                ORDER BY th.updated_at DESC LIMIT $limit
                """,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                limit=int(limit),
            )
            if rows:
                return [
                    {
                        "id": row[0],
                        "title": row[1] or "New Chat",
                        "created_at": row[2],
                        "updated_at": row[3],
                        "ticker": row[4] if len(row) > 4 and row[4] else "",
                    }
                    for row in rows
                ]
        except Exception:
            pass
        # Fallback to local thread store
        threads = sorted(
            self._local_threads.values(),
            key=lambda t: t.get("updated_at", ""),
            reverse=True,
        )
        return threads[:limit]

    def rename_thread(self, thread_id: str, new_title: str) -> bool:
        if not thread_id.strip() or not new_title.strip():
            return False
        clean_title = new_title.strip()
        now_ts = _now()
        if thread_id in self._local_threads:
            self._local_threads[thread_id]["title"] = clean_title
            self._local_threads[thread_id]["updated_at"] = now_ts
        try:
            rows = self._query(
                """
                MATCH (u:User {id:$user_id})-[:HAS_THREAD]->(th:Thread {id:$thread_id})
                WHERE th.user_id=$user_id AND th.tenant_id=$tenant_id
                SET th.title=$title, th.updated_at=$now
                RETURN th.id
                """,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                thread_id=thread_id.strip(),
                title=clean_title,
                now=now_ts,
            )
            return bool(rows)
        except Exception:
            return thread_id in self._local_threads

    def delete_thread(self, thread_id: str) -> bool:
        if not thread_id.strip():
            return False
        th_id = thread_id.strip()
        self._local_threads.pop(th_id, None)
        self._local_turns.pop(th_id, None)
        try:
            session_id = _scoped_id(self.tenant_id, self.user_id, th_id)
            self._query(
                """
                MATCH (u:User {id:$user_id})
                OPTIONAL MATCH (u)-[:HAS_THREAD]->(th:Thread {id:$thread_id})
                OPTIONAL MATCH (s:Session {id:$session_id})-[:CONTAINS]->(t:Turn)
                OPTIONAL MATCH (u)-[:HAS_HANDOFF]->(h:Handoff {thread_id:$thread_id})
                DETACH DELETE th, t, s, h
                """,
                user_id=self.user_id,
                tenant_id=self.tenant_id,
                thread_id=th_id,
                session_id=session_id,
            )
            return True
        except Exception:
            return True

    def auto_title_thread(self, thread_id: str, prompt_text: str) -> str:
        """Auto-generate a concise title from the user prompt and rename the thread."""
        if not thread_id.strip() or not prompt_text.strip():
            return "New Chat"
        clean = " ".join(prompt_text.strip().split())
        words = clean.split()
        if len(words) > 5:
            title = " ".join(words[:5]) + "..."
        else:
            title = clean[:32]
        title = title.strip().title()
        self.rename_thread(thread_id, title)
        return title

    def append_turn(
        self,
        thread_id: str,
        role: str,
        content: str,
        agent_id: str = "user",
    ) -> str:
        if not thread_id.strip() or not content.strip():
            raise ValueError("thread_id and content are required")

        turn_id = uuid4().hex
        now_ts = _now()

        # Update local caches for fast retrieval and test-runner compatibility
        if thread_id not in self._local_threads:
            self._local_threads[thread_id] = {
                "id": thread_id,
                "title": content[:30].strip().title() if role == "user" else "New Chat",
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        else:
            self._local_threads[thread_id]["updated_at"] = now_ts

        self._local_turns.setdefault(thread_id, []).append(
            {
                "role": role,
                "content": content,
                "agent": agent_id,
                "created_at": now_ts,
            }
        )

        self._query(
            """
            MERGE (u:User {id:$user_id})
            MERGE (th:Thread {id:$thread_id})
            ON CREATE SET th.title='New Chat', th.created_at=$now
            SET th.user_id=$user_id, th.tenant_id=$tenant_id, th.updated_at=$now
            MERGE (u)-[:HAS_THREAD]->(th)
            MERGE (s:Session {id:$session_id})
            ON CREATE SET s.created_at=$now
            SET s.user_id=$user_id, s.thread_id=$thread_id,
                s.tenant_id=$tenant_id
            MERGE (u)-[:HAS_SESSION]->(s)
            CREATE (t:Turn {id:$turn_id, user_id:$user_id,
                thread_id:$thread_id, role:$role, content:$content,
                agent_id:$agent_id, created_at:$now})
            CREATE (s)-[:CONTAINS]->(t)
            RETURN t.id
            """,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            session_id=_scoped_id(self.tenant_id, self.user_id, thread_id),
            thread_id=thread_id,
            role=role,
            content=content,
            agent_id=agent_id,
            turn_id=turn_id,
            now=now_ts,
        )

        # Enforce bounded session turns per thread
        try:
            self._query(
                """
                MATCH (s:Session {id:$session_id})-[:CONTAINS]->(t:Turn)
                WHERE s.user_id=$user_id AND t.user_id=$user_id
                WITH t ORDER BY t.created_at DESC
                SKIP $max_turns
                DETACH DELETE t
                """,
                user_id=self.user_id,
                session_id=_scoped_id(self.tenant_id, self.user_id, thread_id),
                max_turns=MAX_RECENT_CHAT_TURNS,
            )
        except Exception:
            pass

        return turn_id

    def recent_turns(self, thread_id: str, limit: int = 12) -> list[dict]:
        rows = self._read(
            """
            MATCH (s:Session {id:$session_id})-[:CONTAINS]->(t:Turn)
            WHERE s.user_id=$user_id AND t.user_id=$user_id
            RETURN t.role,t.content,t.agent_id,t.created_at
            ORDER BY t.created_at DESC LIMIT $limit
            """,
            session_id=_scoped_id(self.tenant_id, self.user_id, thread_id),
            user_id=self.user_id,
            limit=int(limit),
        )
        if rows:
            return [
                dict(zip(("role", "content", "agent", "created_at"), row)) for row in reversed(rows)
            ]
        # Fallback to local turns
        turns = self._local_turns.get(thread_id, [])
        return turns[-int(limit) :]

    def search_memories(
        self,
        question: str,
        *,
        ticker: str = "",
        thread_id: str = "",
        limit: int = 6,
    ) -> list[dict]:
        rows = self._read(
            """
            CALL db.idx.vector.queryNodes('Memory','embedding',$candidates,
                vecf32($embedding)) YIELD node, score
            WHERE node.user_id=$user_id AND node.tenant_id=$tenant_id
              AND (node.ticker='' OR node.ticker=$ticker)
              AND (node.kind <> 'episodic' OR node.thread_id=$thread_id OR $thread_id='')
            RETURN node.kind,node.key,node.content,node.ticker,score
            ORDER BY score DESC LIMIT $limit
            """,
            candidates=max(50, limit * 12),
            embedding=self._embed(question),
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            ticker=ticker.strip().upper(),
            thread_id=thread_id.strip(),
            limit=int(limit),
        )
        return [dict(zip(("kind", "key", "content", "ticker", "score"), row)) for row in rows]

    def list_memories(
        self,
        *,
        kind: str = "",
        ticker: str = "",
        limit: int = 30,
    ) -> list[dict]:
        rows = self._read(
            """
            MATCH (u:User {id:$user_id})-[:OWNS_MEMORY]->(m:Memory)
            WHERE m.user_id=$user_id AND m.tenant_id=$tenant_id
              AND ($kind='' OR m.kind=$kind)
              AND ($ticker='' OR m.ticker='' OR m.ticker=$ticker)
            RETURN m.id,m.kind,m.key,m.content,m.ticker,m.version,m.updated_at
            ORDER BY m.updated_at DESC LIMIT $limit
            """,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            kind=kind,
            ticker=ticker.strip().upper(),
            limit=int(limit),
        )
        return [
            dict(zip(("id", "kind", "key", "content", "ticker", "version", "updated_at"), row))
            for row in rows
        ]

    def upsert_document(
        self,
        content: str,
        *,
        ticker: str,
        source: str,
        thread_id: str = "",
        period: str = "",
    ) -> str:
        if not content.strip():
            raise ValueError("Cannot index an empty document")

        ticker = ticker.strip().upper()
        doc_id = _scoped_id(self.tenant_id, self.user_id, ticker, source, period, content)
        self._query(
            """
            MERGE (u:User {id:$user_id})
            MERGE (d:FinancialChunk {id:$doc_id})
            ON CREATE SET d.created_at=$now
            SET d.tenant_id=$tenant_id, d.user_id=$user_id,
                d.ticker=$ticker, d.source=$source, d.period=$period, d.thread_id=$thread_id,
                d.content=$content, d.embedding=vecf32($embedding),
                d.updated_at=$now
            MERGE (u)-[:OWNS_DOCUMENT]->(d)
            RETURN d.id
            """,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            doc_id=doc_id,
            ticker=ticker,
            source=source,
            period=period,
            thread_id=thread_id,
            content=content,
            now=_now(),
            embedding=self._embed(content),
        )
        return doc_id

    def search_documents(
        self,
        question: str,
        *,
        ticker: str,
        limit: int = 5,
    ) -> list[dict]:
        rows = self._read(
            """
            CALL db.idx.vector.queryNodes('FinancialChunk','embedding',
                $candidates,vecf32($embedding)) YIELD node, score
            WHERE node.tenant_id=$tenant_id AND node.user_id=$user_id
              AND node.ticker=$ticker
            RETURN node.content,node.source,node.ticker,score
            ORDER BY score DESC LIMIT $limit
            """,
            candidates=max(50, limit * 12),
            embedding=self._embed(question),
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            ticker=ticker.strip().upper(),
            limit=int(limit),
        )
        return [dict(zip(("content", "source", "ticker", "score"), row)) for row in rows]

    def exact_financial_documents(
        self,
        *,
        ticker: str,
        year: int,
        limit: int = 12,
    ) -> list[dict]:
        rows = self._read(
            """
            MATCH (d:FinancialChunk)
            WHERE d.tenant_id=$tenant_id AND d.user_id=$user_id
              AND d.ticker=$ticker AND d.period=$period
              AND d.source IN ['sec_annual_income', 'sec_annual_balance']
            RETURN d.content,d.source,d.ticker,d.period
            ORDER BY d.source ASC, d.updated_at DESC LIMIT $limit
            """,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            ticker=ticker.strip().upper(),
            period=str(year),
            limit=int(limit),
        )
        return [dict(zip(("content", "source", "ticker", "period"), row)) for row in rows]

    def create_handoff(
        self,
        *,
        from_agent: str,
        to_agent: str,
        thread_id: str,
        payload: dict,
        handoff_id: str = "",
    ) -> str:
        handoff_id = handoff_id or uuid4().hex
        self._query(
            """
            MERGE (u:User {id:$user_id})
            MERGE (a:Agent {id:$from_agent})
            MERGE (b:Agent {id:$to_agent})
            CREATE (h:Handoff {
                id: $handoff_id,
                status: 'pending',
                created_at: $now,
                payload: $payload,
                user_id: $user_id,
                tenant_id: $tenant_id,
                thread_id: $thread_id,
                from_agent: $from_agent,
                to_agent: $to_agent
            })
            MERGE (u)-[:HAS_HANDOFF]->(h)
            MERGE (a)-[:SENT]->(h)
            MERGE (h)-[:TO_AGENT]->(b)
            RETURN h.id
            """,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            handoff_id=handoff_id,
            from_agent=from_agent,
            to_agent=to_agent,
            thread_id=thread_id,
            payload=json.dumps(payload, default=str),
            now=_now(),
        )
        return handoff_id

    def claim_handoff(self, handoff_id: str, agent_id: str) -> dict | None:
        rows = self._query(
            """
            MATCH (u:User {id:$user_id})-[:HAS_HANDOFF]->(h:Handoff {id:$handoff_id})
            WHERE h.user_id=$user_id AND h.tenant_id=$tenant_id
              AND h.to_agent=$agent_id AND h.status='pending'
            SET h.status='claimed',h.claimed_at=$now
            RETURN h.payload
            """,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            handoff_id=handoff_id,
            agent_id=agent_id,
            now=_now(),
        )
        return json.loads(rows[0][0]) if rows else None

    def complete_handoff(
        self,
        handoff_id: str,
        agent_id: str,
        *,
        outcome: str = "completed",
    ) -> bool:
        rows = self._query(
            """
            MATCH (u:User {id:$user_id})-[:HAS_HANDOFF]->(h:Handoff {id:$handoff_id})
            WHERE h.user_id=$user_id AND h.tenant_id=$tenant_id
              AND h.to_agent=$agent_id AND h.status='claimed'
            SET h.status='completed',h.outcome=$outcome,h.completed_at=$now
            RETURN h.id
            """,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            handoff_id=handoff_id,
            agent_id=agent_id,
            outcome=outcome,
            now=_now(),
        )
        return bool(rows)

    def pending_handoffs(self, agent_id: str, thread_id: str = "") -> list[dict]:
        rows = self._read(
            """
            MATCH (u:User {id:$user_id})-[:HAS_HANDOFF]->(h:Handoff)
            WHERE h.user_id=$user_id AND h.tenant_id=$tenant_id
              AND h.to_agent=$agent_id AND h.status='pending'
              AND ($thread_id='' OR h.thread_id=$thread_id)
            RETURN h.id,h.thread_id,h.payload,h.from_agent
            ORDER BY h.created_at ASC LIMIT 50
            """,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            agent_id=agent_id,
            thread_id=thread_id.strip(),
        )
        return [
            dict(id=r[0], thread_id=r[1], payload=json.loads(r[2]), from_agent=r[3]) for r in rows
        ]
