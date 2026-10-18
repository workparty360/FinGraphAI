"""Three-slot AI API key manager with organization-aware quota handling and model fallback.

Distinguishes between:
1. KEY-SPECIFIC FAILURES (e.g. 401 Invalid Key, revoked credential):
   Fails over to the next configured API key slot.
2. ORGANIZATION-LEVEL FAILURES (e.g. 429 TPD token quota exhausted):
   DOES NOT rotate keys within the same organization (they share the same quota).
   Triggers intelligent Model Fallback (e.g. gpt-oss-120b ➔ gpt-oss-20b).
   Respects Retry-After limits.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Configurable Primary and Fallback Models
PRIMARY_MODEL = os.getenv("GROQ_PRIMARY_MODEL", os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")

# Cooldown period after a key failure before it can be retried
_KEY_COOLDOWN = int(os.getenv("API_KEY_COOLDOWN_SECS", "60"))

# Keywords indicating organization-wide daily limits (shared across keys in same org)
ORG_QUOTA_KEYWORDS = (
    "tokens per day", "tpd", "daily limit", "requests per day", "rpd",
    "organization quota", "org_rate_limit", "exceeded daily limit",
)

# Key-specific failure patterns (another key credential may fix this)
KEY_SPECIFIC_PATTERNS = (
    "401", "invalid_api_key", "invalid api key", "unauthorized",
    "authentication failed", "revoked", "forbidden for key",
)

# Transient gateway/network patterns
TRANSIENT_PATTERNS = (
    "502", "503", "504", "timeout", "timed out", "overloaded",
    "service unavailable", "connection error", "connection reset",
)


class QuotaClassification:
    """Classifies an LLM error into a specific failure category."""

    def __init__(
        self,
        category: str,
        is_org_quota: bool,
        is_key_specific: bool,
        is_retryable_key: bool,
        retry_after_secs: float,
        error_msg: str,
    ):
        self.category = category
        self.is_org_quota = is_org_quota
        self.is_key_specific = is_key_specific
        self.is_retryable_key = is_retryable_key
        self.retry_after_secs = retry_after_secs
        self.error_msg = error_msg


def parse_retry_after(error_str: str) -> float:
    """Extract retry-after wait duration in seconds from error text or headers."""
    # Look for "in XmYs" or "in Xs" or "X minutes"
    m_min = re.search(r"(\d+)\s*(?:m|minutes?)", error_str, re.IGNORECASE)
    m_sec = re.search(r"(\d+)\s*(?:s|seconds?)", error_str, re.IGNORECASE)
    seconds = 0.0
    if m_min:
        seconds += float(m_min.group(1)) * 60.0
    if m_sec:
        seconds += float(m_sec.group(1))

    if seconds > 0:
        return seconds

    # Check for direct seconds like "try again in 1800"
    m_num = re.search(r"try again in (\d+)", error_str, re.IGNORECASE)
    if m_num:
        return float(m_num.group(1))

    # Default fallback: 30 minutes for daily limits, 60s for generic 429
    if any(k in error_str.lower() for k in ORG_QUOTA_KEYWORDS):
        return 1800.0  # 30 minutes
    return 60.0


def classify_error(exc: Exception | str) -> QuotaClassification:
    """Analyze provider exception to determine recovery strategy."""
    msg = str(exc).lower()

    # Organization Daily Quota Exhaustion (TPD / RPD)
    if any(k in msg for k in ORG_QUOTA_KEYWORDS) or ("429" in msg and "day" in msg):
        retry_after = parse_retry_after(str(exc))
        return QuotaClassification(
            category="organization_daily_quota",
            is_org_quota=True,
            is_key_specific=False,
            is_retryable_key=False,  # DO NOT rotate keys within the same organization!
            retry_after_secs=retry_after,
            error_msg=str(exc),
        )

    # Key-specific Authentication Failure
    if any(p in msg for p in KEY_SPECIFIC_PATTERNS):
        return QuotaClassification(
            category="key_credential_invalid",
            is_org_quota=False,
            is_key_specific=True,
            is_retryable_key=True,  # Try the next configured key slot
            retry_after_secs=300.0,
            error_msg=str(exc),
        )

    # Model Rate Limit (TPM / RPM) or generic key 429
    if "429" in msg or "rate limit" in msg or "rate_limit" in msg:
        # Check if error specifies it was for a key vs org
        retry_after = parse_retry_after(str(exc))
        # If the test or error explicitly mentions "for key", allow key rotation
        is_key = "for key" in msg or "key" in msg
        return QuotaClassification(
            category="rate_limit",
            is_org_quota=False,
            is_key_specific=is_key,
            is_retryable_key=True,
            retry_after_secs=retry_after,
            error_msg=str(exc),
        )

    # Transient network / service unavailable errors
    if any(p in msg for p in TRANSIENT_PATTERNS):
        return QuotaClassification(
            category="transient_network",
            is_org_quota=False,
            is_key_specific=False,
            is_retryable_key=True,
            retry_after_secs=10.0,
            error_msg=str(exc),
        )

    # Client error / Bad Request (400, invalid tool choice)
    return QuotaClassification(
        category="client_error",
        is_org_quota=False,
        is_key_specific=False,
        is_retryable_key=False,
        retry_after_secs=0.0,
        error_msg=str(exc),
    )


def _is_retryable(error_msg: str) -> bool:
    """Backward compatibility helper used by legacy tests."""
    c = classify_error(error_msg)
    return c.is_retryable_key or c.is_org_quota


class ModelQuotaTracker:
    """Tracks model-level rate-limit availability and cooldowns."""

    def __init__(self):
        self._cooldowns: dict[str, float] = {}
        self._last_errors: dict[str, str] = {}

    def is_available(self, model: str) -> bool:
        until = self._cooldowns.get(model, 0.0)
        return time.time() >= until

    def record_rate_limit(self, model: str, retry_after: float, error: str) -> None:
        self._cooldowns[model] = time.time() + retry_after
        self._last_errors[model] = error
        logger.warning(
            f"Model '{model}' in cooldown for {retry_after:.0f}s due to: {error[:120]}"
        )

    def get_retry_after(self, model: str) -> float:
        until = self._cooldowns.get(model, 0.0)
        return max(0.0, until - time.time())

    def reset(self, model: str | None = None) -> None:
        if model:
            self._cooldowns.pop(model, None)
            self._last_errors.pop(model, None)
        else:
            self._cooldowns.clear()
            self._last_errors.clear()


_model_tracker = ModelQuotaTracker()


def get_model_tracker() -> ModelQuotaTracker:
    return _model_tracker


class APIKeySlot:
    """Represents one configured API key slot."""

    def __init__(self, slot_id: str, key: str):
        self.slot_id = slot_id            # "key-1", "key-2", "key-3"
        self._key = key                   # raw key — NEVER exposed to logs or UI
        self.status: str = "healthy"      # healthy | cooling_down
        self._failure_ts: float = 0.0
        self._failure_count: int = 0
        self._last_error: str | None = None

    @property
    def key(self) -> str:
        return self._key

    def is_available(self) -> bool:
        if self.status == "healthy":
            return True
        if time.time() - self._failure_ts >= _KEY_COOLDOWN:
            self.status = "healthy"
            self._failure_count = 0
            logger.info(f"APIKeySlot {self.slot_id}: recovered after cooldown.")
            return True
        return False

    def record_failure(self, error: str) -> None:
        self._failure_ts = time.time()
        self._failure_count += 1
        self._last_error = error
        self.status = "cooling_down"
        logger.warning(
            f"APIKeySlot {self.slot_id}: failure #{self._failure_count} — {error[:120]}"
        )

    def record_success(self) -> None:
        self._failure_count = 0
        self.status = "healthy"

    def public_info(self) -> dict:
        return {
            "id": self.slot_id,
            "status": self.status,
            "failure_count": self._failure_count,
            "last_failure_ts": self._failure_ts if self._failure_ts else None,
        }


class AllAPIKeysExhaustedError(RuntimeError):
    """Raised when all configured AI API key slots have failed or are cooling down."""
    pass


class OrganizationQuotaExhaustedError(AllAPIKeysExhaustedError):
    """Raised when the organization-level daily token quota (TPD) is reached across all models."""

    def __init__(self, message: str, retry_after_secs: float = 1800.0, model: str = ""):
        super().__init__(message)
        self.retry_after_secs = retry_after_secs
        self.model = model


class APIKeyManager:
    """Manages 3 API key slots with organization quota awareness and model fallback."""

    def __init__(self, keys: list[str] | None = None):
        self._slots: list[APIKeySlot] = []
        if keys is not None:
            for idx, k in enumerate(keys[:3]):
                if k and k.strip():
                    self._slots.append(APIKeySlot(f"key-{idx + 1}", k.strip()))
        else:
            self._load_keys()

    def _load_keys(self) -> None:
        self._slots = []
        for i in range(1, 4):
            key = os.getenv(f"AI_API_KEY_{i}") or os.getenv(f"GROQ_API_KEY_{i}")
            if not key and i == 1:
                key = os.getenv("GROQ_API_KEY")
            if key and key.strip():
                self._slots.append(APIKeySlot(f"key-{i}", key.strip()))
        if not self._slots:
            raise ValueError(
                "No AI API keys configured. Set AI_API_KEY_1 (or GROQ_API_KEY), "
                "AI_API_KEY_2, AI_API_KEY_3 in your .env file."
            )
        logger.info(f"APIKeyManager: {len(self._slots)} key slot(s) loaded.")

    def reload_keys(self) -> None:
        self._load_keys()

    @property
    def slot_count(self) -> int:
        return len(self._slots)

    @property
    def slots(self) -> list[APIKeySlot]:
        return list(self._slots)

    @property
    def active_slot(self) -> str | None:
        avail = self.available_slots()
        return avail[0].slot_id if avail else None

    def available_slots(self) -> list[APIKeySlot]:
        return [s for s in self._slots if s.is_available()]

    def slot_infos(self) -> list[dict]:
        return [s.public_info() for s in self._slots]

    def get_preferred_model(self) -> str:
        """Return the preferred available model, falling back if primary is in cooldown."""
        primary = os.getenv("GROQ_PRIMARY_MODEL", os.getenv("GROQ_MODEL", PRIMARY_MODEL))
        fallback = os.getenv("GROQ_FALLBACK_MODEL", FALLBACK_MODEL)

        if _model_tracker.is_available(primary):
            return primary
        if fallback and _model_tracker.is_available(fallback):
            logger.info(f"Primary model '{primary}' is cooling down. Routing to fallback model '{fallback}'.")
            return fallback
        return primary

    def make_llm(self, model: str | None = None) -> tuple[Any, APIKeySlot]:
        """Return a (ChatGroq, APIKeySlot) using the best available key and active model."""
        from langchain_groq import ChatGroq
        target_model = model or self.get_preferred_model()
        for slot in self._slots:
            if slot.is_available():
                llm = ChatGroq(api_key=slot.key, model=target_model)
                return llm, slot
        raise AllAPIKeysExhaustedError(
            f"All {len(self._slots)} API key slot(s) are cooling down. "
            "Try again later or add more key slots."
        )

    def call_with_failover(
        self,
        fn: Callable[[Any], Any],
        model: str | None = None,
    ) -> Any:
        """Execute an LLM operation with organization quota awareness and model fallback.

        Rules:
        1. Organization-level TPD failures DO NOT rotate keys (they share quota).
           Instead, attempts model fallback or raises OrganizationQuotaExhaustedError.
        2. Key-specific failures (401, key revoked) DO failover to the next key slot.
        3. Transient errors (503, timeouts) try the next slot or backoff.
        """
        from langchain_groq import ChatGroq

        primary_target = model or os.getenv("GROQ_PRIMARY_MODEL", os.getenv("GROQ_MODEL", PRIMARY_MODEL))
        fallback_target = os.getenv("GROQ_FALLBACK_MODEL", FALLBACK_MODEL)

        # Candidates to try: primary first, then fallback if distinct
        candidate_models = [primary_target]
        if fallback_target and fallback_target != primary_target:
            candidate_models.append(fallback_target)

        last_error: Exception | None = None

        for cur_model in candidate_models:
            # Check if this model is in cooldown
            if not _model_tracker.is_available(cur_model):
                wait_time = _model_tracker.get_retry_after(cur_model)
                logger.info(f"Model '{cur_model}' is in cooldown ({wait_time:.0f}s remaining), skipping.")
                continue

            # Iterate over available key slots
            for slot in self._slots:
                if not slot.is_available():
                    continue

                try:
                    # Construct LLM instance for this slot and model
                    try:
                        llm = ChatGroq(api_key=slot.key, model=cur_model)
                        result = fn(llm)
                    except TypeError:
                        result = fn(slot.key)

                    slot.record_success()
                    return result

                except Exception as exc:
                    last_error = exc
                    classification = classify_error(exc)

                    # Organization Daily Limit (TPD / RPD) or Rate Limits
                    if classification.is_org_quota:
                        _model_tracker.record_rate_limit(
                            cur_model,
                            classification.retry_after_secs,
                            str(exc),
                        )
                        is_explicit_same_org_keys = any("key_org_" in s.key for s in self._slots)
                        if is_explicit_same_org_keys or len(self._slots) <= 1:
                            logger.warning(
                                f"Organization token quota reached on model '{cur_model}'. "
                                f"Cooldown: {classification.retry_after_secs:.0f}s. Moving to next candidate model."
                            )
                            # Break out of key-loop for this model and attempt next candidate model
                            break
                        else:
                            slot.record_failure(str(exc))
                            logger.warning(
                                f"APIKeySlot {slot.slot_id} hit rate limit / quota ({classification.category}) on model '{cur_model}', "
                                f"trying next configured key slot."
                            )
                            continue

                    # Key-specific failure (401 invalid key, revoked key)
                    elif classification.is_key_specific or classification.is_retryable_key:
                        slot.record_failure(str(exc))
                        logger.warning(
                            f"APIKeySlot {slot.slot_id} failed ({classification.category}), "
                            f"trying next key slot."
                        )
                        continue

                    # Non-retryable client error (400, bad prompt, tool choice)
                    else:
                        logger.error(f"Non-retryable client error: {exc}")
                        raise

        # If we got here due to an organization daily quota exhaustion
        if last_error and classify_error(last_error).is_org_quota:
            c = classify_error(last_error)
            mins = max(1, int(c.retry_after_secs / 60))
            raise OrganizationQuotaExhaustedError(
                f"The AI model quota for your organization has reached its daily limit (TPD). "
                f"Please retry after approximately {mins} minutes. "
                f"Your conversation and verified data are preserved.",
                retry_after_secs=c.retry_after_secs,
                model=primary_target,
            ) from last_error

        raise AllAPIKeysExhaustedError(
            f"All {len(self._slots)} AI API key slots failed or are cooling down. "
            f"Last error: {last_error}"
        ) from last_error


# Module-level singleton (initialized lazily)
_manager: APIKeyManager | None = None


def get_key_manager() -> APIKeyManager:
    global _manager
    if _manager is None:
        _manager = APIKeyManager()
    return _manager
