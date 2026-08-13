def test_offline_selftest_all_green(tmp_path):
    # Point the workspace at a temp dir so the selftest's disk writes are isolated.
    from coder import coding_agent as ca
    ca.set_workspace(tmp_path)
    assert ca._selftest() is True


def test_public_surface_present():
    from coder import coding_agent as ca
    for name in ("CodingAgent", "CodeResult", "build_agent_graph", "TOOLS_BASE",
                 "lint_python", "write_code_to_disk", "compile_test_suite",
                 "spec_verify", "content_text", "strip_code_fences",
                 "set_workspace", "CODER_SYSTEM_PROMPT", "init_backend"):
        assert hasattr(ca, name), name
