import json, os, sys

# demos/_driver.py lives three levels up from present/tests/ (present -> 08-cto -> demos)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from _driver import _append_jsonl  # noqa: E402


def test_append_writes_one_json_object_per_line(tmp_path):
    p = tmp_path / "run.jsonl"
    _append_jsonl(str(p), {"beat": 1, "title": "a"})
    _append_jsonl(str(p), {"beat": 2, "title": "b"})
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["beat"] == 1
    assert json.loads(lines[1])["title"] == "b"
