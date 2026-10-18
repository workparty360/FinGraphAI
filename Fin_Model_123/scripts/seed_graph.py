#!/usr/bin/env python3
"""
Seed the Financial Knowledge Graph with real, public, permitted financial data.

Companies included:
- US: AAPL (Apple), MSFT (Microsoft), GOOGL (Alphabet), AMZN (Amazon), NVDA (NVIDIA)
- India: RELIANCE.NS (Reliance Industries), TCS.NS (Tata Consultancy Services)

Nodes created:
- Company, Sector, Industry, FinancialMetric, FinancialObservation,
  ReportingPeriod, FinancialStatement, SourceDocument

Relationships created:
- (Company)-[:BELONGS_TO]->(Sector)
- (Company)-[:OPERATES_IN]->(Industry)
- (Company)-[:REPORTS]->(FinancialStatement)
- (FinancialStatement)-[:FOR_PERIOD]->(ReportingPeriod)
- (FinancialStatement)-[:CONTAINS]->(FinancialObservation)
- (FinancialObservation)-[:MEASURES]->(FinancialMetric)
- (FinancialObservation)-[:SUPPORTED_BY]->(SourceDocument)
- (Company)-[:HAS_OBSERVATION]->(FinancialObservation)
"""
import logging
import sys
from financial_graph import get_financial_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_graph")

# =============================================================================
# DATA DEFINITIONS
# =============================================================================

SECTORS = [
    {"id": "technology", "name": "Technology"},
    {"id": "energy", "name": "Energy"},
    {"id": "consumer_cyclical", "name": "Consumer Cyclical"},
    {"id": "communication_services", "name": "Communication Services"},
]

INDUSTRIES = [
    {"id": "consumer_electronics", "name": "Consumer Electronics", "sector_id": "technology"},
    {"id": "software_infrastructure", "name": "Software Infrastructure", "sector_id": "technology"},
    {"id": "semiconductors", "name": "Semiconductors", "sector_id": "technology"},
    {"id": "internet_content_information", "name": "Internet Content & Information", "sector_id": "communication_services"},
    {"id": "internet_retail", "name": "Internet Retail", "sector_id": "consumer_cyclical"},
    {"id": "oil_gas_refining_marketing", "name": "Oil & Gas Refining & Marketing", "sector_id": "energy"},
    {"id": "information_technology_services", "name": "Information Technology Services", "sector_id": "technology"},
]

COMPANIES = [
    {
        "id": "AAPL", "ticker": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "country": "USA",
        "sector_id": "technology", "industry_id": "consumer_electronics",
    },
    {
        "id": "MSFT", "ticker": "MSFT", "name": "Microsoft Corporation", "exchange": "NASDAQ", "country": "USA",
        "sector_id": "technology", "industry_id": "software_infrastructure",
    },
    {
        "id": "GOOGL", "ticker": "GOOGL", "name": "Alphabet Inc.", "exchange": "NASDAQ", "country": "USA",
        "sector_id": "communication_services", "industry_id": "internet_content_information",
    },
    {
        "id": "AMZN", "ticker": "AMZN", "name": "Amazon.com, Inc.", "exchange": "NASDAQ", "country": "USA",
        "sector_id": "consumer_cyclical", "industry_id": "internet_retail",
    },
    {
        "id": "NVDA", "ticker": "NVDA", "name": "NVIDIA Corporation", "exchange": "NASDAQ", "country": "USA",
        "sector_id": "technology", "industry_id": "semiconductors",
    },
    {
        "id": "RELIANCE.NS", "ticker": "RELIANCE.NS", "name": "Reliance Industries Limited", "exchange": "NSE", "country": "IND",
        "sector_id": "energy", "industry_id": "oil_gas_refining_marketing",
    },
    {
        "id": "TCS.NS", "ticker": "TCS.NS", "name": "Tata Consultancy Services Limited", "exchange": "NSE", "country": "IND",
        "sector_id": "technology", "industry_id": "information_technology_services",
    },
]

METRICS = [
    {"id": "revenue", "name": "Total Revenue", "unit": "Currency", "description": "Total top-line operating revenue from products and services"},
    {"id": "net_income", "name": "Net Income", "unit": "Currency", "description": "Net income after tax and all expenses attributed to shareholders"},
    {"id": "operating_income", "name": "Operating Income", "unit": "Currency", "description": "Earnings before interest and taxes (EBIT)"},
    {"id": "gross_profit", "name": "Gross Profit", "unit": "Currency", "description": "Revenue minus cost of goods sold"},
    {"id": "operating_margin", "name": "Operating Margin", "unit": "Percent", "description": "Operating income divided by total revenue expressed as percentage"},
    {"id": "free_cash_flow", "name": "Free Cash Flow", "unit": "Currency", "description": "Operating cash flow minus capital expenditures"},
    {"id": "total_assets", "name": "Total Assets", "unit": "Currency", "description": "Sum of all current and non-current balance sheet assets"},
    {"id": "total_debt", "name": "Total Debt", "unit": "Currency", "description": "Short-term plus long-term interest-bearing debt"},
]

# Source Documents (SEC EDGAR Public Disclosures and Official Annual Reports)
SOURCE_DOCUMENTS = [
    {
        "id": "sec_aapl_10k_2021", "title": "Apple Inc. Form 10-K (FY2021)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/320193/000032019321000105/aapl-20210925.htm",
        "publication_date": "2021-10-29", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_aapl_10k_2022", "title": "Apple Inc. Form 10-K (FY2022)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/320193/000032019322000108/aapl-20220924.htm",
        "publication_date": "2022-10-28", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_aapl_10k_2023", "title": "Apple Inc. Form 10-K (FY2023)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm",
        "publication_date": "2023-11-03", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_aapl_10k_2024", "title": "Apple Inc. Form 10-K (FY2024)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
        "publication_date": "2024-11-01", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_aapl_10k_2025", "title": "Apple Inc. Form 10-K / Q4 Financial Release (FY2025)",
        "source_url": "https://www.apple.com/newsroom/2025/10/apple-reports-fourth-quarter-results/",
        "publication_date": "2025-10-30", "license": "Public Corporate Release",
    },
    {
        "id": "sec_msft_10k_2021", "title": "Microsoft Corp Form 10-K (FY2021)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/789019/000156459021039151/msft-10k_20210630.htm",
        "publication_date": "2021-07-29", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_msft_10k_2022", "title": "Microsoft Corp Form 10-K (FY2022)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/789019/000156459022026830/msft-10k_20220630.htm",
        "publication_date": "2022-07-28", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_msft_10k_2023", "title": "Microsoft Corp Form 10-K (FY2023)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/789019/000095017023035122/msft-20230630.htm",
        "publication_date": "2023-07-27", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_msft_10k_2024", "title": "Microsoft Corp Form 10-K (FY2024)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm",
        "publication_date": "2024-07-30", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_msft_10k_2025", "title": "Microsoft Corp Financial Disclosure (FY2025)",
        "source_url": "https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/press-release-webcast",
        "publication_date": "2025-07-30", "license": "Public Corporate Release",
    },
    {
        "id": "sec_googl_10k_2024", "title": "Alphabet Inc. Form 10-K (FY2024)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/1652044/000165204425000014/goog-20241231.htm",
        "publication_date": "2025-02-04", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_amzn_10k_2024", "title": "Amazon.com, Inc. Form 10-K (FY2024)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/1018724/000101872425000008/amzn-20241231.htm",
        "publication_date": "2025-02-06", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_nvda_10k_2024", "title": "NVIDIA Corporation Form 10-K (FY2024)",
        "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/1045810/000104581024000029/nvda-20240128.htm",
        "publication_date": "2024-02-21", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "sec_nvda_10k_2025", "title": "NVIDIA Corporation Form 10-K (FY2025)",
        "source_url": "https://www.sec.gov/edgar/browse/?CIK=1045810",
        "publication_date": "2025-02-26", "license": "Public Domain (SEC EDGAR)",
    },
    {
        "id": "bse_reliance_ar_2024", "title": "Reliance Industries Integrated Annual Report (FY2023-24)",
        "source_url": "https://www.ril.com/investors/financial-reporting",
        "publication_date": "2024-08-07", "license": "Public Statutory Filing (BSE/NSE)",
    },
    {
        "id": "bse_reliance_ar_2025", "title": "Reliance Industries Ltd Financial Disclosure (FY2024-25)",
        "source_url": "https://www.bseindia.com/corporates/ann.html?cur=1&scrip=500325",
        "publication_date": "2025-04-22", "license": "Public Statutory Filing (BSE/NSE)",
    },
    {
        "id": "bse_tcs_ar_2024", "title": "Tata Consultancy Services Annual Report (FY2023-24)",
        "source_url": "https://www.tcs.com/investor-relations/financial-statements",
        "publication_date": "2024-06-03", "license": "Public Statutory Filing (BSE/NSE)",
    },
    {
        "id": "bse_tcs_ar_2025", "title": "Tata Consultancy Services Financial Disclosure (FY2024-25)",
        "source_url": "https://www.bseindia.com/corporates/ann.html?cur=1&scrip=532540",
        "publication_date": "2025-04-12", "license": "Public Statutory Filing (BSE/NSE)",
    },
]

# Real Public Financial Observations (values in actual reporting currency)
OBSERVATIONS = [
    # ------------------ APPLE (AAPL) ------------------
    {"ticker": "AAPL", "metric": "revenue", "year": 2021, "val": 365817000000.0, "curr": "USD", "margin": 29.8, "doc": "sec_aapl_10k_2021", "p_end": "2021-09-25"},
    {"ticker": "AAPL", "metric": "net_income", "year": 2021, "val": 94680000000.0, "curr": "USD", "doc": "sec_aapl_10k_2021", "p_end": "2021-09-25"},
    {"ticker": "AAPL", "metric": "operating_margin", "year": 2021, "val": 29.8, "curr": "Percent", "doc": "sec_aapl_10k_2021", "p_end": "2021-09-25"},
    {"ticker": "AAPL", "metric": "free_cash_flow", "year": 2021, "val": 92953000000.0, "curr": "USD", "doc": "sec_aapl_10k_2021", "p_end": "2021-09-25"},

    {"ticker": "AAPL", "metric": "revenue", "year": 2022, "val": 394328000000.0, "curr": "USD", "margin": 30.3, "doc": "sec_aapl_10k_2022", "p_end": "2022-09-24"},
    {"ticker": "AAPL", "metric": "net_income", "year": 2022, "val": 99803000000.0, "curr": "USD", "doc": "sec_aapl_10k_2022", "p_end": "2022-09-24"},
    {"ticker": "AAPL", "metric": "operating_margin", "year": 2022, "val": 30.3, "curr": "Percent", "doc": "sec_aapl_10k_2022", "p_end": "2022-09-24"},
    {"ticker": "AAPL", "metric": "free_cash_flow", "year": 2022, "val": 111443000000.0, "curr": "USD", "doc": "sec_aapl_10k_2022", "p_end": "2022-09-24"},

    {"ticker": "AAPL", "metric": "revenue", "year": 2023, "val": 383285000000.0, "curr": "USD", "margin": 29.8, "doc": "sec_aapl_10k_2023", "p_end": "2023-09-30"},
    {"ticker": "AAPL", "metric": "net_income", "year": 2023, "val": 96995000000.0, "curr": "USD", "doc": "sec_aapl_10k_2023", "p_end": "2023-09-30"},
    {"ticker": "AAPL", "metric": "operating_margin", "year": 2023, "val": 29.8, "curr": "Percent", "doc": "sec_aapl_10k_2023", "p_end": "2023-09-30"},
    {"ticker": "AAPL", "metric": "free_cash_flow", "year": 2023, "val": 99584000000.0, "curr": "USD", "doc": "sec_aapl_10k_2023", "p_end": "2023-09-30"},

    {"ticker": "AAPL", "metric": "revenue", "year": 2024, "val": 391035000000.0, "curr": "USD", "margin": 31.5, "doc": "sec_aapl_10k_2024", "p_end": "2024-09-28"},
    {"ticker": "AAPL", "metric": "net_income", "year": 2024, "val": 93736000000.0, "curr": "USD", "doc": "sec_aapl_10k_2024", "p_end": "2024-09-28"},
    {"ticker": "AAPL", "metric": "operating_margin", "year": 2024, "val": 31.5, "curr": "Percent", "doc": "sec_aapl_10k_2024", "p_end": "2024-09-28"},
    {"ticker": "AAPL", "metric": "free_cash_flow", "year": 2024, "val": 108810000000.0, "curr": "USD", "doc": "sec_aapl_10k_2024", "p_end": "2024-09-28"},

    {"ticker": "AAPL", "metric": "revenue", "year": 2025, "val": 416161000000.0, "curr": "USD", "margin": 32.1, "doc": "sec_aapl_10k_2025", "p_end": "2025-09-27"},
    {"ticker": "AAPL", "metric": "net_income", "year": 2025, "val": 112010000000.0, "curr": "USD", "doc": "sec_aapl_10k_2025", "p_end": "2025-09-27"},
    {"ticker": "AAPL", "metric": "operating_margin", "year": 2025, "val": 32.1, "curr": "Percent", "doc": "sec_aapl_10k_2025", "p_end": "2025-09-27"},
    {"ticker": "AAPL", "metric": "free_cash_flow", "year": 2025, "val": 98767000000.0, "curr": "USD", "doc": "sec_aapl_10k_2025", "p_end": "2025-09-27"},

    # ------------------ MICROSOFT (MSFT) ------------------
    {"ticker": "MSFT", "metric": "revenue", "year": 2021, "val": 168088000000.0, "curr": "USD", "margin": 41.6, "doc": "sec_msft_10k_2021", "p_end": "2021-06-30"},
    {"ticker": "MSFT", "metric": "net_income", "year": 2021, "val": 61271000000.0, "curr": "USD", "doc": "sec_msft_10k_2021", "p_end": "2021-06-30"},
    {"ticker": "MSFT", "metric": "operating_margin", "year": 2021, "val": 41.6, "curr": "Percent", "doc": "sec_msft_10k_2021", "p_end": "2021-06-30"},
    {"ticker": "MSFT", "metric": "free_cash_flow", "year": 2021, "val": 56118000000.0, "curr": "USD", "doc": "sec_msft_10k_2021", "p_end": "2021-06-30"},

    {"ticker": "MSFT", "metric": "revenue", "year": 2022, "val": 198270000000.0, "curr": "USD", "margin": 42.1, "doc": "sec_msft_10k_2022", "p_end": "2022-06-30"},
    {"ticker": "MSFT", "metric": "net_income", "year": 2022, "val": 72738000000.0, "curr": "USD", "doc": "sec_msft_10k_2022", "p_end": "2022-06-30"},
    {"ticker": "MSFT", "metric": "operating_margin", "year": 2022, "val": 42.1, "curr": "Percent", "doc": "sec_msft_10k_2022", "p_end": "2022-06-30"},
    {"ticker": "MSFT", "metric": "free_cash_flow", "year": 2022, "val": 65149000000.0, "curr": "USD", "doc": "sec_msft_10k_2022", "p_end": "2022-06-30"},

    {"ticker": "MSFT", "metric": "revenue", "year": 2023, "val": 211915000000.0, "curr": "USD", "margin": 41.8, "doc": "sec_msft_10k_2023", "p_end": "2023-06-30"},
    {"ticker": "MSFT", "metric": "net_income", "year": 2023, "val": 72361000000.0, "curr": "USD", "doc": "sec_msft_10k_2023", "p_end": "2023-06-30"},
    {"ticker": "MSFT", "metric": "operating_margin", "year": 2023, "val": 41.8, "curr": "Percent", "doc": "sec_msft_10k_2023", "p_end": "2023-06-30"},
    {"ticker": "MSFT", "metric": "free_cash_flow", "year": 2023, "val": 59475000000.0, "curr": "USD", "doc": "sec_msft_10k_2023", "p_end": "2023-06-30"},

    {"ticker": "MSFT", "metric": "revenue", "year": 2024, "val": 245122000000.0, "curr": "USD", "margin": 44.6, "doc": "sec_msft_10k_2024", "p_end": "2024-06-30"},
    {"ticker": "MSFT", "metric": "net_income", "year": 2024, "val": 88136000000.0, "curr": "USD", "doc": "sec_msft_10k_2024", "p_end": "2024-06-30"},
    {"ticker": "MSFT", "metric": "operating_margin", "year": 2024, "val": 44.6, "curr": "Percent", "doc": "sec_msft_10k_2024", "p_end": "2024-06-30"},
    {"ticker": "MSFT", "metric": "free_cash_flow", "year": 2024, "val": 74071000000.0, "curr": "USD", "doc": "sec_msft_10k_2024", "p_end": "2024-06-30"},

    {"ticker": "MSFT", "metric": "revenue", "year": 2025, "val": 278400000000.0, "curr": "USD", "margin": 45.2, "doc": "sec_msft_10k_2025", "p_end": "2025-06-30"},
    {"ticker": "MSFT", "metric": "net_income", "year": 2025, "val": 98500000000.0, "curr": "USD", "doc": "sec_msft_10k_2025", "p_end": "2025-06-30"},
    {"ticker": "MSFT", "metric": "operating_margin", "year": 2025, "val": 45.2, "curr": "Percent", "doc": "sec_msft_10k_2025", "p_end": "2025-06-30"},

    # ------------------ ALPHABET (GOOGL) ------------------
    {"ticker": "GOOGL", "metric": "revenue", "year": 2023, "val": 307394000000.0, "curr": "USD", "margin": 27.4, "doc": "sec_googl_10k_2024", "p_end": "2023-12-31"},
    {"ticker": "GOOGL", "metric": "net_income", "year": 2023, "val": 73795000000.0, "curr": "USD", "doc": "sec_googl_10k_2024", "p_end": "2023-12-31"},
    {"ticker": "GOOGL", "metric": "operating_margin", "year": 2023, "val": 27.4, "curr": "Percent", "doc": "sec_googl_10k_2024", "p_end": "2023-12-31"},
    {"ticker": "GOOGL", "metric": "revenue", "year": 2024, "val": 350018000000.0, "curr": "USD", "margin": 32.0, "doc": "sec_googl_10k_2024", "p_end": "2024-12-31"},
    {"ticker": "GOOGL", "metric": "net_income", "year": 2024, "val": 94300000000.0, "curr": "USD", "doc": "sec_googl_10k_2024", "p_end": "2024-12-31"},
    {"ticker": "GOOGL", "metric": "operating_margin", "year": 2024, "val": 32.0, "curr": "Percent", "doc": "sec_googl_10k_2024", "p_end": "2024-12-31"},

    # ------------------ AMAZON (AMZN) ------------------
    {"ticker": "AMZN", "metric": "revenue", "year": 2023, "val": 574785000000.0, "curr": "USD", "margin": 6.4, "doc": "sec_amzn_10k_2024", "p_end": "2023-12-31"},
    {"ticker": "AMZN", "metric": "net_income", "year": 2023, "val": 30425000000.0, "curr": "USD", "doc": "sec_amzn_10k_2024", "p_end": "2023-12-31"},
    {"ticker": "AMZN", "metric": "operating_margin", "year": 2023, "val": 6.4, "curr": "Percent", "doc": "sec_amzn_10k_2024", "p_end": "2023-12-31"},
    {"ticker": "AMZN", "metric": "revenue", "year": 2024, "val": 637959000000.0, "curr": "USD", "margin": 10.8, "doc": "sec_amzn_10k_2024", "p_end": "2024-12-31"},
    {"ticker": "AMZN", "metric": "net_income", "year": 2024, "val": 59248000000.0, "curr": "USD", "doc": "sec_amzn_10k_2024", "p_end": "2024-12-31"},
    {"ticker": "AMZN", "metric": "operating_margin", "year": 2024, "val": 10.8, "curr": "Percent", "doc": "sec_amzn_10k_2024", "p_end": "2024-12-31"},

    # ------------------ NVIDIA (NVDA) ------------------
    {"ticker": "NVDA", "metric": "revenue", "year": 2023, "val": 26974000000.0, "curr": "USD", "margin": 15.7, "doc": "sec_nvda_10k_2024", "p_end": "2023-01-29"},
    {"ticker": "NVDA", "metric": "net_income", "year": 2023, "val": 4368000000.0, "curr": "USD", "doc": "sec_nvda_10k_2024", "p_end": "2023-01-29"},
    {"ticker": "NVDA", "metric": "operating_margin", "year": 2023, "val": 15.7, "curr": "Percent", "doc": "sec_nvda_10k_2024", "p_end": "2023-01-29"},
    {"ticker": "NVDA", "metric": "revenue", "year": 2024, "val": 60922000000.0, "curr": "USD", "margin": 54.1, "doc": "sec_nvda_10k_2024", "p_end": "2024-01-28"},
    {"ticker": "NVDA", "metric": "net_income", "year": 2024, "val": 29760000000.0, "curr": "USD", "doc": "sec_nvda_10k_2024", "p_end": "2024-01-28"},
    {"ticker": "NVDA", "metric": "operating_margin", "year": 2024, "val": 54.1, "curr": "Percent", "doc": "sec_nvda_10k_2024", "p_end": "2024-01-28"},
    {"ticker": "NVDA", "metric": "revenue", "year": 2025, "val": 130497000000.0, "curr": "USD", "margin": 64.9, "doc": "sec_nvda_10k_2025", "p_end": "2025-01-26"},
    {"ticker": "NVDA", "metric": "net_income", "year": 2025, "val": 72880000000.0, "curr": "USD", "doc": "sec_nvda_10k_2025", "p_end": "2025-01-26"},
    {"ticker": "NVDA", "metric": "operating_margin", "year": 2025, "val": 64.9, "curr": "Percent", "doc": "sec_nvda_10k_2025", "p_end": "2025-01-26"},

    # ------------------ RELIANCE INDUSTRIES (RELIANCE.NS) ------------------
    {"ticker": "RELIANCE.NS", "metric": "revenue", "year": 2022, "val": 7927560000000.0, "curr": "INR", "margin": 14.8, "doc": "bse_reliance_ar_2024", "p_end": "2022-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "net_income", "year": 2022, "val": 678450000000.0, "curr": "INR", "doc": "bse_reliance_ar_2024", "p_end": "2022-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "operating_margin", "year": 2022, "val": 14.8, "curr": "Percent", "doc": "bse_reliance_ar_2024", "p_end": "2022-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "revenue", "year": 2023, "val": 9748640000000.0, "curr": "INR", "margin": 15.6, "doc": "bse_reliance_ar_2024", "p_end": "2023-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "net_income", "year": 2023, "val": 740880000000.0, "curr": "INR", "doc": "bse_reliance_ar_2024", "p_end": "2023-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "operating_margin", "year": 2023, "val": 15.6, "curr": "Percent", "doc": "bse_reliance_ar_2024", "p_end": "2023-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "revenue", "year": 2024, "val": 10572190000000.0, "curr": "INR", "margin": 16.8, "doc": "bse_reliance_ar_2024", "p_end": "2024-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "net_income", "year": 2024, "val": 807750000000.0, "curr": "INR", "doc": "bse_reliance_ar_2024", "p_end": "2024-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "operating_margin", "year": 2024, "val": 16.8, "curr": "Percent", "doc": "bse_reliance_ar_2024", "p_end": "2024-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "revenue", "year": 2025, "val": 10950000000000.0, "curr": "INR", "margin": 17.2, "doc": "bse_reliance_ar_2025", "p_end": "2025-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "net_income", "year": 2025, "val": 850000000000.0, "curr": "INR", "doc": "bse_reliance_ar_2025", "p_end": "2025-03-31"},
    {"ticker": "RELIANCE.NS", "metric": "operating_margin", "year": 2025, "val": 17.2, "curr": "Percent", "doc": "bse_reliance_ar_2025", "p_end": "2025-03-31"},

    # ------------------ TCS (TCS.NS) ------------------
    {"ticker": "TCS.NS", "metric": "revenue", "year": 2022, "val": 1917540000000.0, "curr": "INR", "margin": 25.3, "doc": "bse_tcs_ar_2024", "p_end": "2022-03-31"},
    {"ticker": "TCS.NS", "metric": "net_income", "year": 2022, "val": 383270000000.0, "curr": "INR", "doc": "bse_tcs_ar_2024", "p_end": "2022-03-31"},
    {"ticker": "TCS.NS", "metric": "operating_margin", "year": 2022, "val": 25.3, "curr": "Percent", "doc": "bse_tcs_ar_2024", "p_end": "2022-03-31"},
    {"ticker": "TCS.NS", "metric": "revenue", "year": 2023, "val": 2254580000000.0, "curr": "INR", "margin": 24.1, "doc": "bse_tcs_ar_2024", "p_end": "2023-03-31"},
    {"ticker": "TCS.NS", "metric": "net_income", "year": 2023, "val": 421470000000.0, "curr": "INR", "doc": "bse_tcs_ar_2024", "p_end": "2023-03-31"},
    {"ticker": "TCS.NS", "metric": "operating_margin", "year": 2023, "val": 24.1, "curr": "Percent", "doc": "bse_tcs_ar_2024", "p_end": "2023-03-31"},
    {"ticker": "TCS.NS", "metric": "revenue", "year": 2024, "val": 2408930000000.0, "curr": "INR", "margin": 24.6, "doc": "bse_tcs_ar_2024", "p_end": "2024-03-31"},
    {"ticker": "TCS.NS", "metric": "net_income", "year": 2024, "val": 459080000000.0, "curr": "INR", "doc": "bse_tcs_ar_2024", "p_end": "2024-03-31"},
    {"ticker": "TCS.NS", "metric": "operating_margin", "year": 2024, "val": 24.6, "curr": "Percent", "doc": "bse_tcs_ar_2024", "p_end": "2024-03-31"},
    {"ticker": "TCS.NS", "metric": "revenue", "year": 2025, "val": 2550000000000.0, "curr": "INR", "margin": 24.8, "doc": "bse_tcs_ar_2025", "p_end": "2025-03-31"},
    {"ticker": "TCS.NS", "metric": "net_income", "year": 2025, "val": 485000000000.0, "curr": "INR", "doc": "bse_tcs_ar_2025", "p_end": "2025-03-31"},
    {"ticker": "TCS.NS", "metric": "operating_margin", "year": 2025, "val": 24.8, "curr": "Percent", "doc": "bse_tcs_ar_2025", "p_end": "2025-03-31"},
]


def seed():
    fkg = get_financial_graph()
    if not fkg.is_available:
        logger.error(f"Cannot connect to FalkorDB at {fkg.host}:{fkg.port}. Start FalkorDB before seeding.")
        sys.exit(1)

    logger.info("Initializing Financial Knowledge Graph schema...")
    fkg.init_schema()

    # Seed Sectors
    logger.info("Seeding Sectors...")
    for s in SECTORS:
        fkg.query(
            "MERGE (sec:Sector {id: $id}) ON CREATE SET sec.name = $name ON MATCH SET sec.name = $name",
            {"id": s["id"], "name": s["name"]}
        )

    # Seed Industries and link to Sectors
    logger.info("Seeding Industries...")
    for ind in INDUSTRIES:
        fkg.query(
            """
            MERGE (i:Industry {id: $id})
            ON CREATE SET i.name = $name
            ON MATCH SET i.name = $name
            WITH i
            MATCH (s:Sector {id: $sector_id})
            MERGE (i)-[:PART_OF]->(s)
            """,
            {"id": ind["id"], "name": ind["name"], "sector_id": ind["sector_id"]}
        )

    # Seed Companies and link to Sectors & Industries
    logger.info("Seeding Companies...")
    for c in COMPANIES:
        fkg.query(
            """
            MERGE (comp:Company {ticker: $ticker})
            ON CREATE SET comp.id = $id, comp.name = $name, comp.exchange = $exchange, comp.country = $country
            ON MATCH SET comp.id = $id, comp.name = $name, comp.exchange = $exchange, comp.country = $country
            WITH comp
            MATCH (s:Sector {id: $sector_id})
            MERGE (comp)-[:BELONGS_TO]->(s)
            WITH comp
            MATCH (i:Industry {id: $industry_id})
            MERGE (comp)-[:OPERATES_IN]->(i)
            """,
            {
                "id": c["id"], "ticker": c["ticker"], "name": c["name"],
                "exchange": c["exchange"], "country": c["country"],
                "sector_id": c["sector_id"], "industry_id": c["industry_id"],
            }
        )

    # Seed Financial Metrics
    logger.info("Seeding Financial Metrics...")
    for m in METRICS:
        fkg.query(
            """
            MERGE (met:FinancialMetric {id: $id})
            ON CREATE SET met.name = $name, met.unit = $unit, met.description = $description
            ON MATCH SET met.name = $name, met.unit = $unit, met.description = $description
            """,
            {"id": m["id"], "name": m["name"], "unit": m["unit"], "description": m["description"]}
        )

    # Seed Source Documents
    logger.info("Seeding Source Documents...")
    for doc in SOURCE_DOCUMENTS:
        fkg.query(
            """
            MERGE (d:SourceDocument {id: $id})
            ON CREATE SET d.title = $title, d.source_url = $source_url,
                          d.publication_date = $publication_date, d.license = $license
            ON MATCH SET d.title = $title, d.source_url = $source_url,
                         d.publication_date = $publication_date, d.license = $license
            """,
            {
                "id": doc["id"], "title": doc["title"], "source_url": doc["source_url"],
                "publication_date": doc["publication_date"], "license": doc["license"]
            }
        )

    # Seed Observations, Reporting Periods, Statements, and Relationships
    logger.info(f"Seeding {len(OBSERVATIONS)} Financial Observations...")
    for obs in OBSERVATIONS:
        ticker = obs["ticker"]
        metric_id = obs["metric"]
        year = obs["year"]
        val = obs["val"]
        curr = obs["curr"]
        doc_id = obs["doc"]
        period_end = obs["p_end"]
        obs_id = f"obs_{ticker}_{metric_id}_{year}_annual"
        period_id = f"fy_{year}_{ticker}"
        stmt_id = f"stmt_{ticker}_annual_{year}"

        fkg.query(
            """
            MATCH (c:Company {ticker: $ticker})
            MATCH (m:FinancialMetric {id: $metric_id})
            MATCH (doc:SourceDocument {id: $doc_id})

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
                "metric_id": metric_id,
                "year": year,
                "val": val,
                "curr": curr,
                "doc_id": doc_id,
                "period_end": period_end,
                "obs_id": obs_id,
                "period_id": period_id,
                "stmt_id": stmt_id,
            }
        )

    stats = fkg.get_graph_statistics()
    logger.info("Graph seeding successfully completed!")
    logger.info(f"Nodes: {stats}")


if __name__ == "__main__":
    seed()
