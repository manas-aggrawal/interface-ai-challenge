import unittest

from helpers import snapshots
from kb.facts import FactStore, parse_amount, parse_model_years, parse_term
from kb.store import Store


class ParseTest(unittest.TestCase):
    def test_qualifiers(self):
        self.assertEqual(parse_term("2024-27 Model Year | 60 Months"), 60)
        self.assertEqual(parse_term("Loan – Standard (120 mo)"), 120)
        self.assertEqual(parse_term("Purchase: 30-Year Fixed Conventional"), 360)
        self.assertEqual(parse_model_years("2024-27 Model Year"), (2024, 2027))
        self.assertEqual(parse_model_years("Model Year 2026-23"), (2023, 2026))
        self.assertEqual(parse_model_years("2016 Model Year (and older)"), (1900, 2016))
        self.assertEqual(parse_amount("$10,000 – $24,999.99"), (10000, 24999))
        self.assertEqual(parse_amount("$25,000+"), (25000, float("inf")))
        self.assertEqual(parse_amount("30-Year Fixed Conventional $100,000 or less"), (0, 100000))


class FactStoreTest(unittest.TestCase):
    def test_history_and_kinds(self):
        st, fs = Store(), FactStore()
        for s in snapshots():
            st.add_snapshot(s)
            for _, v in st.pages_in(st.latest):
                fs.add_version(v, st.latest)
        jumbo = [r for r in fs.rows if r.jumbo and r.family == "certificate"]
        self.assertEqual({(r.values["apy"], tuple(sorted(r.snaps))) for r in jumbo}, {("3.90%", (0,)), ("4.00%", (1,))})
        calc = [r for r in fs.rows if r.kind.name == "calculator"]
        self.assertEqual(calc[0].values, {"apr": "5.34%"})
        self.assertEqual(calc[0].snaps, {0, 1})
        self.assertEqual(calc[0].family, "auto")


if __name__ == "__main__":
    unittest.main()
