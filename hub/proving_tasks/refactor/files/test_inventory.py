import unittest

from inventory import cheapest, item_price, order_total

ITEMS = [{"price": 10.0, "qty": 2}, {"price": 3.35, "qty": 1}, {"price": 0.99, "qty": 5}]


class InventoryTest(unittest.TestCase):
    def test_item_price(self):
        self.assertEqual(item_price({"price": 10.0}), 12.0)

    def test_order_total(self):
        self.assertEqual(order_total(ITEMS), 33.97)

    def test_cheapest(self):
        self.assertEqual(cheapest(ITEMS), 1.19)
        self.assertEqual(cheapest([]), 0.0)


if __name__ == "__main__":
    unittest.main()
