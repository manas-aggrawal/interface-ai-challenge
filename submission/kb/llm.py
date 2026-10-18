"""Optional LLM answer writer (any OpenAI-compatible chat API; default: Groq's free tier).

Used only for free-text questions, with the passages our retriever already picked (right date,
trusted pages first). Anything going wrong - no key, rate limit, timeout, odd output - returns None
and the caller uses the extractive answer instead, so the system never depends on the LLM.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SYSTEM = """You answer questions from members of Dupaco Community Credit Union using ONLY the website excerpts provided.
Rules:
- Use only facts stated in the excerpts. Never use outside knowledge or guess.
- Give the CURRENT information. Excerpts are listed most-trusted first; official product/info pages beat blogs, press releases and calculators, and newer beats older. If excerpts disagree, state only the value you judge current - never list the old value too.
- Be brief: 1-3 sentences, plain text, member-friendly. Include exact names, numbers, addresses, URLs and dates when they answer the question.
- If the excerpts do not answer the question, reply exactly: NOT_FOUND
- End with a line: SOURCES: <comma-separated excerpt numbers you used>"""


@dataclass
class LLMAnswer:
    text: str
    urls: list[str]
    confident: bool = True


def _load_dotenv() -> None:
    """Read KEY=VALUE lines from a .env next to the repo root (no python-dotenv dependency)."""
    for d in Path(__file__).resolve().parents:
        f = d / ".env"
        if f.is_file():
            for line in f.read_text().splitlines():
                m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
                if m and m.group(1) not in os.environ:
                    os.environ[m.group(1)] = m.group(2).strip().strip("'\"")
            return


class LLM:
    def __init__(self):
        _load_dotenv()
        self.key = os.getenv("KB_LLM_API_KEY") or os.getenv("GROQ_API_KEY")
        self.base = os.getenv("KB_LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
        self.model = os.getenv("KB_LLM_MODEL", "llama-3.1-8b-instant")
        self.timeout = float(os.getenv("KB_LLM_TIMEOUT", "4"))
        self.enabled = bool(self.key) and os.getenv("KB_DISABLE_LLM", "") not in ("1", "true", "yes")
        self._cooldown_until = 0.0
        self._lock = threading.Lock()
        self.calls = self.failures = 0

    def write(self, question: str, chunks, today) -> LLMAnswer | None:
        if not self.enabled or time.time() < self._cooldown_until:
            return None
        excerpts = []
        for i, c in enumerate(chunks, 1):
            when = f", published {c.pub_date.isoformat()}" if c.pub_date else ""
            excerpts.append(f"[{i}] ({c.kind.name} page{when}) {c.url}\n{c.text[:1400]}")
        user = (f"Today is {today.isoformat()}.\n\nExcerpts:\n\n" + "\n\n".join(excerpts) +
                f"\n\nMember question: {question}")
        body = json.dumps({
            "model": self.model, "temperature": 0, "max_tokens": 300, "seed": 7,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(f"{self.base}/chat/completions", data=body, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
            "User-Agent": "dupaco-knowledge/1.0"})
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out = json.loads(resp.read())["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            self.failures += 1
            if e.code == 429:                      # free-tier rate limit: back off, use fallback meanwhile
                retry = e.headers.get("retry-after")
                self._cooldown_until = time.time() + min(60.0, float(retry) if retry and retry.replace(".", "").isdigit() else 20.0)
            elif e.code in (401, 403, 404):        # bad key / model: stop trying
                self.enabled = False
            return None
        except Exception:
            self.failures += 1
            return None
        return self._parse(out, chunks)

    @staticmethod
    def _parse(out: str, chunks) -> LLMAnswer | None:
        m = re.search(r"\n?\s*SOURCES?:\s*([\d,\s\[\]]*)\s*$", out, re.I)
        nums = [int(n) for n in re.findall(r"\d+", m.group(1))] if m else []
        text = out[: m.start()].strip() if m else out.strip()
        if not text or "NOT_FOUND" in text.upper():
            return LLMAnswer("I'm not able to confirm that from Dupaco's website. Please contact Dupaco directly.",
                             [], confident=False) if "NOT_FOUND" in text.upper() else None
        urls = [chunks[n - 1].url for n in nums if 0 < n <= len(chunks)]
        return LLMAnswer(text, list(dict.fromkeys(urls)))
