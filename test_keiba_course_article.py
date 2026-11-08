import unittest
from datetime import date
from keiba_course_article import build_article

class ArticleTest(unittest.TestCase):
    def test_saturday(self):
        title, page = build_article(date(2026, 11, 7))
        self.assertIn('競馬の基礎', title)
        self.assertIn('当日の出走馬', page)
        self.assertIn('font-size:19px', page)
    def test_sunday(self):
        title, page = build_article(date(2026, 11, 8))
        self.assertIn('競馬の基礎', title)
        self.assertIn('<h2>', page)
    def test_weekday_rejected(self):
        with self.assertRaises(ValueError):
            build_article(date(2026, 11, 9))
    def test_deterministic(self):
        self.assertEqual(build_article(date(2026, 11, 8)), build_article(date(2026, 11, 8)))

if __name__ == '__main__':
    unittest.main()
