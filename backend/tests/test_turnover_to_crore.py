import unittest

from config.targeting import turnover_to_crore


class TurnoverToCroreTests(unittest.TestCase):
    def test_crore_unit_passes_through_unchanged(self):
        self.assertEqual(turnover_to_crore("50 Crore"), 50.0)
        self.assertEqual(turnover_to_crore("12.5 Cr"), 12.5)

    def test_million_is_converted_not_treated_as_crore(self):
        # 1 Crore = 10 Million, so 128 Million = 12.8 Crore - NOT 128.
        self.assertEqual(turnover_to_crore("128 Million"), 12.8)

    def test_lakh_is_converted(self):
        # 1 Crore = 100 Lakh, so 75 Lakh = 0.75 Crore.
        self.assertEqual(turnover_to_crore("75 Lakh"), 0.75)

    def test_billion_is_converted(self):
        # 1 Crore = 0.01 Billion, so 2 Billion = 200 Crore.
        self.assertEqual(turnover_to_crore("2 Billion"), 200.0)

    def test_slab_range_returns_the_lower_bound(self):
        self.assertEqual(turnover_to_crore("5 Cr to 25 Cr"), 5.0)

    def test_currency_prefix_is_ignored(self):
        self.assertEqual(turnover_to_crore("Rs. 12.5 Cr"), 12.5)

    def test_none_and_empty_return_none(self):
        self.assertIsNone(turnover_to_crore(None))
        self.assertIsNone(turnover_to_crore(""))

    def test_text_with_no_recognizable_unit_returns_none(self):
        self.assertIsNone(turnover_to_crore("undisclosed"))
        self.assertIsNone(turnover_to_crore("128"))  # a bare number, no unit at all


if __name__ == "__main__":
    unittest.main()
