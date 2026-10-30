import unittest

from logparse import count_errors


class CountErrorsTest(unittest.TestCase):
    def test_counts_by_code(self):
        lines = [
            "2026-10-01 12:00:01 ERROR E101 disk full\n",
            "2026-10-01 12:00:02 INFO started\n",
            "2026-10-01 12:00:03 ERROR E101 disk full again\n",
            "2026-10-01 12:00:04 ERROR E7 no network\n",
        ]
        self.assertEqual(count_errors(lines), {"E101": 2, "E7": 1})

    def test_nothing(self):
        self.assertEqual(count_errors([]), {})


if __name__ == "__main__":
    unittest.main()
