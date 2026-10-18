"""Trace application stages and classify errors without recording sensitive data."""

from __future__ import annotations

import logging
import os
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("execution_tracer")

# Error categories
GROQ_AUTH_ERROR = "GROQ_AUTH_ERROR"
GROQ_RATE_LIMIT = "GROQ_RATE_LIMIT"
GROQ_MODEL_ERROR = "GROQ_MODEL_ERROR"
GROQ_NETWORK_ERROR = "GROQ_NETWORK_ERROR"
GROQ_TOOL_ERROR = "GROQ_TOOL_ERROR"
FALKORDB_CONNECTION_ERROR = "FALKORDB_CONNECTION_ERROR"
FALKORDB_QUERY_ERROR = "FALKORDB_QUERY_ERROR"
SEC_API_ERROR = "SEC_API_ERROR"
FINANCIAL_DATA_MISSING = "FINANCIAL_DATA_MISSING"
QUERY_PLANNING_ERROR = "QUERY_PLANNING_ERROR"
AGENT_EXECUTION_ERROR = "AGENT_EXECUTION_ERROR"
RESPONSE_RENDERING_ERROR = "RESPONSE_RENDERING_ERROR"

# Request stages
STAGE_REQUEST_RECEIVED = "REQUEST_RECEIVED"
STAGE_QUERY_PLANNED = "QUERY_PLANNED"
STAGE_COMPANY_RESOLVED = "COMPANY_RESOLVED"
STAGE_RETRIEVAL_STARTED = "RETRIEVAL_STARTED"
STAGE_RETRIEVAL_COMPLETED = "RETRIEVAL_COMPLETED"
STAGE_PROMPT_CONSTRUCTED = "PROMPT_CONSTRUCTED"
STAGE_GROQ_REQUEST_STARTED = "GROQ_REQUEST_STARTED"
STAGE_GROQ_RESPONSE_RECEIVED = "GROQ_RESPONSE_RECEIVED"
STAGE_ANALYST_COMPLETED = "ANALYST_COMPLETED"
STAGE_MEMORY_PERSISTED = "MEMORY_PERSISTED"
STAGE_RESPONSE_RENDERED = "RESPONSE_RENDERED"

# In-memory circular buffer for recent trace records
_TRACE_BUFFER: deque[dict[str, Any]] = deque(maxlen=500)


def generate_request_id() -> str:
    """Generate a unique tracking identifier for a user request."""
    return f"req_{uuid.uuid4().hex[:12]}"


def is_tracing_enabled() -> bool:
    """Check if execution tracing is enabled via environment variable."""
    return os.getenv("ENABLE_EXECUTION_TRACING", "true").lower() in ("true", "1", "yes")


def record_trace(
    request_id: str,
    stage: str,
    agent_name: str,
    status: str = "SUCCESS",
    duration_secs: float | None = None,
    error_category: str | None = None,
    record_count: int = 0,
    context_items: int = 0,
    approx_prompt_tokens: int = 0,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a structured execution trace event without logging sensitive data.

    Preserves privacy: never logs full private conversations, credentials, or secrets.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    record = {
        "timestamp": timestamp,
        "request_id": request_id,
        "stage": stage,
        "agent_name": agent_name,
        "status": status,
        "duration_secs": round(duration_secs, 4) if duration_secs is not None else None,
        "error_category": error_category,
        "record_count": record_count,
        "context_items": context_items,
        "approx_prompt_tokens": approx_prompt_tokens,
    }
    if details:
        # Include safe summary metadata only
        safe_details = {
            k: v
            for k, v in details.items()
            if not any(s in k.lower() for s in ("key", "secret", "token", "password"))
        }
        record["details"] = safe_details

    _TRACE_BUFFER.append(record)

    if is_tracing_enabled():
        logger.info(
            f"[TRACE] id={request_id} stage={stage} agent={agent_name} status={status} "
            f"duration={record['duration_secs']}s records={record_count} tokens={approx_prompt_tokens}"
            + (f" error={error_category}" if error_category else "")
        )

    return record


def get_traces_for_request(request_id: str) -> list[dict[str, Any]]:
    """Return all execution trace records matching a specific request_id."""
    return [r for r in _TRACE_BUFFER if r.get("request_id") == request_id]


def get_all_traces() -> list[dict[str, Any]]:
    """Return a copy of all trace records in the buffer."""
    return list(_TRACE_BUFFER)


def clear_traces() -> None:
    """Clear all stored trace records."""
    _TRACE_BUFFER.clear()


def classify_app_error(exc: Exception | str) -> str:
    """Map any exception to one of the explicit error categories (Section 15)."""
    message = str(exc).lower()

    if any(k in message for k in ("401", "unauthorized", "invalid_api_key", "invalid api key")):
        return GROQ_AUTH_ERROR
    if any(k in message for k in ("429", "rate limit", "tpd", "rpd", "tokens per day")):
        return GROQ_RATE_LIMIT
    if any(
        k in message for k in ("model_not_found", "model not found", "unsupported model", "404")
    ):
        return GROQ_MODEL_ERROR
    if any(k in message for k in ("tool choice", "tool_call", "tool call", "invalid tool")):
        return GROQ_TOOL_ERROR
    if "falkor" in message and any(
        k in message for k in ("connect", "refused", "offline", "socket", "port 6379")
    ):
        return FALKORDB_CONNECTION_ERROR
    if "falkor" in message or "cypher" in message or "syntax error" in message:
        return FALKORDB_QUERY_ERROR
    if "sec" in message or "edgar" in message:
        return SEC_API_ERROR
    if any(
        k in message
        for k in ("timeout", "connection refused", "network unreachable", "dns", "connection error")
    ):
        return GROQ_NETWORK_ERROR
    if "missing" in message or "not found" in message or "no data" in message:
        return FINANCIAL_DATA_MISSING
    if "plan" in message or "planner" in message:
        return QUERY_PLANNING_ERROR
    if "render" in message or "streamlit" in message:
        return RESPONSE_RENDERING_ERROR

    return AGENT_EXECUTION_ERROR
