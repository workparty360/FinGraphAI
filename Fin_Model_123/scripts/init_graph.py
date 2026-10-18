#!/usr/bin/env python3
"""
Initialize the Financial Knowledge Graph schema and indexes in FalkorDB.
"""
import logging
import sys
from financial_graph import get_financial_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("init_graph")


def main():
    logger.info("Connecting to FalkorDB Financial Knowledge Graph...")
    fkg = get_financial_graph()

    if not fkg.is_available:
        logger.error(f"Cannot connect to FalkorDB at {fkg.host}:{fkg.port}. Please ensure FalkorDB is running.")
        sys.exit(1)

    logger.info(f"Initializing schema and indexes on graph '{fkg.graph_name}'...")
    fkg.init_schema()

    # Also register node uniqueness constraints where supported or create indexes
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

    for label, prop in indexes:
        try:
            fkg.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            logger.info(f"Created index on :{label}({prop})")
        except Exception:
            # Index might already exist
            pass

    logger.info("Schema and index initialization complete.")


if __name__ == "__main__":
    main()
