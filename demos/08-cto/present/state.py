"""Pure state helpers for the presentation console: parse the JSONL beat stream,
save/load recordings, and map an outcome kind to a chip style. No Streamlit here
so it stays unit-testable."""
from __future__ import annotations
import glob
import json
import os
from datetime import datetime, timezone


def read_jsonl(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # partial trailing line mid-write
    return out


def save_recording(dir_: str, beats: list[dict], ledger_rows: list[dict],
                   chain: tuple[str, int | None]) -> str:
    os.makedirs(dir_, exist_ok=True)
    # microseconds keep filenames unique even for back-to-back saves.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    path = os.path.join(dir_, f"{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"beats": beats, "ledger_snapshot": ledger_rows,
                   "chain": list(chain), "captured_at": ts}, f, indent=2)
    return path


def load_recording(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def latest_recording(dir_: str) -> str | None:
    files = sorted(glob.glob(os.path.join(dir_, "*.json")), key=os.path.getmtime)
    return files[-1] if files else None


_STYLES = {
    "executed":  ("EXECUTED", "#1a7f37"),
    "refused":   ("REFUSED", "#b35900"),
    "deferred":  ("DEFERRED", "#6639ba"),
    "read_only": ("READ-ONLY", "#57606a"),
}


def outcome_style(kind: str) -> tuple[str, str]:
    return _STYLES.get(kind, (kind.upper(), "#57606a"))
