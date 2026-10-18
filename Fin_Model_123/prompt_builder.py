"""Structured Prompt Construction Pipeline for FinModel AI.

Builds bounded, structured, and validated prompt packages for the Analyst Agent and Groq API.
Adheres strictly to the architectural requirements:
- Applicable system instructions
- Current user question
- Relevant recent conversation context
- Resolved company and ticker
- Relevant financial facts from FalkorDB
- Source filing references (e.g. SEC 10-K)
- Calculated financial metrics and composite scores
- Relevant session-scoped memories from FalkorDB
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from financial_math import format_financial_amount


def is_general_financial_query(question: str) -> bool:
    """Detect whether a query without an active stock is a general financial concept or educational question."""
    q_lower = (question or "").strip().lower()
    if not q_lower:
        return False

    has_year = bool(re.search(r"\b(?:19|20)\d{2}\b", q_lower)) or bool(re.search(r"\bfy\s*\d{2,4}\b", q_lower))

    general_phrases = [
        "what is ", "what are ", "explain ", "define ", "meaning of ",
        "how does ", "how do ", "difference between ", "formula for ",
        "how to calculate ", "tell me about ", "overview of ", "concept of ",
    ]
    is_general_phrase = any(q_lower.startswith(p) or f" {p}" in q_lower for p in general_phrases)

    financial_terms = [
        "return on equity", "roe", "return on assets", "roa", "return on capital", "roce",
        "balance sheet", "income statement", "cash flow statement", "free cash flow",
        "operating margin", "gross margin", "net margin", "profit margin",
        "ebitda", "ebit", "p/e ratio", "pe ratio", "price to earnings",
        "working capital", "debt to equity", "current ratio", "quick ratio",
        "financial leverage", "capital structure", "amortization", "depreciation",
        "wacc", "cost of capital", "dividend yield", "earnings per share", "eps",
    ]
    has_financial_term = any(term in q_lower for term in financial_terms)

    # If it asks about a general concept and has NO specific year attached, it's general knowledge
    if is_general_phrase and has_financial_term and not has_year:
        return True

    # Standalone ratio/concept questions (e.g. "What is ROE?")
    if (is_general_phrase or has_financial_term) and not has_year and not any(k in q_lower for k in ("its", "their", "his", "her", "share price", "stock price")):
        return True

    return False


def is_unspecified_company_follow_up(question: str, target_ticker: str, conversation_history: list[dict]) -> bool:
    """Detect whether a question is asking for company-specific data but no company is identified or in history."""
    if target_ticker:
        return False

    q_lower = (question or "").strip().lower()
    has_year = bool(re.search(r"\b(?:19|20)\d{2}\b", q_lower)) or bool(re.search(r"\bfy\s*\d{2,4}\b", q_lower))
    has_company_data_words = any(w in q_lower for w in ["revenue", "net income", "profit", "sales", "earnings", "stock", "shares", "dividend", "performance", "best year", "worst year"])
    has_follow_up_phrase = any(p in q_lower for p in ["what about", "how about", "and in", "what was", "how much was"])

    # If it asks for year-specific or company data with no company active
    return (has_year or has_company_data_words or has_follow_up_phrase) and not is_general_financial_query(question)


def build_analyst_prompt_package(
    question: str,
    plan: dict[str, Any],
    retrieved_data: dict[str, Any],
    conversation_history: list[dict] | None = None,
) -> dict[str, Any]:
    """Construct a structured, bounded prompt package for the Analyst Agent and Groq API.

    Returns:
        dict containing:
        - "system_prompt": str
        - "messages": list[BaseMessage]
        - "approx_prompt_tokens": int
        - "context_package": dict (structured JSON facts)
        - "is_general_knowledge": bool
        - "is_unspecified_company": bool
    """
    history = conversation_history or []
    target_ticker = retrieved_data.get("target_ticker") or plan.get("target_ticker", "")
    target_company = retrieved_data.get("target_company") or plan.get("target_company", "")
    target_currency = retrieved_data.get("target_currency") or plan.get("target_currency", "USD")
    target_exchange = retrieved_data.get("target_exchange") or plan.get("target_exchange", "US")
    companies = plan.get("companies") or []
    query_intent = plan.get("intent", "general_knowledge")

    # Check for unspecified company follow-up
    if not target_ticker and is_unspecified_company_follow_up(question, target_ticker, history):
        return {
            "system_prompt": "",
            "messages": [],
            "approx_prompt_tokens": 0,
            "context_package": {},
            "is_general_knowledge": False,
            "is_unspecified_company": True,
            "clarification_message": "No valid stock is currently selected. Which company are you referring to? Please mention a specific company name or select a stock to analyze.",
        }

    # Check for general financial knowledge question
    if not target_ticker or is_general_financial_query(question):
        is_gen = True
        system_prompt = (
            "You are a senior financial analyst and educator. "
            "Task: Explain financial concepts, definitions, metrics, ratios, and principles accurately, "
            "clearly, and comprehensively.\n"
            "Ground rules:\n"
            "1. Define the concept or metric clearly.\n"
            "2. Provide standard formulas or mathematical expressions where applicable.\n"
            "3. Explain what high, low, or changing values indicate for investors and management.\n"
            "4. Mention practical industry context or benchmarks.\n"
            "5. Do not require company-specific filings when explaining general financial concepts."
        )

        messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
        for turn in history[-4:]:
            role = turn.get("role", "user")
            content = turn.get("text") or turn.get("content", "")
            if content:
                if role == "user":
                    messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    messages.append(AIMessage(content=content))

        messages.append(HumanMessage(content=question))

        # Approximate token count (chars / 4)
        total_chars = sum(len(m.content) for m in messages)
        return {
            "system_prompt": system_prompt,
            "messages": messages,
            "approx_prompt_tokens": max(1, total_chars // 4),
            "context_package": {
                "question": question,
                "query_intent": "general_financial_knowledge",
                "financial_facts": [],
                "sources": [],
            },
            "is_general_knowledge": True,
            "is_unspecified_company": False,
            "clarification_message": None,
        }

    # Company-Specific or Comparative Financial Query
    is_gen = False
    financial_facts: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    calculated_metrics: list[dict[str, Any]] = []

    # Extract exact metric observations
    exact_m = retrieved_data.get("exact_metric")
    if exact_m and exact_m.get("value") is not None:
        financial_facts.append({
            "ticker": exact_m.get("ticker", target_ticker),
            "company": exact_m.get("company_name", target_company),
            "fiscal_year": exact_m.get("fiscal_year"),
            "metric": exact_m.get("metric"),
            "value": exact_m.get("value"),
            "formatted_value": format_financial_amount(exact_m.get("value"), exact_m.get("currency", target_currency)),
            "currency": exact_m.get("currency", target_currency),
            "source": exact_m.get("source_title", "Regulatory Filing"),
        })
        if exact_m.get("source_url"):
            sources.append({
                "title": exact_m.get("source_title", "Regulatory Filing"),
                "url": exact_m.get("source_url"),
                "filing_type": exact_m.get("filing_type", "Form 10-K"),
            })

    # Extract single year overview
    syo = retrieved_data.get("single_year_overview")
    if syo and syo.get("metrics"):
        yr = syo.get("year")
        curr = syo.get("currency", target_currency)
        for m_name, val in syo["metrics"].items():
            if m_name != "currency" and val is not None:
                financial_facts.append({
                    "ticker": target_ticker,
                    "company": target_company,
                    "fiscal_year": yr,
                    "metric": m_name,
                    "value": val,
                    "formatted_value": f"{val:.2f}%" if "margin" in m_name.lower() else format_financial_amount(val, curr),
                    "currency": curr,
                    "source": f"Form 10-K (FY{yr})",
                })

    # Extract best year analysis
    bya = retrieved_data.get("best_year_analysis")
    if bya and not bya.get("error"):
        calculated_metrics.append({
            "type": "best_performing_year_composite",
            "best_composite_year": bya.get("best_composite_year"),
            "highest_revenue_year": bya.get("leaders", {}).get("highest_revenue", {}).get("year"),
            "highest_revenue_value": bya.get("leaders", {}).get("highest_revenue", {}).get("formatted"),
            "highest_net_income_year": bya.get("leaders", {}).get("highest_net_income", {}).get("year"),
            "highest_net_income_value": bya.get("leaders", {}).get("highest_net_income", {}).get("formatted"),
            "highest_margin_year": bya.get("leaders", {}).get("highest_operating_margin", {}).get("year"),
            "highest_margin_value": bya.get("leaders", {}).get("highest_operating_margin", {}).get("formatted"),
            "summary": bya.get("summary_text"),
        })

    # Extract performance comparison
    pc = retrieved_data.get("performance_comparison")
    if pc and not pc.get("error"):
        calculated_metrics.append({
            "type": "multi_year_comparison",
            "start_year": pc.get("start_year"),
            "end_year": pc.get("end_year"),
            "revenue_start": format_financial_amount(pc.get("revenue", {}).get("start"), target_currency),
            "revenue_end": format_financial_amount(pc.get("revenue", {}).get("end"), target_currency),
            "net_income_start": format_financial_amount(pc.get("net_income", {}).get("start"), target_currency),
            "net_income_end": format_financial_amount(pc.get("net_income", {}).get("end"), target_currency),
            "summary": pc.get("summary_text"),
        })

    # Extract comparison data across multiple companies
    comp_data = retrieved_data.get("comparison_data") or {}
    metrics_comp = comp_data.get("metrics_comparison", [])
    for row in metrics_comp:
        financial_facts.append({
            "ticker": row.get("ticker"),
            "company": row.get("company_name"),
            "fiscal_year": row.get("fiscal_year"),
            "metric": row.get("metric"),
            "value": row.get("value"),
            "formatted_value": format_financial_amount(row.get("value"), row.get("currency", "USD")),
            "currency": row.get("currency", "USD"),
            "source": row.get("source_title", "Regulatory Filing"),
        })
        if row.get("source_url"):
            sources.append({
                "title": row.get("source_title", "Regulatory Filing"),
                "url": row.get("source_url"),
                "filing_type": row.get("filing_type", "Form 10-K"),
            })

    # Trace evidence path if available
    ev_path = retrieved_data.get("evidence_path")
    if ev_path:
        sources.append({
            "title": ev_path.get("document_title") or ev_path.get("source_title", "Regulatory Filing"),
            "url": ev_path.get("document_url") or ev_path.get("source_url", ""),
            "filing_type": ev_path.get("filing_type", "Form 10-K"),
            "provenance_path": ev_path.get("evidence_path"),
        })

    # Semantic and episodic memories
    relevant_memories = []
    for mem in (retrieved_data.get("memories") or []):
        m_content = mem.get("content", "") if isinstance(mem, dict) else str(mem)
        if m_content:
            relevant_memories.append(m_content[:200])

    context_package = {
        "question": question,
        "company": {
            "name": target_company,
            "ticker": target_ticker,
            "exchange": target_exchange,
            "currency": target_currency,
        },
        "query_intent": query_intent,
        "financial_facts": financial_facts,
        "calculated_metrics": calculated_metrics,
        "relevant_memories": relevant_memories,
        "sources": sources,
    }

    if len(companies) >= 2 or query_intent == "company_comparison":
        comp_descs = ", ".join(f"{c['name']} ({c['ticker']})" for c in companies)
        system_prompt = (
            f"You are a senior financial analyst. Task: Provide a comprehensive, accurate comparative analysis "
            f"between {comp_descs}.\n"
            "Ground rules:\n"
            "1. Compare compatible metrics (Total Revenue, Net Income, Operating Margin) across the same or recent fiscal years.\n"
            "2. Use ONLY the verified financial facts provided in the prompt context. Never fabricate numbers.\n"
            "3. State all currencies clearly.\n"
            "4. Cite the official source documents (SEC Form 10-K) when available.\n"
            "5. Structure your output with a clear summary and a clean comparison table."
        )
    else:
        active_ticker = retrieved_data.get("active_ticker") or ""
        active_note = f" Active Dashboard Selection: {active_ticker}." if active_ticker else ""
        system_prompt = (
            f"You are a principal financial analyst. Symbol: {target_ticker or 'NONE'}.{active_note} "
            f"Company: {target_company or 'NONE'}. Currency: {target_currency or 'NONE'}.\n"
            "Ground rules:\n"
            f"1. Use the verified financial data provided in the context below. Data is sourced from "
            f"Yahoo Finance (yfinance) for all markets including NSE/BSE/US, and SEC EDGAR for US filings.\n"
            "2. Never fabricate, invent, or guess financial figures.\n"
            f"3. Clearly identify the fiscal year (FY) and reporting currency ({target_currency}).\n"
            "4. Distinguish annual and quarterly data when relevant.\n"
            "5. Include source references when provided.\n"
            "6. Provide a concise, clear, and professional financial answer.\n"
            "7. If data is available in the context, use it directly. Do NOT say you cannot provide "
            "documents — the data has already been retrieved and is in the context below."
        )

    # Format structured context into readable text for the model
    context_lines = []
    if financial_facts:
        context_lines.append("### Verified Financial Observations (FalkorDB / SEC EDGAR):")
        for f in financial_facts:
            src = f.get("source", "Official Filing")
            context_lines.append(f"- **{f['company']} ({f['ticker']}) FY{f.get('fiscal_year', 'N/A')} {f['metric']}:** {f['formatted_value']} ({f['currency']}) [Source: {src}]")

    if calculated_metrics:
        context_lines.append("\n### Pre-Calculated Analytics & Composite Scores:")
        for cm in calculated_metrics:
            if cm.get("type") == "best_performing_year_composite":
                context_lines.append(f"- **Best Composite Year:** FY{cm.get('best_composite_year')}")
                context_lines.append(f"- **Highest Revenue:** FY{cm.get('highest_revenue_year')} with {cm.get('highest_revenue_value')}")
                context_lines.append(f"- **Highest Net Income:** FY{cm.get('highest_net_income_year')} with {cm.get('highest_net_income_value')}")
                context_lines.append(f"- **Highest Operating Margin:** FY{cm.get('highest_margin_year')} with {cm.get('highest_margin_value')}")
            elif cm.get("type") == "multi_year_comparison":
                context_lines.append(f"- **FY{cm.get('start_year')} vs FY{cm.get('end_year')}:** Revenue grew from {cm.get('revenue_start')} to {cm.get('revenue_end')}; Net Income from {cm.get('net_income_start')} to {cm.get('net_income_end')}")

    if sources:
        context_lines.append("\n### Verified Filing Citations:")
        seen_urls = set()
        for s in sources:
            url = s.get("url")
            title = s.get("title", "Regulatory Filing")
            if url and url not in seen_urls:
                seen_urls.add(url)
                context_lines.append(f"- [{title}]({url})")

    if relevant_memories:
        context_lines.append("\n### Relevant Conversation & Agent Memories:")
        for m in relevant_memories:
            context_lines.append(f"- {m}")

    # Retrieved documents from FalkorDB (indexed financial statements, overviews, etc.)
    retrieved_docs = retrieved_data.get("documents") or []
    retrieved_docs += retrieved_data.get("exact_year_matches") or []

    context_text = "\n".join(context_lines) if context_lines else ""

    if retrieved_docs:
        doc_lines = ["\n### Retrieved Financial Data (FalkorDB Knowledge Store):"]
        seen_contents: set[str] = set()
        for doc in retrieved_docs[:6]:  # cap at 6 to stay within token budget
            if isinstance(doc, dict):
                content = doc.get("content", "")
            else:
                content = str(doc)
            content = content.strip()
            if not content or content in seen_contents:
                continue
            seen_contents.add(content)
            src = doc.get("source", "") if isinstance(doc, dict) else ""
            period = doc.get("period", "") if isinstance(doc, dict) else ""
            label = f" [{src}" + (f" · {period}" if period else "") + "]" if src else ""
            doc_lines.append(f"- {content[:800]}{label}")
        if len(doc_lines) > 1:
            context_text = (context_text + "\n" + "\n".join(doc_lines)).strip()

    if not context_text:
        context_text = "No historical graph records found for this query."


    # Assemble messages with conversation history for multi-turn awareness
    messages = [SystemMessage(content=system_prompt)]
    for turn in history[-4:]:
        role = turn.get("role", "user")
        content = turn.get("text") or turn.get("content", "")
        if content:
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

    final_user_content = (
        f"Active Ticker: {target_ticker}\n"
        f"Company: {target_company} (`{target_ticker}`) | Exchange: {target_exchange} | Currency: {target_currency}\n"
        f"Question: {question}\n\n"
        f"{context_text}\n\n"
        "Instructions: Answer the user's question directly, citing the verified numbers and source documents above."
    )
    messages.append(HumanMessage(content=final_user_content))

    total_chars = sum(len(m.content) for m in messages)
    return {
        "system_prompt": system_prompt,
        "messages": messages,
        "approx_prompt_tokens": max(1, total_chars // 4),
        "context_package": context_package,
        "is_general_knowledge": False,
        "is_unspecified_company": False,
        "clarification_message": None,
    }
