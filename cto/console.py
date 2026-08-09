"""Streamlit console for the Agent CTO — a thin wrapper over the shared csuite
console UI. `streamlit run cto/console.py` puts cto/ on sys.path, not the repo
root, so add the root to import csuite."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csuite.console_ui import run_console  # noqa: E402

run_console(
    title="nano-bank — Agent CTO",
    page_icon="🛠️",
    api_url=os.environ.get("CTO_API_URL", "http://localhost:8095"),
    placeholder="Ask the CTO about the platform — reliability and delivery…",
)
