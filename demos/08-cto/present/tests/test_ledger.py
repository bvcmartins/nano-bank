import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from ledger import parse_rows, parse_verdict  # noqa: E402

SAMPLE = (
    "14|2026-08-11 17:17:17|cto|rollout_restart|executed|cfo|2026-08-11T17:17:17Z|d1038bc793|c0f50ccd6d\n"
    "15|2026-08-11 21:25:31|cto|rollout_restart|refused|coo|coo is not crashlooping|c0f50ccd6d|e7488dad44\n"
)


def test_parse_rows_splits_fields():
    rows = parse_rows(SAMPLE)
    assert len(rows) == 2
    assert rows[0]["seq"] == "14"
    assert rows[0]["actor"] == "cto"
    assert rows[0]["outcome"] == "executed"
    assert rows[1]["deployment"] == "coo"
    assert rows[1]["detail"].startswith("coo is not crashlooping")


def test_parse_rows_ignores_blank_lines():
    assert parse_rows("\n\n") == []


def test_verdict_empty_is_intact():
    assert parse_verdict("\n") == ("INTACT", None)


def test_verdict_seq_is_tampered():
    assert parse_verdict("7\n") == ("TAMPERED", 7)
