import unittest

import runtime_catalog_safety as catalog


class TestCatalogSafety(unittest.TestCase):
    def test_invisible_title_and_promo_author_are_rejected(self):
        bad = {
            "title": "\u200c",
            "author": "Слушайте аудиокниги на канале @audioknigi_channel",
        }
        self.assertFalse(catalog.is_valid_book_record(bad))
        self.assertEqual(catalog._visible_text(bad["title"]), "")

    def test_promo_author_is_rejected_even_with_visible_title(self):
        bad = {
            "title": "Нормальное название",
            "author": "Слушайте аудиокниги на канале @audioknigi_channel",
        }
        self.assertFalse(catalog.is_valid_book_record(bad))

    def test_normal_book_is_kept(self):
        good = {"title": "Экспедиция надежды", "author": "Хавьер Моро"}
        self.assertTrue(catalog.is_valid_book_record(good))


if __name__ == "__main__":
    unittest.main()
