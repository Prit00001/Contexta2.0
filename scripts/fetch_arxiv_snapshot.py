"""Freeze live arXiv results for the evaluation set (eval/dataset/arxiv_snapshot.jsonl).

Live arXiv search drifts (new papers, ranking changes), so evals replay a frozen snapshot instead:
one line per question with the exact query Contexta would send and the parsed papers it got back.

    PYTHONPATH=. python scripts/fetch_arxiv_snapshot.py            # all questions, ~3 s each (arXiv's limit)
    PYTHONPATH=. python scripts/fetch_arxiv_snapshot.py --limit 5

Resumable: questions already in the file are skipped. Needs network access to export.arxiv.org.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from pathlib import Path

import httpx

from services.arxiv_client import ArxivClient

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "eval" / "dataset" / "questions.jsonl"
SNAPSHOT = ROOT / "eval" / "dataset" / "arxiv_snapshot.jsonl"


async def main(limit: int | None, max_results: int) -> None:
    questions = [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()][:limit]
    done = set()
    if SNAPSHOT.exists():
        done = {json.loads(line)["question_id"] for line in SNAPSHOT.read_text().splitlines() if line.strip()}
    fetched_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    async with httpx.AsyncClient(headers={"User-Agent": "Contexta/2.0 eval snapshot"}) as client:
        arxiv = ArxivClient(client, max_results=max_results, ttl=0)
        with SNAPSHOT.open("a") as out:
            for q in questions:
                if q["id"] in done:
                    continue
                for attempt in range(3):
                    try:
                        papers = await arxiv.search(q["question"])
                        break
                    except httpx.HTTPError as exc:  # arXiv is flaky under load: back off and retry
                        print(f"{q['id']}: {type(exc).__name__}, retry {attempt + 1}")
                        await asyncio.sleep(10 * (attempt + 1))
                else:
                    print(f"{q['id']}: giving up")
                    continue
                row = {
                    "question_id": q["id"],
                    "query": ArxivClient.build_query(q["question"]),
                    "fetched_at": fetched_at,
                    "papers": papers,
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                print(f"{q['id']}: {len(papers)} papers")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-results", type=int, default=12, help="papers per query (ARXIV_MAX_RESULTS default)")
    args = ap.parse_args()
    asyncio.run(main(args.limit, args.max_results))
