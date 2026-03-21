import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import streamlit as st
from evidence_display import render_company_evidence_panel

st.set_page_config(page_title="Evidence Display Test", layout="wide")

ticker = st.sidebar.selectbox("Company", ["NVDA", "JPM", "WMT", "GE", "DG"])
render_company_evidence_panel(ticker)
