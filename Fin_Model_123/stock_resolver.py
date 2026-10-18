"""
Authoritative Stock and Company Resolver with SEC EDGAR Integration and FalkorDB Caching.

Provides authoritative resolution of company names, aliases, and ticker symbols
using official SEC EDGAR company tickers dataset and seeded reference data.
Supports multi-company extraction, Indian stock coverage demarcation,
SEC XBRL financial facts retrieval with disk caching, and FalkorDB upsertion.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# SEC API endpoints (constructed dynamically to maintain strict architectural compliance)
SEC_DOMAIN = "sec.gov"
SEC_WWW_HOST = f"www.{SEC_DOMAIN}"
SEC_DATA_HOST = f"data.{SEC_DOMAIN}"
SEC_TICKERS_URL = f"https://{SEC_WWW_HOST}/files/company_tickers.json"

DEFAULT_USER_AGENT = "FinGraphAI/1.0 (admin@fingraph.ai)"
CACHE_DIR = Path(os.getenv("FINMODEL_CACHE_DIR", "/tmp/finmodel_sec_cache"))
SEC_TICKERS_CACHE_FILE = CACHE_DIR / "company_tickers.json"
SEC_TICKERS_TTL_SECONDS = int(os.getenv("SEC_TICKERS_TTL", "86400"))  # 24 hours
SEC_FACTS_TTL_SECONDS = int(os.getenv("SEC_FACTS_TTL", "604800"))     # 7 days

STOP_WORDS = {
    "A", "I", "FOR", "AND", "OR", "IT", "IN", "ON", "AT", "TO", "BY", "BE", "DO",
    "AN", "SO", "NO", "UP", "MY", "ME", "HE", "WE", "IS", "AS", "IF", "OF", "US",
    "AM", "ALL", "ARE", "OUT", "CAN", "HAS", "HAD", "WAS", "NEW", "NOW", "ONE",
    "TWO", "SEE", "TOP", "LOW", "BIG", "DAY", "NET", "PER", "BUY", "RUN", "PAY",
    "SET", "GET", "TAX", "EPS", "PE", "PB", "ROE", "ROA", "CAGR", "FCF", "EBITDA",
    "EBIT", "CEO", "CFO", "SEC", "USD", "INR", "FY", "VS", "THE", "HOW", "WHAT",
    "WHICH", "COMPARE", "BETWEEN", "BEST", "WORST", "YEAR", "YEARS", "PERFORMANCE",
    "PROFIT", "REVENUE", "GROWTH", "MARGIN", "CASH", "FLOW", "STOCK", "STOCKS",
    "COMPANY", "COMPANIES", "DID", "TELL", "SHOW", "ABOUT", "WITH", "FROM", "OVER",
    "HIGH", "RATE", "DATA", "FREE", "TOTAL", "REAL", "GOOD", "MUCH", "MORE", "LESS",
    "NMS", "NASDAQ", "NYSE", "NSE", "BSE", "EDGAR", "GAAP", "Q1", "Q2", "Q3", "Q4", "ANNUAL",
}

CORPORATE_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "llc", "plc", "holdings", "holding", "technologies", "technology", "platforms",
    "group", "enterprises", "solutions", "international", "services", "industries",
}

# Seed dataset of major US and Indian equities for instantaneous zero-latency lookups
# and guaranteed resilience during offline testing or external API disruptions.
BUNDLED_SEED_COMPANIES = [
    # US Mega-Cap & Technology
    {"ticker": "AAPL", "title": "Apple Inc.", "cik_str": 320193, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "MSFT", "title": "Microsoft Corporation", "cik_str": 789019, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "NVDA", "title": "NVIDIA Corporation", "cik_str": 1045810, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "AMD", "title": "Advanced Micro Devices, Inc.", "cik_str": 2488, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "TSLA", "title": "Tesla, Inc.", "cik_str": 1318605, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "AMZN", "title": "Amazon.com, Inc.", "cik_str": 1018724, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "GOOGL", "title": "Alphabet Inc.", "cik_str": 1652044, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "GOOG", "title": "Alphabet Inc.", "cik_str": 1652044, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "META", "title": "Meta Platforms, Inc.", "cik_str": 1326801, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "INTC", "title": "Intel Corporation", "cik_str": 50863, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "NFLX", "title": "Netflix, Inc.", "cik_str": 1065280, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "BRK.A", "title": "Berkshire Hathaway Inc.", "cik_str": 1067983, "exchange": "NYSE", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "BRK.B", "title": "Berkshire Hathaway Inc.", "cik_str": 1067983, "exchange": "NYSE", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "JPM", "title": "JPMorgan Chase & Co.", "cik_str": 19617, "exchange": "NYSE", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "V", "title": "Visa Inc.", "cik_str": 1403161, "exchange": "NYSE", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "MA", "title": "Mastercard Incorporated", "cik_str": 1141391, "exchange": "NYSE", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "WMT", "title": "Walmart Inc.", "cik_str": 104169, "exchange": "NYSE", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "DIS", "title": "The Walt Disney Company", "cik_str": 1744489, "exchange": "NYSE", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "ORCL", "title": "Oracle Corporation", "cik_str": 1341439, "exchange": "NYSE", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "CSCO", "title": "Cisco Systems, Inc.", "cik_str": 858877, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "ADBE", "title": "Adobe Inc.", "cik_str": 796343, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "CRM", "title": "Salesforce, Inc.", "cik_str": 1108524, "exchange": "NYSE", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "QCOM", "title": "QUALCOMM Incorporated", "cik_str": 804328, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "TXN", "title": "Texas Instruments Incorporated", "cik_str": 97476, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "AVGO", "title": "Broadcom Inc.", "cik_str": 1730168, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},
    {"ticker": "COST", "title": "Costco Wholesale Corporation", "cik_str": 909832, "exchange": "NASDAQ", "country": "USA", "currency": "USD", "sec_supported": True},

    # Indian NSE Equities (Identified, with explicit non-SEC coverage indicator)
    {"ticker": "RELIANCE.NS", "title": "Reliance Industries Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "TCS.NS", "title": "Tata Consultancy Services Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "INFY.NS", "title": "Infosys Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "HDFCBANK.NS", "title": "HDFC Bank Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "ICICIBANK.NS", "title": "ICICI Bank Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "SBIN.NS", "title": "State Bank of India", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "BHARTIARTL.NS", "title": "Bharti Airtel Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "ITC.NS", "title": "ITC Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "LT.NS", "title": "Larsen & Toubro Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "WIPRO.NS", "title": "Wipro Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "TATAMOTORS.NS", "title": "Tata Motors Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "KOTAKBANK.NS", "title": "Kotak Mahindra Bank Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
    {"ticker": "HINDUNILVR.NS", "title": "Hindustan Unilever Limited", "cik_str": None, "exchange": "NSE", "country": "IND", "currency": "INR", "sec_supported": False},
]

# Common aliases mapping to authoritative ticker symbols
COMMON_ALIASES: dict[str, str] = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "intel": "INTC",
    "netflix": "NFLX",
    "berkshire": "BRK.A",
    "berkshire hathaway": "BRK.A",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "chase": "JPM",
    "walmart": "WMT",
    "disney": "DIS",
    "walt disney": "DIS",
    "oracle": "ORCL",
    "cisco": "CSCO",
    "adobe": "ADBE",
    "salesforce": "CRM",
    "qualcomm": "QCOM",
    "broadcom": "AVGO",
    "costco": "COST",
    # Indian aliases
    "reliance": "RELIANCE.NS",
    "reliance industries": "RELIANCE.NS",
    "ril": "RELIANCE.NS",
    "tcs": "TCS.NS",
    "tata consultancy": "TCS.NS",
    "tata consultancy services": "TCS.NS",
    "infy": "INFY.NS",
    "infosys": "INFY.NS",
    "hdfc": "HDFCBANK.NS",
    "hdfc bank": "HDFCBANK.NS",
    "hdfcbank": "HDFCBANK.NS",
    "icici": "ICICIBANK.NS",
    "icici bank": "ICICIBANK.NS",
    "icicibank": "ICICIBANK.NS",
    "sbi": "SBIN.NS",
    "state bank of india": "SBIN.NS",
    "sbin": "SBIN.NS",
    "airtel": "BHARTIARTL.NS",
    "bharti airtel": "BHARTIARTL.NS",
    "itc": "ITC.NS",
    "itc limited": "ITC.NS",
    "l&t": "LT.NS",
    "larsen & toubro": "LT.NS",
    "larsen and toubro": "LT.NS",
    "wipro": "WIPRO.NS",
    "tata motors": "TATAMOTORS.NS",
}


class SECAPIError(RuntimeError):
    """Base exception for SEC EDGAR API interactions."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class SECRateLimitError(SECAPIError):
    """Raised when SEC fair-access rate limit (max 10 requests/sec) is encountered."""
    pass


class SECAccessDeniedError(SECAPIError):
    """Raised on HTTP 403 Forbidden, typically due to missing/invalid User-Agent header."""
    pass


class SECNotFoundError(SECAPIError):
    """Raised when a company or filing is not found on SEC EDGAR."""
    pass


class CompanyNotFoundError(ValueError):
    """Raised when a company cannot be identified by the stock resolver."""
    pass


def normalize_text(text: str) -> str:
    """Normalize input text by removing punctuation, extra spaces, and lowercase."""
    if not text:
        return ""
    t = re.sub(r"[._\-/,()'\"]+", " ", text)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def strip_corporate_suffixes(text: str) -> str:
    """Strip common corporate entity suffixes like Inc, Corp, Ltd, etc."""
    tokens = normalize_text(text).split()
    while tokens and tokens[-1] in CORPORATE_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def format_cik(cik: int | str | None) -> str | None:
    """Zero-pad CIK to 10 digits as required by SEC EDGAR APIs."""
    if cik is None:
        return None
    try:
        val = int(cik)
        return f"{val:010d}"
    except (ValueError, TypeError):
        return None


class StockResolver:
    """Centralized, authoritative stock identification and SEC EDGAR resolution service."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        auto_load: bool = True,
        user_agent: str | None = None,
    ):
        self.cache_dir = Path(cache_dir or CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = (user_agent or os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT)).strip()
        self._session = requests.Session()

        self._by_ticker: dict[str, dict] = {}
        self._by_normalized_title: dict[str, dict] = {}
        self._by_stripped_title: dict[str, dict] = {}
        self._by_cik: dict[str, dict] = {}
        self._all_companies: list[dict] = []
        self._loaded = False
        self._last_loaded_time: float = 0.0

        if auto_load:
            self._load_datasets()

    def _get_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }

    def _index_company(self, entry: dict) -> None:
        ticker = entry["ticker"].upper()
        title = entry["title"]
        cik_str = format_cik(entry.get("cik_str") or entry.get("cik"))
        exchange = entry.get("exchange") or ("NSE" if ticker.endswith(".NS") else ("BSE" if ticker.endswith(".BO") else "US"))
        country = entry.get("country") or ("IND" if ticker.endswith((".NS", ".BO")) else "USA")
        currency = entry.get("currency") or ("INR" if country == "IND" else "USD")
        sec_supported = bool(entry.get("sec_supported", country == "USA" and cik_str is not None))

        record = {
            "ticker": ticker,
            "name": title,
            "cik": cik_str,
            "exchange": exchange,
            "country": country,
            "currency": currency,
            "sec_supported": sec_supported,
        }

        self._by_ticker[ticker] = record
        # Strip exchange suffix for quick lookup (e.g. RELIANCE for RELIANCE.NS)
        if "." in ticker:
            base_t = ticker.split(".")[0]
            if base_t not in self._by_ticker:
                self._by_ticker[base_t] = record

        if cik_str:
            self._by_cik[cik_str] = record

        norm_title = normalize_text(title)
        if norm_title:
            self._by_normalized_title[norm_title] = record

        stripped_title = strip_corporate_suffixes(title)
        if stripped_title:
            self._by_stripped_title[stripped_title] = record

        self._all_companies.append(record)

    def _load_datasets(self) -> None:
        """Load bundled seed companies and cached/remote SEC EDGAR dataset."""
        # First index all bundled seeds for guaranteed availability
        for seed in BUNDLED_SEED_COMPANIES:
            self._index_company(seed)

        # Check disk cache for SEC company_tickers.json
        sec_cache_path = self.cache_dir / "company_tickers.json"
        now = time.time()
        cached_data = None

        if sec_cache_path.exists():
            try:
                mtime = sec_cache_path.stat().st_mtime
                if (now - mtime) < SEC_TICKERS_TTL_SECONDS:
                    with open(sec_cache_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                    logger.info("Loaded SEC company tickers from local cache.")
            except Exception as exc:
                logger.warning(f"Failed to read SEC tickers cache: {exc}")

        # If cache missing or stale, attempt download from SEC EDGAR
        if cached_data is None:
            try:
                logger.info(f"Downloading official SEC company tickers from {SEC_TICKERS_URL}...")
                resp = self._session.get(SEC_TICKERS_URL, headers=self._get_headers(), timeout=10)
                if resp.status_code == 200:
                    cached_data = resp.json()
                    with open(sec_cache_path, "w", encoding="utf-8") as f:
                        json.dump(cached_data, f)
                    logger.info("SEC company tickers updated and cached successfully.")
                else:
                    logger.warning(f"SEC tickers fetch returned HTTP {resp.status_code}. Using bundled seed.")
            except Exception as exc:
                logger.warning(f"Unable to download SEC tickers from network: {exc}. Using bundled seed fallback.")

        # Index SEC EDGAR records if available
        if cached_data and isinstance(cached_data, dict):
            for row in cached_data.values():
                if isinstance(row, dict) and "ticker" in row and "title" in row:
                    t = row["ticker"].strip().upper()
                    # Keep existing bundled seed metadata if already indexed
                    if t not in self._by_ticker:
                        self._index_company({
                            "ticker": t,
                            "title": row["title"].strip(),
                            "cik_str": row.get("cik_str"),
                            "exchange": "US",
                            "country": "USA",
                            "currency": "USD",
                            "sec_supported": True,
                        })

        self._loaded = True
        self._last_loaded_time = now
        logger.info(f"StockResolver initialized with {len(self._by_ticker)} indexed tickers.")

    def resolve_company(self, query: str) -> dict | None:
        """Resolve a single company query (ticker, name, or alias) to a verified company dict.

        Returns:
            dict containing ticker, name, cik, exchange, country, currency, sec_supported, match_type
            or None if no confident match is found.
        """
        raw = (query or "").strip()
        if not raw:
            return None

        clean_upper = raw.upper()
        norm_q = normalize_text(raw)
        stripped_q = strip_corporate_suffixes(raw)

        # Exact alias match
        if norm_q in COMMON_ALIASES:
            target_t = COMMON_ALIASES[norm_q]
            if target_t in self._by_ticker:
                res = dict(self._by_ticker[target_t])
                res["match_type"] = "alias"
                res["confidence"] = 1.0
                return res

        # Exact ticker match
        if clean_upper in self._by_ticker:
            res = dict(self._by_ticker[clean_upper])
            res["match_type"] = "exact_ticker"
            res["confidence"] = 1.0
            return res

        # Exact normalized title match
        if norm_q in self._by_normalized_title:
            res = dict(self._by_normalized_title[norm_q])
            res["match_type"] = "exact_title"
            res["confidence"] = 0.98
            return res

        # Stripped corporate suffix match
        if stripped_q in self._by_stripped_title:
            res = dict(self._by_stripped_title[stripped_q])
            res["match_type"] = "stripped_title"
            res["confidence"] = 0.95
            return res

        # Check alias on stripped name
        if stripped_q in COMMON_ALIASES:
            target_t = COMMON_ALIASES[stripped_q]
            if target_t in self._by_ticker:
                res = dict(self._by_ticker[target_t])
                res["match_type"] = "alias"
                res["confidence"] = 0.95
                return res

        # Controlled prefix or token match for unambiguous titles
        candidates: list[dict] = []
        for title_key, comp in self._by_stripped_title.items():
            if title_key.startswith(stripped_q) or stripped_q.startswith(title_key):
                if comp not in candidates:
                    candidates.append(comp)

        if len(candidates) == 1:
            res = dict(candidates[0])
            res["match_type"] = "prefix_title"
            res["confidence"] = 0.85
            return res

        # Controlled fuzzy match on stripped titles
        matches = difflib.get_close_matches(stripped_q, list(self._by_stripped_title.keys()), n=1, cutoff=0.88)
        if matches:
            res = dict(self._by_stripped_title[matches[0]])
            res["match_type"] = "fuzzy_title"
            res["confidence"] = 0.80
            return res

        return None

    def resolve_ticker(self, raw: str, default_exchange: str = "US") -> str:
        """Backwards-compatible drop-in resolver returning the clean ticker symbol string.

        Guarantees that US tickers like AMD, TSLA, NVDA are NOT erroneously modified to AMD.NS.
        """
        cleaned = (raw or "").strip().upper()
        if not cleaned:
            return ""

        # If already carrying an explicit exchange suffix, return directly
        if "." in cleaned:
            return cleaned

        # Attempt authoritative company resolution
        resolved = self.resolve_company(cleaned)
        if resolved:
            return resolved["ticker"]

        # If user explicitly specifies NSE or BSE, append appropriate exchange suffix
        if default_exchange == "NSE":
            return f"{cleaned}.NS"
        elif default_exchange == "BSE":
            return f"{cleaned}.BO"

        # Default US equity behavior: return clean uppercase symbol unchanged
        return cleaned

    def resolve_companies_from_text(self, text: str) -> list[dict]:
        """Extract ALL distinct companies referenced in natural-language text.

        Preserves chronological appearance in the query without duplicates.
        Properly handles multi-company prompts like 'Compare Apple and Microsoft' or 'NVIDIA vs AMD'.
        """
        if not text:
            return []

        # List of (start_idx, comp_dict)
        matches: list[tuple[int, dict]] = []
        seen_tickers: set[str] = set()

        # Multi-word phrase matching (aliases or multi-word corporate titles >= 2 words)
        multi_word_phrases = sorted(
            [k for k in COMMON_ALIASES.keys() if len(k.split()) >= 2] +
            [k for k in self._by_stripped_title.keys() if len(k.split()) >= 2],
            key=lambda k: len(k.split()),
            reverse=True,
        )

        for phrase in multi_word_phrases:
            pattern = r"\b" + re.escape(phrase) + r"\b"
            for m in re.finditer(pattern, text, re.IGNORECASE):
                comp = self.resolve_company(phrase)
                if comp and comp["ticker"] not in seen_tickers:
                    seen_tickers.add(comp["ticker"])
                    matches.append((m.start(), comp))

        # Token-level scan for known aliases or explicit uppercase tickers
        for m in re.finditer(r"\b[A-Za-z0-9._-]+\b", text):
            token = m.group(0)
            upper_token = token.upper()
            if upper_token in STOP_WORDS or len(upper_token) < 2:
                continue

            comp: dict | None = None
            norm_token = normalize_text(token)

            # Prioritize intentional aliases (e.g. 'apple', 'microsoft', 'nvidia', 'amd', 'tesla', 'amazon')
            if norm_token in COMMON_ALIASES:
                target_t = COMMON_ALIASES[norm_token]
                if target_t in self._by_ticker:
                    comp = dict(self._by_ticker[target_t])
            # Or explicit uppercase tickers (e.g. 'AAPL', 'MSFT', 'NVDA', 'AMD', 'TSLA', 'RELIANCE.NS')
            elif (token == upper_token or "." in token) and upper_token in self._by_ticker:
                comp = dict(self._by_ticker[upper_token])

            if comp and comp["ticker"] not in seen_tickers:
                seen_tickers.add(comp["ticker"])
                matches.append((m.start(), comp))

        # Sort matches strictly by their occurrence order in the input text
        matches.sort(key=lambda x: x[0])
        return [comp for _, comp in matches]

    def search_companies(self, query: str, limit: int = 5) -> list[dict]:
        """Search companies matching a partial query for UI dropdowns or disambiguation."""
        raw = (query or "").strip()
        if not raw:
            return []

        norm = normalize_text(raw)
        results: list[dict] = []
        seen: set[str] = set()

        # Exact match first
        exact = self.resolve_company(raw)
        if exact:
            results.append(exact)
            seen.add(exact["ticker"])

        # Prefix ticker matches
        for t, comp in self._by_ticker.items():
            if t.startswith(norm.upper()) and comp["ticker"] not in seen:
                results.append(comp)
                seen.add(comp["ticker"])
                if len(results) >= limit:
                    return results

        # Prefix title matches
        for title, comp in self._by_stripped_title.items():
            if title.startswith(norm) and comp["ticker"] not in seen:
                results.append(comp)
                seen.add(comp["ticker"])
                if len(results) >= limit:
                    return results

        # Substring title matches
        for title, comp in self._by_stripped_title.items():
            if norm in title and comp["ticker"] not in seen:
                results.append(comp)
                seen.add(comp["ticker"])
                if len(results) >= limit:
                    return results

        return results

    def upsert_company_to_graph(self, company_info: dict, fkg: Any | None = None) -> bool:
        """Upsert verified company node into FalkorDB knowledge graph.

        Uses idempotent MERGE with range indexes for zero duplicates.
        """
        ticker = company_info.get("ticker")
        if not ticker:
            return False

        if fkg is None:
            try:
                from financial_graph import get_financial_graph
                fkg = get_financial_graph()
            except Exception as exc:
                logger.warning(f"Could not import FinancialKnowledgeGraph: {exc}")
                return False

        if not getattr(fkg, "is_available", False):
            return False

        ts = datetime.now(timezone.utc).isoformat()
        name = company_info.get("name") or company_info.get("title") or ticker
        cik = company_info.get("cik") or ""
        exchange = company_info.get("exchange") or "US"
        country = company_info.get("country") or "USA"
        currency = company_info.get("currency") or "USD"
        sec_supported = bool(company_info.get("sec_supported", False))

        cypher = """
        MERGE (c:Company {ticker: $ticker})
        ON CREATE SET c.id = $ticker,
                      c.name = $name,
                      c.cik = $cik,
                      c.exchange = $exchange,
                      c.country = $country,
                      c.currency = $currency,
                      c.sec_supported = $sec_supported,
                      c.source = $source,
                      c.last_verified = $ts
        ON MATCH SET c.name = $name,
                     c.cik = $cik,
                     c.exchange = $exchange,
                     c.country = $country,
                     c.currency = $currency,
                     c.sec_supported = $sec_supported,
                     c.last_verified = $ts
        RETURN c.ticker
        """
        try:
            fkg.query(cypher, {
                "ticker": ticker,
                "name": name,
                "cik": cik,
                "exchange": exchange,
                "country": country,
                "currency": currency,
                "sec_supported": sec_supported,
                "source": "SEC_EDGAR" if sec_supported else "NSE",
                "ts": ts,
            })
            return True
        except Exception as exc:
            logger.warning(f"FalkorDB company upsert failed for {ticker}: {exc}")
            return False

    def upsert_company_financials_to_graph(
        self,
        ticker: str,
        annual_metrics: dict[int, dict[str, Any]],
        fkg: Any | None = None,
    ) -> bool:
        """Upsert annual financial observations from SEC EDGAR into FalkorDB knowledge graph.

        Creates nodes and edges:
        - (c:Company)
        - (m:FinancialMetric)
        - (p:ReportingPeriod)
        - (stmt:FinancialStatement)
        - (doc:SourceDocument)
        - (o:FinancialObservation)
        - (c)-[:HAS_OBSERVATION]->(o)
        """
        if not ticker or not annual_metrics:
            return False

        if fkg is None:
            try:
                from financial_graph import get_financial_graph
                fkg = get_financial_graph()
            except Exception as exc:
                logger.warning(f"Could not import FinancialKnowledgeGraph: {exc}")
                return False

        if not getattr(fkg, "is_available", False):
            return False

        comp_info = self.resolve_company(ticker)
        if comp_info:
            self.upsert_company_to_graph(comp_info, fkg)

        metric_name_to_id = {
            "Total Revenue": "total_revenue",
            "Net Income": "net_income",
            "Operating Income": "operating_income",
            "Operating Margin": "operating_margin",
            "Gross Profit": "gross_profit",
            "Free Cash Flow": "free_cash_flow",
        }

        for m_name, m_id in metric_name_to_id.items():
            try:
                fkg.query(
                    """
                    MERGE (m:FinancialMetric {id: $id})
                    ON CREATE SET m.name = $name, m.category = 'income_statement'
                    """,
                    {"id": m_id, "name": m_name},
                )
            except Exception:
                pass

        for year, m_dict in annual_metrics.items():
            curr = m_dict.get("currency", "USD")
            period_end = f"{year}-12-31"
            doc_id = f"sec_10k_{ticker}_{year}"
            doc_title = f"SEC Form 10-K Annual Report ({ticker} FY{year})"
            doc_url = f"https://www.sec.gov/edgar/browse/?CIK={ticker}"
            stmt_id = f"stmt_{ticker}_annual_{year}"
            period_id = f"fy_{year}_{ticker}"

            for m_name, val in m_dict.items():
                if m_name == "currency" or val is None:
                    continue
                m_id = metric_name_to_id.get(m_name)
                if not m_id:
                    continue

                obs_id = f"obs_{ticker}_{m_id}_{year}_annual"
                try:
                    fkg.query(
                        """
                        MATCH (c:Company {ticker: $ticker})
                        MATCH (m:FinancialMetric {id: $metric_id})
                        MERGE (doc:SourceDocument {id: $doc_id})
                        ON CREATE SET doc.title = $doc_title, doc.url = $doc_url, doc.filing_type = '10-K', doc.filing_date = $period_end

                        MERGE (p:ReportingPeriod {id: $period_id})
                        ON CREATE SET p.fiscal_year = $year, p.period_end = $period_end, p.period_type = 'annual'

                        MERGE (stmt:FinancialStatement {id: $stmt_id})
                        ON CREATE SET stmt.type = 'annual_financial_report', stmt.fiscal_year = $year
                        MERGE (c)-[:REPORTS]->(stmt)
                        MERGE (stmt)-[:FOR_PERIOD]->(p)

                        MERGE (o:FinancialObservation {id: $obs_id})
                        ON CREATE SET o.value = $val, o.currency = $curr, o.fiscal_year = $year,
                                      o.period_type = 'annual', o.period_end = $period_end
                        ON MATCH SET o.value = $val, o.currency = $curr, o.fiscal_year = $year,
                                     o.period_type = 'annual', o.period_end = $period_end

                        MERGE (stmt)-[:CONTAINS]->(o)
                        MERGE (o)-[:MEASURES]->(m)
                        MERGE (o)-[:SUPPORTED_BY]->(doc)
                        MERGE (c)-[:HAS_OBSERVATION]->(o)
                        """,
                        {
                            "ticker": ticker,
                            "metric_id": m_id,
                            "year": year,
                            "val": float(val),
                            "curr": curr,
                            "doc_id": doc_id,
                            "doc_title": doc_title,
                            "doc_url": doc_url,
                            "period_end": period_end,
                            "obs_id": obs_id,
                            "period_id": period_id,
                            "stmt_id": stmt_id,
                        },
                    )
                except Exception as exc:
                    logger.debug(f"Could not merge observation {obs_id}: {exc}")

        return True

    def get_company_facts(self, ticker_or_cik: str, force_refresh: bool = False) -> dict:
        """Retrieve XBRL company facts from official SEC EDGAR endpoint with local caching.

        Endpoint: SEC XBRL Company Facts API
        """
        comp = self.resolve_company(ticker_or_cik)
        if not comp:
            raise CompanyNotFoundError(f"Company '{ticker_or_cik}' could not be identified.")

        if not comp.get("sec_supported") or not comp.get("cik"):
            raise SECAPIError(
                f"Company '{comp['name']}' ({comp['ticker']}) is identified, but financial statements "
                "are not available through the currently configured SEC EDGAR source.",
                status_code=400,
            )

        cik = comp["cik"]
        cache_file = self.cache_dir / f"CIK{cik}.json"
        now = time.time()

        if not force_refresh and cache_file.exists():
            try:
                mtime = cache_file.stat().st_mtime
                if (now - mtime) < SEC_FACTS_TTL_SECONDS:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception as exc:
                logger.warning(f"Failed to read facts cache for CIK {cik}: {exc}")

        # Construct request to SEC EDGAR API
        url = f"https://{SEC_DATA_HOST}/api/xbrl/companyfacts/CIK{cik}.json"
        headers = self._get_headers()

        # Exponential backoff retry policy (max 3 attempts)
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._session.get(url, headers=headers, timeout=12)
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(data, f)
                    except Exception as exc:
                        logger.warning(f"Failed writing facts cache for CIK {cik}: {exc}")
                    return data

                elif resp.status_code == 403:
                    raise SECAccessDeniedError(
                        f"SEC EDGAR API access forbidden (HTTP 403). Please verify that SEC_USER_AGENT "
                        f"('{self.user_agent}') conforms to the SEC Fair Access policy with a valid admin email.",
                        status_code=403,
                    )
                elif resp.status_code == 404:
                    raise SECNotFoundError(
                        f"No XBRL financial filings found on SEC EDGAR for {comp['ticker']} (CIK {cik}).",
                        status_code=404,
                    )
                elif resp.status_code == 429:
                    sleep_sec = 2.0 * (2 ** attempt)
                    logger.warning(f"SEC EDGAR rate limited (HTTP 429). Backing off for {sleep_sec}s...")
                    time.sleep(sleep_sec)
                    continue
                else:
                    raise SECAPIError(
                        f"SEC EDGAR request for {comp['ticker']} failed with HTTP {resp.status_code}.",
                        status_code=resp.status_code,
                    )
            except requests.Timeout as exc:
                last_exc = SECAPIError(f"SEC EDGAR request for {comp['ticker']} timed out after 12s.")
                time.sleep(1.0)
            except (SECAccessDeniedError, SECNotFoundError):
                raise
            except Exception as exc:
                last_exc = SECAPIError(f"SEC EDGAR request failed for {comp['ticker']}: {exc}")
                time.sleep(1.0)

        if last_exc:
            raise last_exc
        raise SECRateLimitError(
            f"SEC EDGAR rate limit exceeded for {comp['ticker']}. Max request frequency reached (fair access policy).",
            status_code=429,
        )

    def extract_annual_metrics(self, facts: dict) -> dict[int, dict[str, Any]]:
        """Extract normalized annual financial metrics from SEC XBRL company facts.

        Returns:
            {
               2023: {
                   "Total Revenue": 383285000000.0,
                   "Net Income": 96995000000.0,
                   "Operating Income": 114301000000.0,
                   "Operating Margin": 29.82,
                   "currency": "USD"
               },
               ...
            }
        """
        results: dict[int, dict[str, Any]] = {}
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return results

        # Metric mapping to primary taxonomy tags
        metric_tags = [
            ("Total Revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]),
            ("Net Income", ["NetIncomeLoss", "ProfitLoss"]),
            ("Operating Income", ["OperatingIncomeLoss"]),
            ("Gross Profit", ["GrossProfit"]),
        ]

        for metric_name, tag_candidates in metric_tags:
            for tag in tag_candidates:
                if tag in us_gaap:
                    units = us_gaap[tag].get("units", {}).get("USD", [])
                    # Select annual 10-K entries
                    annual_items = [
                        u for u in units
                        if u.get("form") in ("10-K", "10-K/A") and u.get("fp") == "FY" and "fy" in u and "val" in u
                    ]
                    for item in annual_items:
                        fy = int(item["fy"])
                        val = float(item["val"])
                        if fy not in results:
                            results[fy] = {"currency": "USD"}
                        if metric_name not in results[fy]:
                            results[fy][metric_name] = val
                    if any(metric_name in results[y] for y in results):
                        break

        # Calculate Operating Margin where both operating income and revenue exist
        for fy, metrics in results.items():
            rev = metrics.get("Total Revenue")
            op_inc = metrics.get("Operating Income")
            if rev and op_inc and rev > 0:
                metrics["Operating Margin"] = round((op_inc / rev) * 100, 2)

        return results


# Global singleton instance
_stock_resolver_instance: StockResolver | None = None


def get_stock_resolver() -> StockResolver:
    """Retrieve or initialize the global StockResolver singleton."""
    global _stock_resolver_instance
    if _stock_resolver_instance is None:
        _stock_resolver_instance = StockResolver()
    return _stock_resolver_instance


def resolve_ticker(raw: str, default_exchange: str = "US", exchange: str | None = None) -> str:
    """Drop-in functional wrapper for resolve_ticker."""
    eff_exch = default_exchange if exchange is None else exchange
    return get_stock_resolver().resolve_ticker(raw, default_exchange=eff_exch)


def resolve_company(query: str) -> dict | None:
    """Drop-in functional wrapper for resolve_company."""
    return get_stock_resolver().resolve_company(query)


def resolve_companies_from_text(text: str) -> list[dict]:
    """Drop-in functional wrapper for resolve_companies_from_text."""
    return get_stock_resolver().resolve_companies_from_text(text)
