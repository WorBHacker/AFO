import unittest

from shop import Cart


class ShopTest(unittest.TestCase):
    def test_no_discount(self):
        cart = Cart()
        cart.add("чай", "2.50", 2)
        cart.add("мёд", "7.00")
        self.assertEqual(cart.total(), "12.00")

    def test_ten_per_cent_off(self):
        cart = Cart()
        cart.add("чай", "2.50", 2)
        cart.add("мёд", "7.00")
        self.assertEqual(cart.total(10), "10.80")


if __name__ == "__main__":
    unittest.main()
