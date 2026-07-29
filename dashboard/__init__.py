"""Streamlit analyst dashboard (app-engineer, CLAUDE.md §5).

Import-light on purpose. `dashboard/datasource.py` and `dashboard/tables.py` never
import streamlit, so they can be exercised by tests and by the API without a
running app; only `components.py`, `app.py` and `pages/` touch streamlit.
"""
