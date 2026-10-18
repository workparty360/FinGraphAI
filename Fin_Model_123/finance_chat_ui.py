from __future__ import annotations

import hashlib
import os

from langchain_groq import ChatGroq
from langchain_text_splitters import RecursiveCharacterTextSplitter
import streamlit as st

from falkor_memory import FalkorMemory
from memory_agents import FinanceAgentTeam


@st.cache_resource(show_spinner=False)
def memory_for(workspace: str, user: str, _embedding_model):
    return FalkorMemory(workspace, user, _embedding_model)


def render_chat_navigation(workspace: str, user: str, embedding_model) -> str:
    """Compact inline chat thread management — 100% full width, zero sidebar usage.

    Preserves:
    - New Chat
    - Switch Chat (dropdown)
    - Rename Chat (popover)
    - Delete Chat
    - Full persistence via FalkorDB
    """
    try:
        memory = memory_for(workspace, user, embedding_model)
    except Exception:
        memory = None

    # Load persisted threads
    threads = []
    if memory:
        try:
            threads = memory.list_threads()
        except Exception:
            threads = []

    # Ensure an active thread exists in session state
    if "active_thread_id" not in st.session_state:
        if threads:
            st.session_state.active_thread_id = threads[0]["id"]
        else:
            new_th = memory.create_thread("New Chat") if memory else {"id": "main", "title": "New Chat"}
            st.session_state.active_thread_id = new_th["id"]
            threads = [new_th]

    active_id = st.session_state.active_thread_id

    known_ids = {t["id"] for t in threads}
    if active_id not in known_ids:
        threads.insert(0, {"id": active_id, "title": "Active Chat"})

    thread_titles = {t["id"]: (t.get("title") or "New Chat") for t in threads}
    options = [t["id"] for t in threads]

    with st.container():
        col_lbl, col_sel, col_new, col_ren, col_del = st.columns([1.5, 4.5, 1.8, 1.1, 1.1])
        with col_lbl:
            st.markdown("##### 💬 Chat")
        with col_sel:
            curr_idx = options.index(active_id) if active_id in options else 0
            selected = st.selectbox(
                "Conversation",
                options=options,
                format_func=lambda tid: f"{thread_titles.get(tid, tid)}",
                index=curr_idx,
                key="chat_thread_selector",
                label_visibility="collapsed",
            )
            if selected != active_id:
                st.session_state.active_thread_id = selected
                # Restore thread-specific ticker into session state if available
                if memory:
                    try:
                        th_ticker = memory.get_thread_ticker(selected)
                        if th_ticker:
                            from stock_api import get_selected_stock_context
                            st.session_state["selected_stock"] = get_selected_stock_context(th_ticker)
                            st.session_state["_ticker_sync"] = th_ticker
                        else:
                            st.session_state["selected_stock"] = None
                            st.session_state["_ticker_sync"] = ""
                    except Exception:
                        st.session_state["selected_stock"] = None
                else:
                    st.session_state["selected_stock"] = None
                st.session_state.pop("chat_messages", None)
                st.session_state.pop("conversation_ticker", None)
                st.rerun()

        with col_new:
            if st.button("➕ New Chat", use_container_width=True, type="primary", key="btn_new_chat"):
                if memory:
                    new_th = memory.create_thread("New Chat", ticker="")
                    st.session_state.active_thread_id = new_th["id"]
                else:
                    from uuid import uuid4
                    st.session_state.active_thread_id = uuid4().hex
                # Clean slate for fresh conversation
                st.session_state["selected_stock"] = None
                st.session_state["_ticker_sync"] = ""
                st.session_state.pop("chat_messages", None)
                st.session_state.pop("conversation_ticker", None)
                st.rerun()

        with col_ren:
            active_title = thread_titles.get(active_id, "New Chat")
            with st.popover("✏️", help="Rename conversation"):
                st.caption("Rename current chat")
                new_title = st.text_input("Title", value=active_title, key=f"rename_input_{active_id}")
                if st.button("Save", key=f"save_rename_{active_id}", use_container_width=True):
                    if memory and new_title.strip():
                        memory.rename_thread(active_id, new_title.strip())
                        st.rerun()

        with col_del:
            if st.button("🗑️", help="Delete conversation", key=f"del_btn_{active_id}"):
                if memory:
                    memory.delete_thread(active_id)
                remaining = [t for t in threads if t["id"] != active_id]
                if remaining:
                    st.session_state.active_thread_id = remaining[0]["id"]
                else:
                    new_th = memory.create_thread("New Chat") if memory else {"id": "main", "title": "New Chat"}
                    st.session_state.active_thread_id = new_th["id"]
                st.rerun()

    return st.session_state.active_thread_id


def render_chat_sidebar(workspace: str, user: str, embedding_model) -> str:
    """Backward compatibility alias: delegates to render_chat_navigation with NO sidebar."""
    return render_chat_navigation(workspace, user, embedding_model)


def render_agent_chat(
    workspace: str,
    user: str,
    thread: str,
    stock_context: dict | str | None,
    embedding_model,
    *,
    documents=None,
):
    """Render conversation interface for the active thread with shared FalkorDB memory access."""
    # Normalize stock_context and ticker
    if isinstance(stock_context, str):
        ticker = stock_context.strip().upper()
        ctx = {
            "symbol": ticker,
            "companyName": ticker,
            "currency": "INR" if ticker.endswith((".NS", ".BO")) else "USD",
            "exchange": "NSE" if ticker.endswith(".NS") else ("BSE" if ticker.endswith(".BO") else "US"),
        } if ticker else None
    elif isinstance(stock_context, dict):
        ctx = stock_context
        ticker = ctx.get("symbol", "").strip().upper()
    else:
        # Fallback to active stock selected on Dashboard tab
        ctx = st.session_state.get("selected_stock")
        if ctx and isinstance(ctx, dict):
            ticker = ctx.get("symbol", "").strip().upper()
        elif st.session_state.get("central_ticker_input"):
            raw_input = st.session_state["central_ticker_input"].strip()
            if raw_input:
                try:
                    from stock_api import get_selected_stock_context
                    ctx = get_selected_stock_context(raw_input)
                    ticker = ctx.get("symbol", "").strip().upper() if ctx else ""
                except Exception:
                    ctx = None
                    ticker = ""
            else:
                ctx = None
                ticker = ""
        else:
            ctx = None
            ticker = ""

    try:
        memory = memory_for(workspace, user, embedding_model)
    except Exception as exc:
        st.error(f"Unable to connect to FalkorDB: {exc}")
        st.code("docker compose up -d")
        return

    # Index documents into shared FalkorDB store if provided
    if documents and ticker:
        docs = []
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=70)
        for source_doc in documents:
            if source_doc.metadata.get("source", "").startswith(("financial_", "sec_")):
                docs.append(source_doc)
            else:
                docs.extend(splitter.split_documents([source_doc]))

        fingerprint = hashlib.sha256(
            "\n".join(doc.page_content + str(doc.metadata) for doc in docs).encode("utf-8")
        ).hexdigest()
        index_key = (workspace, user, ticker, fingerprint)

        if st.session_state.get("indexed_financial_docs") != index_key:
            with st.spinner("Indexing financial data in FalkorDB..."):
                try:
                    for doc in docs:
                        memory.upsert_document(
                            doc.page_content,
                            ticker=ticker,
                            source=doc.metadata.get("source", "finance"),
                            period=doc.metadata.get("period", ""),
                            thread_id=thread,
                        )
                    st.session_state.indexed_financial_docs = index_key
                except Exception as exc:
                    st.error(f"Document indexing failed: {exc}")
                    return

    st.subheader("Ask the finance agent team")
    if ctx and ticker:
        comp = ctx.get("companyName", ticker)
        curr = ctx.get("currency", "")
        exch = ctx.get("exchange", "")
        st.caption(f"Active stock context: **{comp}** (`{ticker}`) · {exch} · {curr}")
    else:
        st.caption("Active stock context: *None (select a stock above or mention a company name in your query)*")

    # Render isolated conversation history for the ACTIVE THREAD ONLY
    try:
        messages = memory.recent_turns(thread, limit=20)
        for message in messages:
            role = "user" if message["role"] == "user" else "assistant"
            with st.chat_message(role):
                st.markdown(message["content"])
    except Exception as exc:
        st.warning(f"Unable to load previous conversations: {exc}")

    question = st.chat_input("Ask about your financials or earlier sessions...", key=f"chat_input_{thread}")
    if not question:
        return

    with st.chat_message("user"):
        st.markdown(question)

    with st.spinner("Finance agents are coordinating..."):
        try:
            try:
                from api_key_manager import get_key_manager
                llm, _ = get_key_manager().make_llm()
            except Exception:
                llm = ChatGroq(model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
            result = FinanceAgentTeam(memory, llm).ask(
                question, ticker=ticker, stock_context=ctx, thread_id=thread
            )

            # Auto-title thread on first message and save thread ticker
            if memory:
                try:
                    threads = memory.list_threads()
                    cur_th = next((t for t in threads if t["id"] == thread), None)
                    if cur_th and (cur_th.get("title") in ("New Chat", "", "Active Chat")):
                        memory.auto_title_thread(thread, question)
                    if result.get("ticker"):
                        memory.set_thread_ticker(thread, result["ticker"])
                except Exception:
                    pass

            with st.chat_message("assistant"):
                st.markdown(result["answer"])
            st.caption(
                f"FalkorDB saved conversation, episodic memory, handoffs and "
                f"{result.get('saved_memories', 0)} semantic/procedural memories."
            )
        except Exception as exc:
            from api_key_manager import OrganizationQuotaExhaustedError
            if isinstance(exc, OrganizationQuotaExhaustedError) or "OrganizationQuotaExhaustedError" in type(exc).__name__:
                mins = max(1, int(getattr(exc, "retry_after_secs", 1800) / 60))
                st.warning(
                    f"⚠️ **AI Token Quota Exceeded (TPD)**: Groq inference has reached its daily limit for your organization. "
                    f"Estimated reset in approximately **{mins} minutes**. Your chat history and verified FalkorDB records are preserved."
                )
            else:
                st.error(f"Agent workflow failed: {exc}")


def render_standalone_chat_page(workspace: str, user: str, embedding_model) -> None:
    """Dedicated AI Chat page — completely independent of the financial dashboard."""
    st.subheader("💬 AI Financial Analyst")
    st.caption("Ask financial questions, analyze companies, or inspect historical trends.")

    # Top conversation selector bar
    active_thread_id = render_chat_navigation(workspace, user, embedding_model)
    st.divider()

    try:
        memory = memory_for(workspace, user, embedding_model)
        th_ticker = memory.get_thread_ticker(active_thread_id)
    except Exception:
        th_ticker = ""

    selected_stock = None
    if th_ticker:
        try:
            from stock_api import get_selected_stock_context
            selected_stock = get_selected_stock_context(th_ticker)
        except Exception:
            selected_stock = {
                "symbol": th_ticker,
                "companyName": th_ticker,
                "currency": "INR" if th_ticker.endswith((".NS", ".BO")) else "USD",
                "exchange": "NSE" if th_ticker.endswith(".NS") else ("BSE" if th_ticker.endswith(".BO") else "US"),
            }
    elif st.session_state.get("selected_stock"):
        selected_stock = st.session_state["selected_stock"]
    elif st.session_state.get("central_ticker_input"):
        raw = st.session_state["central_ticker_input"].strip()
        if raw:
            try:
                from stock_api import get_selected_stock_context
                selected_stock = get_selected_stock_context(raw)
            except Exception:
                selected_stock = None

    render_agent_chat(
        workspace,
        user,
        active_thread_id,
        selected_stock,
        embedding_model,
    )

