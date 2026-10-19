# arXiv snapshot

`eval/dataset/arxiv_snapshot.jsonl` freezes live arXiv results for every question in
`questions.jsonl`, so `eval/run_eval.py --arxiv` can measure `/ask`'s default path (library + arXiv)
deterministically — live search results drift over time.

- Fetched 2026-10-19 from `export.arxiv.org` with `scripts/fetch_arxiv_snapshot.py`: 143 questions,
  12 papers each (1,716 entries), no failed queries.
- Each line is one question:

```json
{"question_id": "q001", "query": "all:recurrence OR all:convolution OR …", "fetched_at": "2026-10-19",
 "papers": [{"id": "arxiv_1706.03762", "source": "arxiv", "title": "…", "url": "https://arxiv.org/abs/1706.03762v7",
             "text": "Title. Summary…", "authors": ["…"], "published": "2017-06-12"}]}
```

`query` is exactly what `services.arxiv_client.ArxivClient.build_query()` sends for that question;
the replay (`SnapshotArxiv` in `eval/run_eval.py`) looks papers up by it. `papers` is the parsed
`ArxivClient.parse()` output.

Gold labels stay library document IDs. A library paper that comes back through its arXiv copy (e.g.
LoRA, `doc_009` = arXiv 2106.09685) is mapped back to its library ID before scoring
(`library_arxiv_ids()`), so it still counts as the gold paper. Other arXiv papers are never gold — the
dataset has no arXiv-only gold labels — so for `unanswerable` questions an arXiv paper may in fact
answer the question; abstention numbers on the arXiv path need that caveat.

Refresh (appends missing questions; delete the file first for a full refetch):

    PYTHONPATH=. python scripts/fetch_arxiv_snapshot.py
