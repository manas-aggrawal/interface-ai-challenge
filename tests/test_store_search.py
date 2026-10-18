import unittest

from helpers import snapshots
from kb.search import BM25, tokenize
from kb.store import Store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.st = Store()
        for s in snapshots():
            self.st.add_snapshot(s)

    def test_unchanged_text_stored_once(self):
        contact = [c for c in self.st.chunks if c.url.endswith("/contact")]
        self.assertEqual(len(contact), 1)
        self.assertEqual(contact[0].snaps, {0, 1})

    def test_changed_page_keeps_both_versions(self):
        self.assertEqual(len(self.st.versions["https://www.dupaco.com/rates"]), 2)

    def test_snapshot_for_dates(self):
        self.assertEqual(self.st.snapshot_for(None), 1)
        self.assertEqual(self.st.snapshot_for("2026-05-31"), 0)
        self.assertEqual(self.st.snapshot_for("2026-10-01"), 1)
        self.assertEqual(self.st.snapshot_for("2025-01-01"), 0)

    def test_table_title_kept(self):
        tables = [c for c in self.st.chunks if c.block.kind == "table" and "Jumbo" in c.text]
        self.assertTrue(all("Special Certificates" in c.text for c in tables))


class SearchTest(unittest.TestCase):
    def test_tokenize_numbers(self):
        self.assertEqual(tokenize("$10,000 at 5.39% for 60-month"), ["10000", "5.39", "60", "month"])

    def test_snapshot_filter_and_synonyms(self):
        st = Store()
        for s in snapshots():
            st.add_snapshot(s)
        bm = BM25()
        for c in st.chunks:
            bm.add(c.id, c.text)
        hits = bm.search("routing number", limit=1)
        self.assertIn("273974581", st.chunks[hits[0][0]].text)
        old = bm.search("jumbo special", allowed=lambda d: 0 in st.chunks[d].snaps)
        self.assertTrue(all(0 in st.chunks[d].snaps for d, _ in old))
        self.assertTrue(bm.search("CD rates"))   # "CD" expands to "certificate"


if __name__ == "__main__":
    unittest.main()
