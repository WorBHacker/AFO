def to_cents(amount: str) -> int:
    """'12.34' -> 1234; '5' -> 500."""
    whole, _, part = amount.partition(".")
    return int(whole) * 100 + int((part + "00")[:2])


def show(cents: int) -> str:
    """1234 -> '12.34'."""
    return f"{cents // 100}.{cents % 100:02d}"
