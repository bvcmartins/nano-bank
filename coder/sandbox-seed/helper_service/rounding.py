def split_amount(total_cents: int, n: int) -> list[int]:
    """Split total_cents into n parts. BUG: drops the remainder, so the parts
    don't sum back to total_cents. The remediation task fixes this."""
    each = total_cents // n
    return [each] * n
