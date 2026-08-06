"""Streamlit console for the Agent COO — a thin wrapper over the shared csuite
console UI. `streamlit run coo/console.py` puts coo/ on sys.path, not the repo
root, so add the root to import csuite."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csuite.console_ui import run_console  # noqa: E402

run_console(
    title="nano-bank — Agent COO",
    page_icon="🏭",
    api_url=os.environ.get("COO_API_URL", "http://localhost:8093"),
    placeholder="Ask the COO about how the bank is running…",
)
