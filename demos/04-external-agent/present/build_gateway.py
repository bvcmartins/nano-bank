#!/usr/bin/env python3
"""Build the self-contained animated gateway page: inline the canonical
recording's events into gateway.template.html and write gateway.html.

    python demos/04-external-agent/present/build_gateway.py
"""
from __future__ import annotations
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "gateway.template.html")
OUT = os.path.join(HERE, "gateway.html")
REC = os.path.join(HERE, "recordings", "canonical.json")
MARKER = "/*__EVENTS__*/"


def _load() -> list:
    if not os.path.exists(REC):
        return []
    with open(REC, encoding="utf-8") as f:
        return json.load(f).get("events", [])


def render(events: list) -> str:
    """Fill the template with the given events and return the HTML string.
    Pure -- no file writes. Shared by build() (writes gateway.html) and
    app.py (embeds the result live via st.components.v1.html)."""
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(events, ensure_ascii=False)
    idx = html.index(MARKER)
    end = html.index(";", idx)
    return html[:idx] + payload + html[end:]


def build() -> str:
    html = render(_load())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    return OUT


if __name__ == "__main__":
    out = build()
    events = json.loads(open(out, encoding="utf-8").read()
                        .split("const EVENTS = ", 1)[1].split(";\n", 1)[0])
    print(f"  {len(events)} events")
    print("wrote", out)
