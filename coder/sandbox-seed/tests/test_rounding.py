import pytest

from helper_service.rounding import split_amount


@pytest.mark.xfail(strict=True, reason="known remainder-loss bug; remediation task fixes it")
def test_split_sums_back():
    parts = split_amount(100, 3)
    assert sum(parts) == 100
    assert len(parts) == 3
