from langchain_core.messages import HumanMessage, AIMessage
from csuite.harness.context import estimate_tokens, compact


def _big(n):
    return [HumanMessage("x" * 4000) if i % 2 == 0 else AIMessage("y" * 4000)
            for i in range(n)]


def test_below_threshold_is_untouched():
    msgs = [HumanMessage("hi"), AIMessage("hello")]
    res = compact(msgs, threshold=10_000, summarize_fn=lambda m: "S", keep_last=6)
    assert res.compacted is False
    assert res.kept == msgs and res.dropped == []


def test_over_threshold_summarizes_all_but_last_k():
    msgs = _big(20)
    res = compact(msgs, threshold=5_000, summarize_fn=lambda m: "ROLLED", keep_last=6)
    assert res.compacted is True
    assert res.summary == "ROLLED"
    assert len(res.kept) == 6                       # only the tail kept
    assert len(res.dropped) == 14                   # the rest summarized away
    assert estimate_tokens(msgs) > 5_000
