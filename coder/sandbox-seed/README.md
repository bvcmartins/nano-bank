# cto-sandbox

The sandbox the Agent CTO delegates coding tasks against (Phase C). A tiny Python
helper "service" with two intentional, real, fixable gaps:

  * `helper_service/rounding.py` — `split_amount()` drops the remainder
    (the **remediation** target), guarded by an `xfail(strict=True)` test so the
    baseline suite is green.
  * `helper_service/fees.py` — `etransfer_fee()` is a stub (the **delivery**
    target), guarded by a skipped test.

A delegated PR fixes the code AND removes the marker; the coder's self-verify gate
(this repo's own `pytest`) only goes green when the fix is complete. `main` is the
stable baseline (tag `baseline`).
