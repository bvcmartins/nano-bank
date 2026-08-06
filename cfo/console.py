"""Streamlit console for the Agent CFO — a thin wrapper over the shared csuite
console UI. `streamlit run cfo/console.py` puts cfo/ on sys.path, not the repo
root, so add the root to import csuite."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csuite.console_ui import run_console  # noqa: E402

run_console(
    title="nano-bank — Agent CFO",
    page_icon="📊",
    api_url=os.environ.get("CFO_API_URL", "http://localhost:8089"),
    placeholder="Ask the CFO about the bank's finances…",
)
