import unittest

from services.validators import turnover_validator


class TurnoverValidatorTests(unittest.TestCase):
    def test_normalizes_crore_variants(self):
        self.assertEqual(turnover_validator.normalize("Rs. 25 Crores"), "25 Crore")
        self.assertEqual(turnover_validator.normalize("25 Cr"), "25 Crore")
        self.assertEqual(turnover_validator.normalize("₹25 Crore"), "25 Crore")

    def test_normalizes_million_and_billion(self):
        self.assertEqual(turnover_validator.normalize("250 Million"), "250 Million")
        # INR/Rs/₹ are the implicit default currency this app already uses
        # everywhere else, so the prefix is dropped rather than carried through.
        self.assertEqual(turnover_validator.normalize("INR 12 Billion"), "12 Billion")

    def test_normalizes_usd_shorthand(self):
        self.assertEqual(turnover_validator.normalize("USD 5M"), "USD 5 Million")

    def test_falls_back_to_raw_text_when_no_unit_word_is_recognized(self):
        self.assertEqual(turnover_validator.normalize("Above 500"), "Above 500")

    def test_rejects_text_with_no_recognizable_figure(self):
        self.assertIsNone(turnover_validator.normalize("Turnover not disclosed"))
        self.assertIsNone(turnover_validator.normalize(""))
        self.assertIsNone(turnover_validator.normalize(None))


if __name__ == "__main__":
    unittest.main()
