import pytest

from helper_service.fees import etransfer_fee


@pytest.mark.skip(reason="etransfer_fee not implemented; delivery task implements it")
def test_flat_fee():
    assert etransfer_fee(5000) == 150
