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


SELECT_SYSTEM = """You help a credit-union assistant pick which website passages answer a member's question.
You get numbered passages taken verbatim from Dupaco Community Credit Union's website (with page type and publish date).
Choose the passage(s) that directly answer the question with CURRENT information:
- prefer official product/info pages over blogs, press releases and calculators, and newer over older;
- never choose a passage that states an outdated value when a newer passage states the current one;
- choose at most 2 passages; choose 2 only if both are needed for a complete answer.
Reply with ONLY the passage numbers separated by commas (e.g. "3" or "1,4"), or NONE if no passage answers the question."""


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
        self.key = os.getenv("LLM_API_KEY") or os.getenv("KB_LLM_API_KEY") or os.getenv("GROQ_API_KEY")
        self.base = os.getenv("KB_LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
        # Free tiers limit tokens per minute *per model*, so we rotate through several.
        self.models = [m.strip() for m in os.getenv(
            "KB_LLM_MODELS", os.getenv("KB_LLM_MODEL", "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b")
        ).split(",") if m.strip()]
        self.timeout = float(os.getenv("KB_LLM_TIMEOUT", "4"))
        self.enabled = bool(self.key) and os.getenv("KB_DISABLE_LLM", "") not in ("1", "true", "yes")
        self._cooldown: dict[str, float] = {}       # model -> time it may be used again
        self._budget: dict[str, tuple[int, float]] = {}   # model -> (tokens left, when that was read)
        self._lock = threading.Lock()
        self.calls = self.failures = 0

    def _pick_model(self, need_tokens: int) -> str | None:
        now = time.time()
        for m in self.models:
            if now < self._cooldown.get(m, 0):
                continue
            left, at = self._budget.get(m, (10 ** 9, now))
            if left < need_tokens and now - at < 60:
                continue
            return m
        return None

    def _chat(self, system: str, user: str, max_tokens: int) -> str | None:
        if not self.enabled:
            return None
        need = (len(system) + len(user)) // 3 + max_tokens
        model = self._pick_model(need)
        if model is None:
            return None
        payload = {"model": model, "temperature": 0, "max_tokens": max_tokens, "seed": 7,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if "gpt-oss" in model:
            payload["reasoning_effort"] = "low"
        req = urllib.request.Request(f"{self.base}/chat/completions", data=json.dumps(payload).encode(), headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
            "User-Agent": "dupaco-knowledge/1.0"})
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                left = resp.headers.get("x-ratelimit-remaining-tokens")
                if left and left.isdigit():
                    self._budget[model] = (int(left), time.time())
                return _clean(json.loads(resp.read())["choices"][0]["message"]["content"] or "")
        except urllib.error.HTTPError as e:
            self.failures += 1
            if e.code == 429:
                retry = e.headers.get("retry-after")
                wait = float(retry) if retry and re.fullmatch(r"[\d.]+", retry) else 20.0
                self._cooldown[model] = time.time() + min(60.0, wait)
            elif e.code in (401, 403):
                self.enabled = False
            elif e.code in (400, 404):
                self._cooldown[model] = float("inf")
        except Exception:
            self.failures += 1
        return None

    def select(self, question: str, passages: list[tuple[str, object]], today) -> list[int] | None:
        """Ask the model which candidate passages answer the question.
        Returns 0-based indices ([] = none answer it), or None when the LLM is unavailable."""
        if not self.enabled or not passages:
            return None
        lines = []
        for i, (text, c) in enumerate(passages, 1):
            when = f", published {c.pub_date.isoformat()}" if c.pub_date else ""
            lines.append(f"[{i}] ({c.kind.name} page{when}; {c.url})\n{text[:500]}")
        user = f"Today is {today.isoformat()}.\nQuestion: {question}\n\nPassages:\n" + "\n\n".join(lines)
        out = self._chat(SELECT_SYSTEM, user, 200)
        if out is None:
            return None
        if re.search(r"\bnone\b", out, re.I) and not re.search(r"\d", out):
            return []
        picks = [int(n) - 1 for n in re.findall(r"\d+", out) if 0 < int(n) <= len(passages)]
        return list(dict.fromkeys(picks))[:2] or None

    def write(self, question: str, chunks, today) -> LLMAnswer | None:
        if not self.enabled:
            return None
        excerpts = []
        for i, c in enumerate(chunks, 1):
            when = f", published {c.pub_date.isoformat()}" if c.pub_date else ""
            excerpts.append(f"[{i}] ({c.kind.name} page{when}) {c.url}\n{c.text[:900]}")
        user = (f"Today is {today.isoformat()}.\n\nExcerpts:\n\n" + "\n\n".join(excerpts) +
                f"\n\nMember question: {question}")
        need = (len(SYSTEM) + len(user)) // 3 + 350
        model = self._pick_model(need)
        if model is None:
            return None
        payload = {"model": model, "temperature": 0, "max_tokens": 350, "seed": 7,
                   "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
        if "gpt-oss" in model:
            payload["reasoning_effort"] = "low"
        body = json.dumps(payload).encode()
        req = urllib.request.Request(f"{self.base}/chat/completions", data=body, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
            "User-Agent": "dupaco-knowledge/1.0"})
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                left = resp.headers.get("x-ratelimit-remaining-tokens")
                if left and left.isdigit():
                    self._budget[model] = (int(left), time.time())
                out = json.loads(resp.read())["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            self.failures += 1
            if e.code == 429:                      # free-tier rate limit: rest this model, fall back now
                retry = e.headers.get("retry-after")
                wait = float(retry) if retry and re.fullmatch(r"[\d.]+", retry) else 20.0
                self._cooldown[model] = time.time() + min(60.0, wait)
            elif e.code in (401, 403):             # bad key: stop trying altogether
                self.enabled = False
            elif e.code in (400, 404):             # model gone / unsupported: drop it
                self._cooldown[model] = float("inf")
            return None
        except Exception:
            self.failures += 1
            return None
        return self._parse(_clean(out), chunks)

    @staticmethod
    def _parse(out: str, chunks) -> LLMAnswer | None:
        if not out:
            return None
        m = re.search(r"\n?\s*SOURCES?:\s*([\d,\s\[\]]*)\s*$", out, re.I)
        nums = [int(n) for n in re.findall(r"\d+", m.group(1))] if m else []
        text = out[: m.start()].strip() if m else out.strip()
        if not text or "NOT_FOUND" in text.upper():
            return LLMAnswer("I'm not able to confirm that from Dupaco's website. Please contact Dupaco directly.",
                             [], confident=False) if "NOT_FOUND" in text.upper() else None
        urls = [chunks[n - 1].url for n in nums if 0 < n <= len(chunks)]
        # Guard against invented numbers: every figure in the answer must appear in an excerpt.
        source = " ".join(c.text for c in chunks)
        src_nums = set(re.findall(r"\d+(?:[.,:]\d+)*", source.replace(",", "")))
        for n in re.findall(r"\d+(?:[.,:]\d+)*", text.replace(",", "")):
            if n not in src_nums and not (len(n) == 4 and n.startswith("20")):
                return None
        return LLMAnswer(text, list(dict.fromkeys(urls)))


def _clean(out: str) -> str:
    """Normalise model output: drop reasoning tags and 【n】 citation marks, plain ASCII dashes/spaces."""
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S)
    out = re.sub(r"【[^】]*】|\[\d+(?:,\s*\d+)*\]", "", out)
    for a, b in (("\u2011", "-"), ("\u2010", "-"), ("\u2013", "-"), ("\u2014", "-"), ("\u202f", " "),
                 ("\u00a0", " "), ("\u2009", " ")):
        out = out.replace(a, b)
    return re.sub(r"[ \t]+", " ", out).strip()
