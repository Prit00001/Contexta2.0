# arXiv snapshot — not yet fetched

`eval/dataset/arxiv_snapshot.jsonl` is currently empty. A frozen arXiv snapshot lets `eval/run_eval.py`
include arXiv-sourced candidates without results drifting between runs (live arXiv search changes over
time). It needs to be fetched once, from a machine that can actually reach `export.arxiv.org` -
unreachable from the sandbox this dataset was drafted in (see `docs/testing/TEST_REPORT.md` §5, B-2).

## Format

One JSON object per line, matching `services.arxiv_client.ArxivClient.parse()`'s output shape exactly:

```json
{"id": "arxiv_1706.03762", "source": "arxiv", "title": "...", "url": "https://arxiv.org/abs/1706.03762", "text": "Title. Summary...", "authors": ["..."], "published": "2017-06-12"}
```

## How to fetch it

From a machine with network access:

```python
import asyncio, json, httpx
from services.arxiv_client import ArxivClient


async def main():
    async with httpx.AsyncClient() as client:
        arxiv = ArxivClient(client)
        # run one search per topic you want snapshotted, e.g. the keywords behind
        # eval/dataset/questions.jsonl's comparison/paraphrase questions
        papers = await arxiv.search("retrieval augmented generation")
        with open("eval/dataset/arxiv_snapshot.jsonl", "a") as f:
            for p in papers:
                f.write(json.dumps(p) + "\n")


asyncio.run(main())
```

Dedupe by `id` afterward if you run multiple searches.

## Until it's populated

`eval/dataset/questions.jsonl`'s `comparison` and `paraphrase` questions are scoped to the local 25-paper
library only (`data/documents.py`) - none of them depend on arXiv-only coverage. The eval harness's default
offline run doesn't need this file at all; it only matters once arXiv-sourced retrieval is being measured.
