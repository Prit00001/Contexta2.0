# LangGraph research workflow

Defined in `services/research_graph.py`.

```mermaid
stateDiagram-v2
    [*] --> validate_request
    validate_request --> retrieve: valid
    validate_request --> finalize: invalid (error)
    retrieve --> generate: ≥1 source
    retrieve --> insufficient_evidence: no sources
    retrieve --> finalize: retriever raised (error)
    generate --> validate_answer: LLM answered
    generate --> fallback: no provider succeeded
    validate_answer --> finalize: usable (≥1 valid citation)
    validate_answer --> generate: unusable & attempts < LLM_MAX_ATTEMPTS
    validate_answer --> fallback: unusable & attempts exhausted
    fallback --> finalize
    insufficient_evidence --> finalize
    finalize --> [*]
```

| Outcome | Path |
|---|---|
| `answered` | validate_request → retrieve → generate → validate_answer → finalize |
| `extractive_fallback` | … → generate → fallback → finalize, or validate_answer → fallback |
| `insufficient_evidence` | retrieve → insufficient_evidence → finalize |
| `error` | validate_request → finalize (invalid input from non-HTTP callers; HTTP validation rejects first → 422), or retrieve → finalize (retriever raised → HTTP 503) |

## Generated from the compiled graph

Produced by `build_research_graph(...).get_graph().draw_mermaid()` (dotted = conditional edge). `tests/test_research_graph.py::test_graph_structure_matches_design` asserts the node set and key edges.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	validate_request(validate_request)
	retrieve(retrieve)
	generate(generate)
	validate_answer(validate_answer)
	fallback(fallback)
	insufficient_evidence(insufficient_evidence)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> validate_request;
	fallback --> finalize;
	generate -.-> fallback;
	generate -.-> validate_answer;
	insufficient_evidence --> finalize;
	retrieve -.-> finalize;
	retrieve -.-> generate;
	retrieve -.-> insufficient_evidence;
	validate_answer -.-> fallback;
	validate_answer -.-> finalize;
	validate_answer -.-> generate;
	validate_request -.-> finalize;
	validate_request -.-> retrieve;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```
