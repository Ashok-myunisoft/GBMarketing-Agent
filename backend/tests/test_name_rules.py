import unittest

from services.contact_extraction.name_rules import is_person_name, normalize_name


class NameRulesTests(unittest.TestCase):
    def test_accepts_initials_and_prefixes(self):
        for name in (
            "A.K. Sharma",
            "K S Mani",
            "Dr Raj Kumar",
            "John D'Souza",
            "Mary-Anne Thomas",
            "R Srinivasan",
        ):
            self.assertTrue(is_person_name(name), f"expected {name!r} to be accepted")

    def test_rejects_stoplist_and_junk(self):
        for value in ("Butterfly Valves", "Contact Us", "About Us", "Read More", "K M"):
            self.assertFalse(is_person_name(value), f"expected {value!r} to be rejected")

    def test_rejects_single_word_text(self):
        # Regression: a lone short word ("Nff" - an icon-font artifact seen
        # on a live site, or "Login", "Home") is shape-indistinguishable
        # from a genuine single-word name, so it must not pass on its own.
        for value in ("Nff", "Login", "Home", "Products"):
            self.assertFalse(is_person_name(value), f"expected {value!r} to be rejected")

    def test_rejects_empty_and_whitespace(self):
        self.assertFalse(is_person_name(""))
        self.assertFalse(is_person_name("   "))

    def test_normalizes_punctuation_correctly(self):
        self.assertEqual(normalize_name("d'souza john"), "D'Souza John")
        self.assertEqual(normalize_name("a.k. sharma"), "A.K. Sharma")
        self.assertEqual(normalize_name("mary-anne thomas"), "Mary-Anne Thomas")


if __name__ == "__main__":
    unittest.main()
