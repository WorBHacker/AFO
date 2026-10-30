def item_price(item: dict) -> float:
    """One item's price with VAT."""
    return round(item["price"] * 1.2, 2)


def order_total(items: list[dict]) -> float:
    """The order's total with VAT: each line is quantity times the price with VAT."""
    total = 0.0
    for item in items:
        total += round(item["price"] * 1.2, 2) * item["qty"]
    return round(total, 2)


def cheapest(items: list[dict]) -> float:
    """The lowest price with VAT among the items (0 for none)."""
    prices = [round(item["price"] * 1.2, 2) for item in items]
    return min(prices) if prices else 0.0
