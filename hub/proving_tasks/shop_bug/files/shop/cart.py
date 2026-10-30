from . import discounts
from .money import show, to_cents


class Cart:
    """Lines of (name, price in cents, quantity); the order's discount is taken once, from the whole sum."""

    def __init__(self):
        self.lines: list[tuple[str, int, int]] = []

    def add(self, name: str, price: str, qty: int = 1) -> None:
        self.lines.append((name, to_cents(price), qty))

    def subtotal(self, percent: int = 0) -> int:
        total = 0
        for _name, cents, qty in self.lines:
            line = cents * qty
            if percent:
                line = discounts.apply(line, percent)
            total += line
        return total

    def total(self, percent: int = 0) -> str:
        """The order's sum after the discount, as '12.34'."""
        return show(discounts.apply(self.subtotal(percent), percent))
