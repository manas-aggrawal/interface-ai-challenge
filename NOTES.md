# NOTES — how this knowledge system works

Submission folder: **`submission/`** (`solution.py` → `build()`).

```bash
pip install -r requirements.txt
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
```

| Run (this repo, Apple M-series laptop) | Accuracy | p95 latency | Ingest |
|---|---|---|---|
| Public eval (40 q) | 0.964 (the only miss, `dup-005`, has an empty `must_include` and can't score without the LLM judge) | 0.04 s (no LLM) / ~0.7 s (LLM) | ~4 s |
| Our dev eval (`devtools/dev_eval.jsonl`, 40 reworded / as-of / refusal q) | 1.0 | same | same |
| Kit selftest (`harness/selftest`) | 1.0, conflicts F1 1.0 | | |

## Try it yourself

```bash
python devtools/web.py      # local web page at http://127.0.0.1:8765 (ask, pick an "as of" date, see conflicts)
python devtools/chat.py     # terminal chat; prefix "@2026-05-31 " for as-of questions, ":conflicts" to list them
```

## API keys

**None are required.** The system runs fully offline and scores the same on our evals without a key.

Optional: a free [Groq](https://console.groq.com) key in the environment or in a `.env` file at the repo root
(`LLM_API_KEY=gsk_...`, see `.env.example`). With it, a free LLM (`openai/gpt-oss-120b`,
`openai/gpt-oss-20b`, `qwen/qwen3.8-27b` on Groq) acts as a *judge* for free-text questions (details below).
The free tier allows ~8k tokens/min per model, so we rotate models, read the rate-limit headers, and skip the
LLM instantly (no waiting) when the budget is used up. Any failure falls back to the offline path.

Other optional env vars: `KB_DISABLE_LLM=1`, `KB_LLM_MODELS` (comma list), `KB_LLM_BASE_URL` (any
OpenAI-compatible endpoint), `KB_LLM_TIMEOUT` (default 4 s), `KB_DISABLE_EMBEDDINGS=1`, `KB_EMBED_MODEL`.

The embedding model (`minishlab/potion-base-8M`, ~30 MB) is downloaded from Hugging Face on first run; if that
fails the system silently uses keyword search only.

## How it works

**Ingest (once per snapshot, oldest first).** Every page is cleaned (images, reCAPTCHA leftovers,
"Handpicked for You" widgets removed) and classified by URL into a page kind with a trust score:
`/rates` rate sheet 1.0 > product pages 0.85 > info pages 0.8 > guides 0.5 > landing pages 0.35 >
calculators / PDFs 0.3 > blog / news 0.25. Nothing is overwritten: each unique chunk of text and each rate-table
row is stored once with the set of snapshots it appeared in, so "as of" questions and history are free.

* **Facts table** — every rate-table row becomes a fact with its qualifiers: product family, term,
  model-year range, balance / loan-amount range, jumbo / special, business vs personal, measure
  (dividend rate / APY / APR / rate), the table's "effective as of" date, page kind and snapshots seen.
  Untitled tables inherit the product of the table before them; plain-text rates are used only where a
  product has no table.
* **Search index** — ~10k unique chunks (tables kept whole with their titles) in a BM25 index plus static
  embeddings (model2vec, ~22k chunks/s on CPU), fused with Reciprocal Rank Fusion.

**Answer.** The question is parsed with rules (fast and repeatable): intent, product, tier qualifiers,
superlatives, and dates in the wording. `as_of` (or "as of May 2026" in the text) selects the snapshot that
was live on that date; otherwise the newest.

* *Rate questions* → facts of that product in that snapshot → **the single most trusted page** (rate sheet, then
  product page, then newest effective date) → narrow to the asked tier → state only those values, cite the page,
  and note in `conflict_note` which stale values elsewhere were ignored. Asking for a tier that doesn't exist
  ("60-month jumbo", "84-month on a 2016 car") or an unlisted product (ARM, construction, FHA) → "not listed".
* *Everything else* → hybrid search → sentences scored by question-word overlap, embedding similarity, page
  trust, recency (news/blog) and search rank; FAQ questions contribute their answer line, headings keep their
  bullet list. Answers are **verbatim website text** (exact values survive), from a single article when it is
  news. Special handlers: hours (distinct lobby / drive-up schedules), branch addresses (branch locator),
  counts, links.
* *LLM judge (optional)* — picks which candidate passages actually answer; its picks go first, the top extractive
  passages fill in. If it says none answer, we refuse. We measured letting the LLM *write* the answer: it scored
  worse (0.964 → 0.879) because paraphrasing drops exact values, so it never writes. Any number not present in
  the excerpts is rejected.
* *Refusals* contain no numbers: "I'm not able to confirm that from Dupaco's website…".
* Every answer is cached per (question, snapshot), so asking twice gives the same answer.

## How we decide what is current (the rules)

1. **Snapshot first.** Only the snapshot live on the question's date counts (newest by default).
2. **Rate sheet beats everything.** Within that snapshot: `/rates` > product page > guide > landing page >
   calculator > PDF > blog / news. A calculator or promo page that still shows an old number never wins.
3. **Newest effective date** breaks ties between equally trusted pages.
4. **Tiers are different facts.** 48 vs 60 months, 2021-23 vs 2024-27 model years, balance tiers, jumbo vs
   standard, rate vs APY vs APR, personal vs business — never compared, never conflicts.
5. **Model-year buckets roll over** (2023-26 → 2024-27); a vehicle year is mapped into the bucket that
   contains it, and for conflicts buckets are compared by position (newest bucket vs newest bucket).
6. **Ended promotions.** A product that dropped off the rate sheet and survives only on landing pages is treated
   as ended; we don't quote its number. Text offers marked "ended"/"expired" are never current.
7. **Press releases age.** Yearly releases re-published as `/slug-2`, `/slug-3` — only the newest edition counts
   ("the fifth time Forbes recognized Dupaco"). Undated current-state claims that drift (branch counts in the
   "About Dupaco" paragraph: 19 → 24 → 22) are taken from the most recently published page. Dated statistics
   ("294 employees used VTO in 2023", "256 in 2024") are a time series and reported by year.
8. **"When" questions need a date** in the answer; headlines and photo captions are never answers.
9. **Missing ≠ changed.** A page absent from the newest crawl doesn't erase its facts; if nothing current
   answers, we use the latest earlier copy and say when it was last seen.
10. **Don't guess.** No matching fact, no relevant passage, an unknown branch or entity → say it isn't listed.

## conflicts()

Facts are grouped by product + tier + business flag + measure; any group with more than one value across
pages and snapshots is a contradiction. The winner follows the rules above (newest snapshot → most trusted
page → newest effective date), with a reason such as *"still contradicted on the live site: 5.39% comes from the
rates page … (effective 2026-08-31); 5.34% on /calculators/vehicle-affordability is a calculator page that was
not updated"*. Dividend-rate twins of APY conflicts and identical business / IRA copies are merged to keep the
list precise (F1 is scored); contradictions still visible in the newest snapshot are listed first. Branch-count
drift in press releases is reported as well.

## Layout

```
submission/solution.py      build(), routing, special handlers
submission/kb/pages.py      cleaning, page kinds / trust, publication dates
submission/kb/chunks.py     table-aware chunking
submission/kb/store.py      snapshot history, dedup, as-of resolution
submission/kb/search.py     BM25 + synonyms + stemming
submission/kb/vectors.py    static embeddings, RRF
submission/kb/retrieve.py   hybrid retrieval, newest press-release edition
submission/kb/facts.py      rate facts with qualifiers and history
submission/kb/query.py      question parsing
submission/kb/answer.py     rate answers, sentence selection, hours
submission/kb/conflicts.py  contradiction detection / resolution
submission/kb/llm.py        optional Groq judge with fallback
devtools/                   dev eval set, conflict gold, miss report
tests/                      unit tests (synthetic data; `python -m unittest discover tests`)
```

The supplied website snapshots are not in this repository (see `.gitignore`).
