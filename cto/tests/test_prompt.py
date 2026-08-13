from cto.agent import CTO_PROMPT


def test_prompt_mentions_delegation_lever():
    p = CTO_PROMPT.lower()
    assert "delegate_coding_task" in p
    assert "pull request" in p or "pr" in p
    assert "merge" in p            # the human-merge gate is stated
