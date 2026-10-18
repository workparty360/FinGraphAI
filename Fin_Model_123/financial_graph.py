"""
Financial Knowledge Graph Engine using FalkorDB.

Manages the core financial knowledge graph modeling companies, sectors,
industries, financial statements, reporting periods, metrics, observations,
and source document evidence.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_GRAPH_NAME = "financial_knowledge"


class FalkorDBUnavailableError(RuntimeError):
    """Raised when FalkorDB is unreachable and graph intelligence cannot proceed."""
    pass


class FinancialKnowledgeGraph:
    """Core FalkorDB interface for the Financial Knowledge Graph."""

    def __init__(self, host: str | None = None, port: int | None = None, graph_name: str | None = None, graph: Any | None = None):
        self.host = host or os.getenv("FALKORDB_HOST", "localhost")
        self.port = int(port or os.getenv("FALKORDB_PORT", "6379"))
        self.graph_name = graph_name or os.getenv("FALKORDB_FINANCIAL_GRAPH", DEFAULT_GRAPH_NAME)
        self._graph = graph
        self._db: FalkorDB | None = None

        if self._graph is None:
            self._connect()

    def _connect(self) -> None:
        try:
            falkor_ssl = os.getenv("FALKORDB_SSL", "false").lower() in ("true", "1", "yes")
            falkor_user = os.getenv("FALKORDB_USERNAME") or None
            falkor_pass = os.getenv("FALKORDB_PASSWORD") or None
            self._db = FalkorDB(
                host=self.host,
                port=self.port,
                username=falkor_user,
                password=falkor_pass,
                ssl=falkor_ssl,
            )
            self._graph = self._db.select_graph(self.graph_name)
        except Exception as exc:
            logger.warning(f"Failed to connect to FalkorDB at {self.host}:{self.port}: {exc}")
            self._graph = None

    @property
    def is_available(self) -> bool:
        if self._graph is None:
            self._connect()
        if self._graph is None:
            return False
        try:
            if self._db and hasattr(self._db, "connection"):
                return bool(self._db.connection.ping())
            self._graph.query("RETURN 1")
            return True
        except Exception:
            return False

    def _ensure_connected(self) -> Any:
        if self._graph is None:
            self._connect()
        if self._graph is None:
            raise FalkorDBUnavailableError(
                f"FalkorDB is unavailable at {self.host}:{self.port}. "
                "Graph-powered financial intelligence requires an active FalkorDB instance."
            )
        return self._graph

    def query(self, cypher: str, params: dict | None = None) -> list:
        g = self._ensure_connected()
        try:
            res = g.query(cypher, params=params or {})
            return res.result_set
        except Exception as exc:
            if "Connection" in str(exc) or "refused" in str(exc).lower():
                raise FalkorDBUnavailableError(f"FalkorDB query failed due to connection error: {exc}") from exc
            logger.error(f"Cypher write query error: {exc}\nCypher: {cypher}")
            raise

    def ro_query(self, cypher: str, params: dict | None = None) -> list:
        g = self._ensure_connected()
        try:
            res = g.ro_query(cypher, params=params or {})
            return res.result_set
        except Exception as exc:
            err_str = str(exc)
            if "empty key" in err_str.lower():
                return []
            if "Connection" in err_str or "refused" in err_str.lower():
                raise FalkorDBUnavailableError(f"FalkorDB read query failed due to connection error: {exc}") from exc
            logger.error(f"Cypher read query error: {exc}\nCypher: {cypher}")
            raise

    def init_schema(self) -> None:
        """Create range indexes on primary lookup properties."""
        indexes = [
            ("Company", "ticker"),
            ("Company", "id"),
            ("Sector", "id"),
            ("Industry", "id"),
            ("FinancialMetric", "id"),
            ("FinancialMetric", "name"),
            ("FinancialObservation", "id"),
            ("ReportingPeriod", "id"),
            ("FinancialStatement", "id"),
            ("SourceDocument", "id"),
        ]
        g = self._ensure_connected()
        for label, prop in indexes:
            try:
                g.create_node_range_index(label, prop)
            except Exception:
                pass

    # =========================================================================
    # CORE GRAPH RETRIEVAL METHODS
    # =========================================================================

    def find_company(self, ticker: str) -> dict | None:
        """Find company node and its sector/industry relationships."""
        sym = ticker.strip().upper()
        cypher = """
        MATCH (c:Company)
        WHERE c.ticker = $ticker OR c.id = $ticker
        OPTIONAL MATCH (c)-[:BELONGS_TO]->(s:Sector)
        OPTIONAL MATCH (c)-[:OPERATES_IN]->(i:Industry)
        RETURN c.id, c.name, c.ticker, c.exchange, c.country, s.name, i.name
        LIMIT 1
        """
        rows = self.ro_query(cypher, {"ticker": sym})
        if not rows:
            return None
        r = rows[0]
        return {
            "id": r[0],
            "name": r[1],
            "ticker": r[2],
            "exchange": r[3],
            "country": r[4],
            "sector": r[5] or "N/A",
            "industry": r[6] or "N/A",
        }

    def get_company_financials(
        self,
        ticker: str,
        metric_name: str,
        period_type: str = "annual",
        fiscal_year: int | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Retrieve financial observations for a given company and metric ordered by fiscal year."""
        sym = ticker.strip().upper()
        cypher = """
        MATCH (c:Company {ticker: $ticker})-[:HAS_OBSERVATION]->(o:FinancialObservation)-[:MEASURES]->(m:FinancialMetric)
        WHERE (toLower(m.name) = toLower($metric) OR toLower(m.id) = toLower($metric))
          AND ($period_type = 'all' OR o.period_type = $period_type)
          AND ($fiscal_year IS NULL OR o.fiscal_year = $fiscal_year)
        OPTIONAL MATCH (o)-[:SUPPORTED_BY]->(doc:SourceDocument)
        RETURN c.ticker, c.name, m.name, o.id, o.value, o.currency, o.fiscal_year, o.period_type,
               o.period_end, doc.id, doc.title, doc.source_url
        ORDER BY o.fiscal_year ASC
        LIMIT $limit
        """
        rows = self.ro_query(cypher, {
            "ticker": sym,
            "metric": metric_name,
            "period_type": period_type,
            "fiscal_year": fiscal_year,
            "limit": limit,
        })
        results = []
        for r in rows:
            results.append({
                "ticker": r[0],
                "company_name": r[1],
                "metric": r[2],
                "observation_id": r[3],
                "value": float(r[4]) if r[4] is not None else None,
                "currency": r[5],
                "fiscal_year": int(r[6]) if r[6] is not None else None,
                "period_type": r[7],
                "period_end": r[8],
                "source_doc_id": r[9],
                "source_title": r[10],
                "source_url": r[11],
            })
        return results

    def compare_company_metrics(
        self,
        tickers: list[str],
        metric_name: str,
        period_type: str = "annual",
        fiscal_year: int | None = None,
    ) -> list[dict]:
        """Compare a financial metric across multiple companies."""
        clean_tickers = [t.strip().upper() for t in tickers]
        cypher = """
        MATCH (c:Company)-[:HAS_OBSERVATION]->(o:FinancialObservation)-[:MEASURES]->(m:FinancialMetric)
        WHERE c.ticker IN $tickers
          AND (toLower(m.name) = toLower($metric) OR toLower(m.id) = toLower($metric))
          AND ($period_type = 'all' OR o.period_type = $period_type)
          AND ($fiscal_year IS NULL OR o.fiscal_year = $fiscal_year)
        OPTIONAL MATCH (o)-[:SUPPORTED_BY]->(doc:SourceDocument)
        RETURN c.ticker, c.name, m.name, o.fiscal_year, o.value, o.currency, o.period_type, doc.title, doc.source_url
        ORDER BY o.fiscal_year ASC, c.ticker ASC
        """
        rows = self.ro_query(cypher, {
            "tickers": clean_tickers,
            "metric": metric_name,
            "period_type": period_type,
            "fiscal_year": fiscal_year,
        })
        results = []
        for r in rows:
            results.append({
                "ticker": r[0],
                "company_name": r[1],
                "metric": r[2],
                "fiscal_year": int(r[3]) if r[3] is not None else None,
                "value": float(r[4]) if r[4] is not None else None,
                "currency": r[5],
                "period_type": r[6],
                "source_title": r[7],
                "source_url": r[8],
            })
        return results

    def get_sector_peers(self, ticker: str) -> list[dict]:
        """Find all peer companies belonging to the same sector."""
        sym = ticker.strip().upper()
        cypher = """
        MATCH (target:Company {ticker: $ticker})-[:BELONGS_TO]->(s:Sector)<-[:BELONGS_TO]-(peer:Company)
        WHERE peer.ticker <> $ticker
        OPTIONAL MATCH (peer)-[:OPERATES_IN]->(ind:Industry)
        RETURN peer.ticker, peer.name, peer.exchange, peer.country, s.name, ind.name
        ORDER BY peer.name ASC
        """
        rows = self.ro_query(cypher, {"ticker": sym})
        peers = []
        for r in rows:
            peers.append({
                "ticker": r[0],
                "name": r[1],
                "exchange": r[2],
                "country": r[3],
                "sector": r[4],
                "industry": r[5] or "N/A",
            })
        return peers

    def get_peer_metric_comparison(
        self,
        ticker: str,
        metric_name: str,
        fiscal_year: int | None = None,
    ) -> list[dict]:
        """Compare a target company with all sector peers on a specified metric."""
        sym = ticker.strip().upper()
        cypher = """
        MATCH (target:Company {ticker: $ticker})-[:BELONGS_TO]->(s:Sector)<-[:BELONGS_TO]-(c:Company)
        MATCH (c)-[:HAS_OBSERVATION]->(o:FinancialObservation)-[:MEASURES]->(m:FinancialMetric)
        WHERE (toLower(m.name) = toLower($metric) OR toLower(m.id) = toLower($metric))
          AND o.period_type = 'annual'
          AND ($fiscal_year IS NULL OR o.fiscal_year = $fiscal_year)
        OPTIONAL MATCH (o)-[:SUPPORTED_BY]->(doc:SourceDocument)
        RETURN c.ticker, c.name, s.name, o.fiscal_year, o.value, o.currency,
               (c.ticker = $ticker) AS is_target, doc.title, doc.source_url
        ORDER BY o.fiscal_year DESC, o.value DESC
        """
        rows = self.ro_query(cypher, {
            "ticker": sym,
            "metric": metric_name,
            "fiscal_year": fiscal_year,
        })
        results = []
        for r in rows:
            results.append({
                "ticker": r[0],
                "name": r[1],
                "sector": r[2],
                "fiscal_year": int(r[3]) if r[3] is not None else None,
                "value": float(r[4]) if r[4] is not None else None,
                "currency": r[5],
                "is_target": bool(r[6]),
                "source_title": r[7],
                "source_url": r[8],
            })
        return results

    def get_financial_evidence(self, observation_id: str) -> dict | None:
        """Trace the full evidence provenance path for a single financial observation."""
        cypher = """
        MATCH (c:Company)-[:HAS_OBSERVATION]->(o:FinancialObservation {id: $obs_id})-[:MEASURES]->(m:FinancialMetric)
        OPTIONAL MATCH (stmt:FinancialStatement)-[:CONTAINS]->(o)
        OPTIONAL MATCH (stmt)-[:FOR_PERIOD]->(p:ReportingPeriod)
        OPTIONAL MATCH (o)-[:SUPPORTED_BY]->(doc:SourceDocument)
        RETURN c.ticker, c.name, m.name, m.unit, o.id, o.value, o.currency,
               o.fiscal_year, o.period_type, o.period_end, o.filing_date,
               stmt.type, p.id, doc.id, doc.title, doc.source_url, doc.publication_date
        LIMIT 1
        """
        rows = self.ro_query(cypher, {"obs_id": observation_id})
        if not rows:
            return None
        r = rows[0]
        return {
            "company_ticker": r[0],
            "company_name": r[1],
            "metric_name": r[2],
            "metric_unit": r[3],
            "observation_id": r[4],
            "value": float(r[5]) if r[5] is not None else None,
            "currency": r[6],
            "fiscal_year": int(r[7]) if r[7] is not None else None,
            "period_type": r[8],
            "period_end": r[9],
            "filing_date": r[10],
            "statement_type": r[11],
            "period_id": r[12],
            "document_id": r[13],
            "document_title": r[14],
            "document_url": r[15],
            "publication_date": r[16],
            "source_title": r[14],
            "source_url": r[15],
            "source_pub_date": r[16],
            "source_license": "Public SEC 10-K Filing / Public Annual Report",
            "formatted_value": f"{float(r[5]):,.2f}" if r[5] is not None else "N/A",
            "evidence_path": (
                f"(:Company {{ticker: '{r[0]}'}}) "
                f"-[:HAS_OBSERVATION]-> (:FinancialObservation {{id: '{r[4]}', value: {r[5]}}}) "
                f"-[:MEASURES]-> (:FinancialMetric {{name: '{r[2]}'}}) "
                f"| -[:SUPPORTED_BY]-> (:SourceDocument {{title: '{r[14]}'}})"
            ),
        }

    def trace_evidence_path(self, ticker: str, metric_name: str, fiscal_year: int | None = None) -> dict | None:
        """Trace evidence path for a specific ticker, metric, and optional year (defaults to latest)."""
        sym = ticker.strip().upper()
        if fiscal_year:
            cypher = """
            MATCH (c:Company {ticker: $ticker})-[:HAS_OBSERVATION]->(o:FinancialObservation)-[:MEASURES]->(m:FinancialMetric)
            WHERE (toLower(m.name) = toLower($metric) OR toLower(m.id) = toLower($metric))
              AND o.fiscal_year = $fiscal_year
              AND o.period_type = 'annual'
            RETURN o.id LIMIT 1
            """
            params = {"ticker": sym, "metric": metric_name, "fiscal_year": fiscal_year}
        else:
            cypher = """
            MATCH (c:Company {ticker: $ticker})-[:HAS_OBSERVATION]->(o:FinancialObservation)-[:MEASURES]->(m:FinancialMetric)
            WHERE (toLower(m.name) = toLower($metric) OR toLower(m.id) = toLower($metric))
              AND o.period_type = 'annual'
            RETURN o.id
            ORDER BY o.fiscal_year DESC
            LIMIT 1
            """
            params = {"ticker": sym, "metric": metric_name}
        rows = self.ro_query(cypher, params)
        if not rows:
            return None
        return self.get_financial_evidence(rows[0][0])

    def get_company_financial_history(self, ticker: str, period_type: str = "annual") -> dict:
        """Fetch all available metrics and historical observations for a company."""
        sym = ticker.strip().upper()
        cypher = """
        MATCH (c:Company {ticker: $ticker})-[:HAS_OBSERVATION]->(o:FinancialObservation)-[:MEASURES]->(m:FinancialMetric)
        WHERE ($period_type = 'all' OR o.period_type = $period_type)
        RETURN m.name, o.fiscal_year, o.value, o.currency, o.period_end
        ORDER BY m.name ASC, o.fiscal_year ASC
        """
        rows = self.ro_query(cypher, {"ticker": sym, "period_type": period_type})
        history: dict[str, list[dict]] = {}
        for r in rows:
            metric = r[0]
            if metric not in history:
                history[metric] = []
            history[metric].append({
                "year": int(r[1]) if r[1] is not None else None,
                "value": float(r[2]) if r[2] is not None else None,
                "currency": r[3],
                "period_end": r[4],
            })
        return {"ticker": sym, "metrics": history}

    def get_available_financial_years(self, ticker: str) -> list[int]:
        """Return sorted list of distinct fiscal years available for a company."""
        sym = ticker.strip().upper()
        cypher = """
        MATCH (c:Company {ticker: $ticker})-[:HAS_OBSERVATION]->(o:FinancialObservation)
        WHERE o.period_type = 'annual' AND o.fiscal_year IS NOT NULL
        RETURN DISTINCT o.fiscal_year
        ORDER BY o.fiscal_year ASC
        """
        rows = self.ro_query(cypher, {"ticker": sym})
        return [int(r[0]) for r in rows if r[0] is not None]

    def get_annual_financials(self, ticker: str, years: list[int] | None = None) -> dict[int, dict[str, Any]]:
        """Retrieve all annual financial metrics mapped by fiscal year.
        
        Returns:
            {2021: {"Total Revenue": ..., "Net Income": ..., "Operating Margin": ..., "Free Cash Flow": ..., "currency": "USD"}, ...}
        """
        sym = ticker.strip().upper()
        cypher = """
        MATCH (c:Company {ticker: $ticker})-[:HAS_OBSERVATION]->(o:FinancialObservation)-[:MEASURES]->(m:FinancialMetric)
        WHERE o.period_type = 'annual' AND o.fiscal_year IS NOT NULL
          AND ($years IS NULL OR o.fiscal_year IN $years)
        RETURN o.fiscal_year, m.name, o.value, o.currency
        ORDER BY o.fiscal_year ASC, m.name ASC
        """
        rows = self.ro_query(cypher, {"ticker": sym, "years": years})
        result: dict[int, dict[str, Any]] = {}
        for r in rows:
            fy = int(r[0])
            metric = r[1]
            val = float(r[2]) if r[2] is not None else None
            curr = r[3] or "USD"
            if fy not in result:
                result[fy] = {"currency": curr}
            result[fy][metric] = val
        return result

    def get_quarterly_financials(self, ticker: str, years: list[int] | None = None) -> list[dict]:
        """Retrieve quarterly financial observations for a company."""
        sym = ticker.strip().upper()
        cypher = """
        MATCH (c:Company {ticker: $ticker})-[:HAS_OBSERVATION]->(o:FinancialObservation)-[:MEASURES]->(m:FinancialMetric)
        WHERE o.period_type = 'quarterly'
          AND ($years IS NULL OR o.fiscal_year IN $years)
        RETURN o.fiscal_year, o.period_end, m.name, o.value, o.currency
        ORDER BY o.period_end ASC, m.name ASC
        """
        rows = self.ro_query(cypher, {"ticker": sym, "years": years})
        results = []
        for r in rows:
            results.append({
                "fiscal_year": int(r[0]) if r[0] is not None else None,
                "period_end": r[1],
                "metric": r[2],
                "value": float(r[3]) if r[3] is not None else None,
                "currency": r[4] or "USD",
            })
        return results

    def get_financial_metric(self, ticker: str, metric: str, year: int) -> dict | None:
        """Retrieve single metric observation for a specific fiscal year."""
        obs = self.get_company_financials(ticker, metric, fiscal_year=year, limit=1)
        return obs[0] if obs else None

    def compare_financial_years(self, ticker: str, metrics: list[str], years: list[int] | None = None) -> dict[str, Any]:
        """Compare specific metrics across specified fiscal years."""
        sym = ticker.strip().upper()
        financials = self.get_annual_financials(sym, years=years)
        return {
            "ticker": sym,
            "years": sorted(list(financials.keys())),
            "metrics": metrics,
            "data": financials,
        }

    def get_financial_data_coverage(self, ticker: str) -> dict[str, Any]:
        """Return available data coverage: min year, max year, available years, and tracked metrics."""
        years = self.get_available_financial_years(ticker)
        history = self.get_company_financial_history(ticker, period_type="annual")
        metrics = list(history.get("metrics", {}).keys())
        return {
            "ticker": ticker.strip().upper(),
            "years_count": len(years),
            "years": years,
            "min_year": min(years) if years else None,
            "max_year": max(years) if years else None,
            "metrics": metrics,
            "has_multi_year": len(years) >= 2,
        }

    def get_graph_statistics(self) -> dict:
        """Get summary node and relationship counts from FalkorDB."""
        stats: dict[str, int] = {}
        labels = [
            "Company", "Sector", "Industry", "FinancialMetric",
            "FinancialObservation", "ReportingPeriod", "FinancialStatement", "SourceDocument"
        ]
        for lbl in labels:
            try:
                res = self.ro_query(f"MATCH (n:{lbl}) RETURN count(n)")
                stats[lbl] = res[0][0] if res else 0
            except Exception:
                stats[lbl] = 0

        rel_types = [
            "BELONGS_TO", "OPERATES_IN", "REPORTS", "FOR_PERIOD",
            "CONTAINS", "MEASURES", "SUPPORTED_BY", "HAS_OBSERVATION"
        ]
        for rel in rel_types:
            try:
                res = self.ro_query(f"MATCH ()-[r:{rel}]->() RETURN count(r)")
                stats[f"REL_{rel}"] = res[0][0] if res else 0
            except Exception:
                stats[f"REL_{rel}"] = 0

        return stats


_fkg_instance: FinancialKnowledgeGraph | None = None


def get_financial_graph(host: str | None = None, port: int | None = None, graph_name: str | None = None) -> FinancialKnowledgeGraph:
    """Singleton getter for the FinancialKnowledgeGraph client."""
    global _fkg_instance
    if _fkg_instance is None:
        _fkg_instance = FinancialKnowledgeGraph(host=host, port=port, graph_name=graph_name)
    return _fkg_instance
