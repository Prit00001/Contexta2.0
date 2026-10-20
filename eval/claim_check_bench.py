"""How well do the claim checks catch claims their cited source does not support?

Built from the real claims in eval/claims_to_label.csv (sentences gpt-oss wrote, each with the
passage it cited). Unlabelled, so the benchmark makes its own negatives with a known answer:

* original      - the claim with the passage it really cited (mostly supported: these are what
                  the checks must NOT flag)
* wrong_source  - the same claim paired with another paper's passage (a citation to the wrong source)
* wrong_number  - a figure in the claim changed (175 billion -> 350 billion)
* flipped       - a direction word flipped (reduces -> increases, faster -> slower)

Checks compared: ``quote`` (verbatim match only), ``numbers`` (quote + figures must appear in the
source), ``judge`` (quote + LLM judge, i.e. CLAIM_CHECK=llm before numbers existed) and ``full``
(quote + numbers + judge, the current CLAIM_CHECK=llm).

    PYTHONPATH=. python eval/claim_check_bench.py            # local checks only, no LLM
    PYTHONPATH=. python eval/claim_check_bench.py --judge    # + the LLM judge (needs a provider key)

Judge replies are cached in eval/claim_bench_cache.jsonl, so reruns cost nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "claim_bench_cache.jsonl"
FLIPS = [
    ("reduces", "increases"), ("increases", "reduces"), ("fewer", "more"), ("faster", "slower"),
    ("slower", "faster"), ("smaller", "larger"), ("larger", "smaller"), ("higher", "lower"), ("lower", "higher"),
    ("outperforms", "underperforms"), ("improves", "degrades"), ("cheaper", "more expensive"),
    ("without", "with"), ("less", "more"), ("more", "less"),
]  # fmt: skip
_TITLE = re.compile(r"^\[\d+\]\s*(.+?)\.\s")


def load_pairs() -> list[dict]:
    rows = list(csv.DictReader(open(HERE / "claims_to_label.csv", encoding="utf-8")))
    out = []
    for r in rows:
        cited = r["cited_text"].strip()
        title = (_TITLE.match(cited) or [None, ""])[1]
        out.append({"qid": r["question_id"], "claim": r["claim"].strip(), "title": title, "text": cited})
    return out


def _bump(match: re.Match) -> str:
    raw = match.group(0).replace(",", "")
    n = float(raw)
    if 1900 <= n <= 2100 and raw.isdigit():
        return str(int(n) + 3)
    new = n * 2 if n else 1
    return (
        f"{int(new):,}"
        if new.is_integer() and "," in match.group(0)
        else (str(int(new)) if new.is_integer() else f"{new:g}")
    )


def make_cases(pairs: list[dict]) -> list[dict]:
    from core.claim_support import _IDENTIFIER, _LOOKALIKES, _NUMBER

    cases = [{"kind": "original", "claim": p["claim"], "source": p["text"]} for p in pairs]
    n = len(pairs)
    for i, p in enumerate(pairs):
        for step in range(97, 97 + n):  # a passage from a different paper, deterministic
            other = pairs[(i + step) % n]
            if other["title"] != p["title"]:
                cases.append({"kind": "wrong_source", "claim": p["claim"], "source": other["text"]})
                break
        claim = p["claim"].translate(_LOOKALIKES)
        masked = _IDENTIFIER.sub(lambda m: "\0" * len(m.group(0)), claim)
        m = next((m for m in _NUMBER.finditer(masked) if float(m.group(0).replace(",", "")) > 12), None)
        if m:
            changed = claim[: m.start()] + _bump(m) + claim[m.end() :]
            cases.append({"kind": "wrong_number", "claim": changed, "source": p["text"]})
        for a, b in FLIPS:
            hit = re.search(rf"\b{a}\b", p["claim"])
            if hit:
                flipped = p["claim"][: hit.start()] + b + p["claim"][hit.end() :]
                cases.append({"kind": "flipped", "claim": flipped, "source": p["text"]})
                break
    return cases


def local_verdicts(cases: list[dict], use_numbers: bool) -> list[str]:
    from core.claim_support import quote_supported, unsupported_numbers

    out = []
    for c in cases:
        src = [{"text": c["source"]}]
        if quote_supported(c["claim"], src):
            out.append("supported")
        elif use_numbers and unsupported_numbers(c["claim"], src):
            out.append("not_supported")
        else:
            out.append("unknown")
    return out


async def judge_verdicts(cases: list[dict], batch: int = 8) -> list[str]:
    """The production judge prompt, ``batch`` claims per call (as one answer's claims would be)."""
    import httpx

    from app.config import get_settings
    from core.claim_support import build_judge_messages, parse_verdicts
    from core.generator import Generator

    cache = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            cache[row["key"]] = row["verdicts"]
    settings = get_settings()
    out: list[str] = ["unknown"] * len(cases)
    async with httpx.AsyncClient() as client:
        gen = Generator(settings.providers, client, settings.llm_timeout)
        if not gen.providers:
            raise SystemExit("--judge needs an LLM provider (GROQ_API_KEY, ...)")
        for start in range(0, len(cases), batch):
            chunk = cases[start : start + batch]
            items = [(c["claim"], [(1, {"text": c["source"]})]) for c in chunk]
            key = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
            for attempt in range(8):  # free tiers rate-limit: back off, and never cache a failure
                if key in cache:
                    break
                text, _ = await gen.complete(build_judge_messages(items))
                verdicts = parse_verdicts(text or "", len(chunk))
                if verdicts is None:
                    await asyncio.sleep(min(60, 8 * 2**attempt))
                    gen._cooldown_until.clear()  # our own backoff replaces the provider cooldown
                    continue
                cache[key] = {str(k): v for k, v in verdicts.items()}
                with CACHE.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": key, "verdicts": cache[key]}) + "\n")
                print(f"  judged {min(start + batch, len(cases))}/{len(cases)}", flush=True)
            if key not in cache:
                raise SystemExit(f"judge kept failing at case {start}; rerun later (progress is cached)")
            for i in range(len(chunk)):
                out[start + i] = cache[key].get(str(i + 1), "unknown")
    return out


def combine(first: list[str], judge: list[str]) -> list[str]:
    """A local verdict stands; the judge decides only what the local checks left unknown."""
    return [f if f != "unknown" else j for f, j in zip(first, judge, strict=True)]


def table(cases: list[dict], results: dict[str, list[str]]) -> str:
    kinds = ["original", "wrong_source", "wrong_number", "flipped"]
    counts = Counter(c["kind"] for c in cases)
    lines = [
        "| Check | False alarms on original claims | Caught: wrong source | Caught: wrong number | Caught: flipped |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, verdicts in results.items():
        flagged = defaultdict(int)
        for c, v in zip(cases, verdicts, strict=True):
            flagged[c["kind"]] += v == "not_supported"
        cells = [f"{flagged[k]}/{counts[k]} ({100 * flagged[k] / max(counts[k], 1):.0f}%)" for k in kinds]
        lines.append(f"| `{name}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--judge", action="store_true", help="also run the LLM judge")
    args = ap.parse_args()
    cases = make_cases(load_pairs())
    print("cases:", dict(Counter(c["kind"] for c in cases)))
    quote = local_verdicts(cases, use_numbers=False)
    numbers = local_verdicts(cases, use_numbers=True)
    results = {"quote": quote, "numbers": numbers}
    if args.judge:
        judge = asyncio.run(judge_verdicts(cases))
        results["judge"] = combine(quote, judge)
        results["full"] = combine(numbers, judge)
    print()
    print(table(cases, results))


if __name__ == "__main__":
    main()
