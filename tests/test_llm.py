import io
import os
import unittest
import urllib.error
from datetime import date
from types import SimpleNamespace
from unittest import mock

import helpers  # noqa: F401  (sets sys.path)
from kb import llm as L
from kb.pages import KINDS


def chunk(text, url="https://www.dupaco.com/contact"):
    return SimpleNamespace(text=text, url=url, kind=KINDS["info"], pub_date=None)


class FakeResp(io.BytesIO):
    def __init__(self, body: str, headers=None):
        super().__init__(body.encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def reply(content):
    import json
    return FakeResp(json.dumps({"choices": [{"message": {"content": content}}]}),
                    {"x-ratelimit-remaining-tokens": "7000"})


class LLMTest(unittest.TestCase):
    def make(self, **env):
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "test", "KB_DISABLE_LLM": "", **env}, clear=False):
            return L.LLM()

    def test_no_key_means_disabled(self):
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "", "KB_LLM_API_KEY": "", "GROQ_API_KEY": ""}):
            with mock.patch.object(L, "_load_dotenv", lambda: None):
                self.assertFalse(L.LLM().enabled)

    def test_select_parses_numbers_and_none(self):
        m = self.make()
        with mock.patch("urllib.request.urlopen", return_value=reply("2, 1")):
            self.assertEqual(m.select("q", [("a", chunk("a")), ("b", chunk("b"))], date.today()), [1, 0])
        with mock.patch("urllib.request.urlopen", return_value=reply("NONE")):
            self.assertEqual(m.select("q", [("a", chunk("a"))], date.today()), [])

    def test_rate_limit_falls_back_and_rests_model(self):
        m = self.make(KB_LLM_MODELS="model-a")
        err = urllib.error.HTTPError("u", 429, "Too Many", {"retry-after": "30"}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertIsNone(m.select("q", [("a", chunk("a"))], date.today()))
        self.assertIsNone(m._pick_model(10))          # model-a is cooling down -> no call at all

    def test_writer_rejects_invented_numbers(self):
        m = self.make()
        ok = "Call 800-373-7600.\nSOURCES: 1"
        bad = "The fee is $9.99.\nSOURCES: 1"
        c = [chunk("Phone: 800-373-7600")]
        with mock.patch("urllib.request.urlopen", return_value=reply(ok)):
            self.assertIn("800-373-7600", m.write("phone?", c, date.today()).text)
        with mock.patch("urllib.request.urlopen", return_value=reply(bad)):
            self.assertIsNone(m.write("fee?", c, date.today()))

    def test_clean_output(self):
        self.assertEqual(L._clean("Mon‑Fri 8 a.m.–5 p.m.【2】"), "Mon-Fri 8 a.m.-5 p.m.")


if __name__ == "__main__":
    unittest.main()
