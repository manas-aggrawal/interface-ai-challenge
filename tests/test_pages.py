import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))
from kb.pages import classify, clean, publication_date  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_kinds(self):
        cases = {
            "https://www.dupaco.com/rates": "rates",
            "https://www.dupaco.com/business-rates": "rates",
            "https://www.dupaco.com/calculators/vehicle-affordability": "calculator",
            "https://www.dupaco.com/newsroom/forbes-ranks-dupaco": "news",
            "https://www.dupaco.com/learn/blog/tag/community": "listing",
            "https://www.dupaco.com/learn/blog/how-to-budget": "blog",
            "https://www.dupaco.com/switch-and-earn": "landing",
            "https://www.dupaco.com/checking-offer-lp": "landing",
            "https://www.dupaco.com/personal/loans/auto-motorcycle-loans": "product",
            "https://www.dupaco.com/contact": "info",
            "https://www.dupaco.com": "info",
        }
        for url, kind in cases.items():
            self.assertEqual(classify(url).name, kind, url)

    def test_authority_order(self):
        self.assertGreater(classify("https://www.dupaco.com/rates").authority,
                           classify("https://www.dupaco.com/calculators/x").authority)


class CleanTest(unittest.TestCase):
    def test_strips_images_links_and_widgets(self):
        md = ("# Title\n\n![pic](https://x/y.png)\n\nSee [our rates](https://www.dupaco.com/rates).\n\n"
              "|   |   |\n| --- | --- |\n|   |   |\n\n## Handpicked for You\n\nOther post")
        out = clean(md, set())
        self.assertIn("See our rates.", out)
        self.assertNotIn("png", out)
        self.assertNotIn("Other post", out)

    def test_publication_date(self):
        md = "2 min read\n\n[Emily Kress](https://x)\nJune 20, 2025\n\n# Dupaco plans Hy-Vee branch closure"
        self.assertEqual(publication_date(md), date(2025, 6, 20))


if __name__ == "__main__":
    unittest.main()
