from cfo.trace import TraceRecorder


def test_tool_output_is_stored_untruncated():
    """The verifier reads tool outputs to build its grounded set; a truncated
    financial_health bundle would drop numbers and cause false 'ungrounded'."""
    rec = TraceRecorder()
    big = "x" * 5000 + "END_MARKER"
    rec.on_tool_start({"name": "financial_health"}, "{}", run_id="r1")
    rec.on_tool_end(big, run_id="r1")
    ev = rec.events()[0]
    assert ev["kind"] == "tool"
    assert ev["output"].endswith("END_MARKER")
    assert len(ev["output"]) >= 5000


def test_tool_input_is_still_bounded():
    """Only the output cap is lifted; the input field keeps its short cap."""
    rec = TraceRecorder()
    rec.on_tool_start({"name": "raroc"}, "y" * 5000, run_id="r2")
    rec.on_tool_end("ok", run_id="r2")
    ev = rec.events()[0]
    assert len(ev["input"]) <= 2100  # 2000 + ellipsis slack
