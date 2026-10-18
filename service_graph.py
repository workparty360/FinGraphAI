"""Track service dependencies and health in FalkorDB for diagnostic requests."""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
UNHEALTHY = "UNHEALTHY"
UNKNOWN = "UNKNOWN"

_health_cache: dict[str, dict] = {}
_CACHE_TTL = int(os.getenv("SERVICE_HEALTH_CACHE_TTL", "120"))

APP_SERVICES = [
    {
        "id": "frontend",
        "name": "Streamlit Frontend",
        "type": "frontend",
        "description": "Streamlit-based user interface for stock analysis dashboard.",
    },
    {
        "id": "ai-agent",
        "name": "AI Agent (Finance Agent Team)",
        "type": "ai_agent",
        "description": "Multi-step LangGraph financial analyst, researcher, and memory curator agents.",
    },
    {
        "id": "ai-provider",
        "name": "AI Provider (Groq)",
        "type": "ai_provider",
        "description": "Groq LLM API used for AI inference and tool calling.",
    },
    {
        "id": "api-key-manager",
        "name": "API Key Manager",
        "type": "infrastructure",
        "description": "Manages 3 AI API key slots with automatic failover logic.",
    },
    {
        "id": "stock-price-service",
        "name": "Stock Price Service",
        "type": "financial_data",
        "description": "Fetches real-time and historical stock prices via Yahoo Finance (yfinance).",
    },
    {
        "id": "financial-statements-service",
        "name": "Financial Statements Service",
        "type": "financial_data",
        "description": "Fetches income statements, balance sheets, and cash flow statements via yfinance.",
    },
    {
        "id": "yahoo-finance",
        "name": "Yahoo Finance (yfinance)",
        "type": "external_provider",
        "description": "External data provider for stock prices and financial statements.",
    },
    {
        "id": "knowledge-store",
        "name": "Financial Knowledge Store",
        "type": "database",
        "description": "Shared FalkorDB vector store for financial chunks and memories.",
    },
    {
        "id": "conversation-store",
        "name": "Conversation Store",
        "type": "database",
        "description": "FalkorDB-backed persistent conversation threads, turns, and handoffs.",
    },
    {
        "id": "falkordb",
        "name": "FalkorDB",
        "type": "graph_database",
        "description": "Graph database for service dependencies, health data, knowledge chunks, and conversations.",
    },
    {
        "id": "embedding-service",
        "name": "Embedding Service",
        "type": "ml_service",
        "description": "HuggingFace sentence-transformers for vectorizing financial text.",
    },
]

DEPENDENCY_EDGES = [
    {"from": "frontend", "to": "ai-agent", "type": "primary", "critical": True},
    {"from": "frontend", "to": "stock-price-service", "type": "primary", "critical": True},
    {"from": "frontend", "to": "financial-statements-service", "type": "primary", "critical": True},
    {"from": "frontend", "to": "conversation-store", "type": "primary", "critical": True},
    {"from": "ai-agent", "to": "ai-provider", "type": "primary", "critical": True},
    {"from": "ai-agent", "to": "api-key-manager", "type": "primary", "critical": True},
    {"from": "ai-agent", "to": "knowledge-store", "type": "primary", "critical": False},
    {"from": "ai-agent", "to": "conversation-store", "type": "primary", "critical": True},
    {"from": "api-key-manager", "to": "ai-provider", "type": "primary", "critical": True},
    {"from": "stock-price-service", "to": "yahoo-finance", "type": "primary", "critical": True},
    {
        "from": "financial-statements-service",
        "to": "yahoo-finance",
        "type": "primary",
        "critical": True,
    },
    {"from": "knowledge-store", "to": "falkordb", "type": "primary", "critical": True},
    {"from": "conversation-store", "to": "falkordb", "type": "primary", "critical": True},
    {"from": "ai-agent", "to": "embedding-service", "type": "support", "critical": False},
    {"from": "knowledge-store", "to": "embedding-service", "type": "support", "critical": False},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _probe_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


class ServiceGraph:
    """Manages the service dependency graph in FalkorDB.

    Falls back gracefully when FalkorDB is unavailable.
    """

    def __init__(self, graph: Any | None = None):
        self._graph = graph
        self._available = graph is not None
        if graph is not None:
            self._bootstrap()

    def _q(self, cypher: str, **params) -> list:
        if not self._available or self._graph is None:
            return []
        try:
            return self._graph.query(cypher, params=params).result_set
        except Exception as exc:
            logger.warning(f"ServiceGraph write query failed: {exc}")
            return []

    def _r(self, cypher: str, **params) -> list:
        if not self._available or self._graph is None:
            return []
        try:
            return self._graph.ro_query(cypher, params=params).result_set
        except Exception as exc:
            logger.warning(f"ServiceGraph read query failed: {exc}")
            return []

    def _bootstrap(self) -> None:
        try:
            self._graph.create_node_range_index("Service", "id")
        except Exception:
            pass

        for service in APP_SERVICES:
            self._q(
                """
                MERGE (s:Service {id: $id})
                ON CREATE SET s.name = $name, s.type = $type, s.description = $desc,
                    s.status = $status, s.last_checked = $ts, s.last_failure = null, s.error = null
                SET s.name = $name, s.type = $type, s.description = $desc
                RETURN s.id
                """,
                id=service["id"],
                name=service["name"],
                type=service["type"],
                desc=service["description"],
                status=UNKNOWN,
                ts=_now_iso(),
            )

        for edge in DEPENDENCY_EDGES:
            self._q(
                """
                MATCH (a:Service {id: $from_id})
                MATCH (b:Service {id: $to_id})
                MERGE (a)-[r:DEPENDS_ON {type: $type}]->(b)
                SET r.critical = $critical
                """,
                from_id=edge["from"],
                to_id=edge["to"],
                type=edge["type"],
                critical=edge["critical"],
            )

    def _update_status(self, service_id: str, status: str, error: str | None = None) -> None:
        now = _now_iso()
        _health_cache[service_id] = {"status": status, "ts": time.time(), "error": error}
        self._q(
            """
            MATCH (s:Service {id: $id})
            SET s.status = $status, s.last_checked = $ts, s.error = $error,
                s.last_failure = CASE WHEN $status <> 'HEALTHY' THEN $ts ELSE s.last_failure END
            """,
            id=service_id,
            status=status,
            ts=now,
            error=error or "",
        )

    def run_health_checks(self) -> dict[str, str]:
        results: dict[str, str] = {}
        now = time.time()
        for service in APP_SERVICES:
            sid = service["id"]
            cached = _health_cache.get(sid)
            if cached and (now - cached["ts"]) < _CACHE_TTL:
                results[sid] = cached["status"]
                continue
            status, error = self._probe_service(sid)
            results[sid] = status
            self._update_status(sid, status, error)
        return results

    def _probe_service(self, service_id: str) -> tuple[str, str | None]:
        try:
            if service_id == "falkordb":
                host = os.getenv("FALKORDB_HOST", "localhost")
                port = int(os.getenv("FALKORDB_PORT", "6379"))
                ok = _probe_tcp(host, port, timeout=2.0)
                return (
                    HEALTHY if ok else UNHEALTHY,
                    None if ok else f"TCP connection to {host}:{port} refused",
                )

            elif service_id == "yahoo-finance":
                ok = _probe_tcp("finance.yahoo.com", 443, timeout=5.0)
                return (
                    HEALTHY if ok else UNHEALTHY,
                    None if ok else "Cannot reach finance.yahoo.com:443",
                )

            elif service_id == "ai-provider":
                ok = _probe_tcp("api.groq.com", 443, timeout=5.0)
                return (
                    HEALTHY if ok else UNHEALTHY,
                    None if ok else "Cannot reach api.groq.com:443",
                )

            elif service_id in ("knowledge-store", "conversation-store"):
                falkor_status = _health_cache.get("falkordb", {}).get("status", UNKNOWN)
                if falkor_status == HEALTHY:
                    return HEALTHY, None
                if falkor_status == UNHEALTHY:
                    return UNHEALTHY, "FalkorDB unavailable (backing store)"
                return UNKNOWN, None

            elif service_id in ("api-key-manager", "ai-agent"):
                api_status = _health_cache.get("ai-provider", {}).get("status", UNKNOWN)
                key_count = sum(
                    1
                    for i in range(1, 4)
                    if os.getenv(f"AI_API_KEY_{i}") or (i == 1 and os.getenv("GROQ_API_KEY"))
                )
                if key_count == 0:
                    return UNHEALTHY, "No AI API keys configured"
                if api_status == UNHEALTHY:
                    return DEGRADED, "AI provider unreachable"
                return HEALTHY, None

            elif service_id in ("frontend", "stock-price-service", "financial-statements-service"):
                return UNKNOWN, None

            elif service_id == "embedding-service":
                return HEALTHY, None

        except Exception as exc:
            return UNKNOWN, str(exc)

        return UNKNOWN, None

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the health check cache."""
        _health_cache.clear()

    def get_service_status(self, service_id: str, use_cache: bool = True) -> dict:
        if use_cache:
            cached = _health_cache.get(service_id)
            if cached and (time.time() - cached["ts"]) < _CACHE_TTL:
                return {
                    "id": service_id,
                    "status": cached["status"],
                    "error": cached.get("error"),
                    "source": "cache",
                }
        rows = self._r(
            "MATCH (s:Service {id: $id}) RETURN s.name, s.status, s.last_checked, s.error",
            id=service_id,
        )
        if not rows:
            return {"id": service_id, "status": UNKNOWN, "error": None, "source": "not_found"}
        name, status, timestamp, error = rows[0]
        return {
            "id": service_id,
            "name": name,
            "status": status or UNKNOWN,
            "last_checked": timestamp,
            "error": error or None,
            "source": "graph",
        }

    def get_service_dependencies(self, service_id: str) -> list[dict]:
        rows = self._r(
            """
            MATCH (s:Service {id: $id})-[r:DEPENDS_ON]->(d:Service)
            RETURN d.id, d.name, d.status, r.critical
            """,
            id=service_id,
        )
        return [
            {"id": r[0], "name": r[1], "status": r[2] or UNKNOWN, "critical": r[3]} for r in rows
        ]

    def get_downstream_dependents(self, service_id: str) -> list[dict]:
        rows = self._r(
            """
            MATCH (dep:Service)-[:DEPENDS_ON*1..5]->(s:Service {id: $id})
            RETURN DISTINCT dep.id, dep.name, dep.status
            """,
            id=service_id,
        )
        return [{"id": r[0], "name": r[1], "status": r[2] or UNKNOWN} for r in rows]

    def get_dependency_path(self, source_id: str, target_id: str) -> list[str]:
        rows = self._r(
            """
            MATCH p = (s:Service {id: $src})-[:DEPENDS_ON*1..6]->(t:Service {id: $tgt})
            RETURN [node IN nodes(p) | node.id] LIMIT 1
            """,
            src=source_id,
            tgt=target_id,
        )
        return rows[0][0] if rows else []

    def get_recent_service_errors(self, service_id: str) -> list[dict]:
        rows = self._r(
            """
            MATCH (s:Service {id: $id})-[:DEPENDS_ON*0..5]->(d:Service)
            WHERE d.status IN ['UNHEALTHY', 'DEGRADED'] AND d.error <> '' AND d.error IS NOT NULL
            RETURN d.id, d.name, d.status, d.error, d.last_failure
            """,
            id=service_id,
        )
        return [
            {"id": r[0], "name": r[1], "status": r[2], "error": r[3], "last_failure": r[4]}
            for r in rows
        ]

    def investigate_failure(self, service_id: str) -> dict:
        service_info = self.get_service_status(service_id)
        dependencies = self.get_service_dependencies(service_id)
        errors = self.get_recent_service_errors(service_id)
        affected = self.get_downstream_dependents(service_id)

        dep_chain: list[dict] = []
        for dep in dependencies:
            path = self.get_dependency_path(service_id, dep["id"])
            dep_chain.append({**dep, "path": path})

        critical_failures = [
            d for d in dependencies if d.get("critical") and d["status"] in (UNHEALTHY, DEGRADED)
        ]

        if not self._available:
            confidence = "unavailable"
            probable_cause = "FalkorDB diagnostics are unavailable. Cannot determine root cause."
        elif service_info["status"] == HEALTHY:
            confidence = "confirmed"
            probable_cause = f"Service '{service_id}' is currently HEALTHY. No failure detected."
        elif critical_failures:
            confidence = "likely"
            failed_names = ", ".join(f["name"] for f in critical_failures)
            probable_cause = f"Critical dependency failure detected: {failed_names}"
        elif errors:
            confidence = "possible"
            probable_cause = f"Dependency errors found: {errors[0]['error']}"
        else:
            confidence = "unknown"
            probable_cause = "Unable to determine root cause from available health data."

        return {
            "service_id": service_id,
            "service_status": service_info,
            "direct_dependencies": dep_chain,
            "dependency_errors": errors,
            "affected_systems": affected,
            "probable_cause": probable_cause,
            "confidence": confidence,
            "diagnostics_available": self._available,
        }

    def get_all_statuses(self) -> list[dict]:
        rows = self._r(
            "MATCH (s:Service) RETURN s.id, s.name, s.type, s.status, s.last_checked, s.error ORDER BY s.id"
        )
        return [
            {
                "id": r[0],
                "name": r[1],
                "type": r[2],
                "status": r[3] or UNKNOWN,
                "last_checked": r[4],
                "error": r[5] or None,
            }
            for r in rows
        ]


_service_graph: ServiceGraph | None = None


def get_service_graph(graph: Any | None = None) -> ServiceGraph:
    global _service_graph
    if _service_graph is None:
        _service_graph = ServiceGraph(graph=graph)
    return _service_graph


def format_investigation_report(result: dict) -> str:
    lines = []
    service = result["service_status"]
    lines.append(
        f"**Service:** {service.get('name', result['service_id'])} (`{result['service_id']}`"
    )
    lines.append(f"**Status:** {service['status']}")
    if service.get("error"):
        lines.append(f"**Observed Error:** {service['error']}")

    if result["direct_dependencies"]:
        lines.append("\n**Dependency Path:**")
        for dep in result["direct_dependencies"]:
            icon = {"HEALTHY": "✅", "DEGRADED": "⚠️", "UNHEALTHY": "❌"}.get(dep["status"], "❓")
            lines.append(f"  {icon} {dep['name']} (`{dep['id']}`) — {dep['status']}")
            if dep.get("error"):
                lines.append(f"      Error: {dep['error']}")

    if result["dependency_errors"]:
        lines.append("\n**Failed Dependencies:**")
        for err in result["dependency_errors"]:
            lines.append(f"  - `{err['id']}`: {err['status']} — {err['error']}")

    if result["affected_systems"]:
        lines.append("\n**Potentially Affected Systems:**")
        for affected_service in result["affected_systems"]:
            icon = {"HEALTHY": "✅", "DEGRADED": "⚠️", "UNHEALTHY": "❌"}.get(
                affected_service["status"], "❓"
            )
            lines.append(f"  {icon} {affected_service['name']} (`{affected_service['id']}`")

    lines.append(f"\n**Probable Cause:** {result['probable_cause']}")
    lines.append(f"**Confidence:** {result['confidence'].upper()}")
    lines.append(
        "\n*Note: Only verified health data is reported. Inferred impacts are potential, not confirmed.*"
    )
    return "\n".join(lines)
