def apply(cents: int, percent: int) -> int:
    """The sum less `percent` per cent of it; the discount is rounded down to whole cents."""
    return cents - cents * percent // 100
