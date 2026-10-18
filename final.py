from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
import streamlit as st

from finance_chat_ui import render_standalone_chat_page
from stock_api_ui import render_stock_api_analysis

load_dotenv()

st.set_page_config(
    page_title="FinGraph AI — Connected Financial Intelligence",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Hide the sidebar for the tab-based layout.
st.markdown(
    """
    <style>
    [data-testid="stSidebar"], [data-testid="collapsedControl"] {
        display: none !important;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 1.5rem;
        margin-bottom: 0.5rem;
    }
    .stTabs [data-baseweb="tab"] {
        font-size: 1.1rem;
        font-weight: 600;
        padding: 0.4rem 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Use configured identities for conversation storage.
tenant_id = os.getenv("WORKSPACE_TENANT_ID", "hackathon-demo")
user_id = os.getenv("DEFAULT_USER_ID", "demo-user")


@st.cache_resource(show_spinner=False)
def get_embeddings():
    return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")


embedding_model = get_embeddings()

# Dashboard and chat share the same embedding model.
col_brand, _ = st.columns([4, 6])
with col_brand:
    st.markdown("## 📈 **FinGraph AI** `FalkorDB`")
    st.caption("Connected Financial Intelligence · Graph Reasoning · Source Provenance")

tab_dashboard, tab_chat = st.tabs(["📊 Dashboard", "💬 AI Chat"])

with tab_dashboard:
    render_stock_api_analysis(tenant_id, user_id, "", embedding_model)

with tab_chat:
    render_standalone_chat_page(tenant_id, user_id, embedding_model)
