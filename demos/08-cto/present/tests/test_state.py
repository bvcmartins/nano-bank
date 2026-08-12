import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from state import (read_jsonl, save_recording, load_recording,  # noqa: E402
                   latest_recording, outcome_style)


def test_read_jsonl_skips_partial_trailing_line():
    text = '{"beat":1}\n{"beat":2}\n{"beat":3'  # last line half-written
    beats = read_jsonl(text)
    assert [b["beat"] for b in beats] == [1, 2]


def test_recording_round_trip(tmp_path):
    p = save_recording(str(tmp_path), beats=[{"beat": 1}],
                       ledger_rows=[{"seq": "1"}], chain=("INTACT", None))
    rec = load_recording(p)
    assert rec["beats"] == [{"beat": 1}]
    assert rec["ledger_snapshot"] == [{"seq": "1"}]
    assert rec["chain"] == ["INTACT", None]  # json turns the tuple into a list


def test_latest_recording_picks_newest(tmp_path):
    a = save_recording(str(tmp_path), [{"beat": 1}], [], ("INTACT", None))
    import time; time.sleep(0.01)
    b = save_recording(str(tmp_path), [{"beat": 2}], [], ("INTACT", None))
    assert latest_recording(str(tmp_path)) == b
    assert a != b


def test_latest_recording_none_when_empty(tmp_path):
    assert latest_recording(str(tmp_path)) is None


def test_outcome_style_labels():
    assert outcome_style("executed")[0] == "EXECUTED"
    assert outcome_style("refused")[0] == "REFUSED"
    assert outcome_style("read_only")[0] == "READ-ONLY"
    assert outcome_style("deferred")[0] == "DEFERRED"
