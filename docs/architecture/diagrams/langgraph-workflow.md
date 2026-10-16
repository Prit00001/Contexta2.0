# LangGraph research workflow

Defined in `services/research_graph.py`.

```mermaid
stateDiagram-v2
    [*] --> validate_request
    validate_request --> retrieve: valid
    validate_request --> finalize: invalid (error)
    retrieve --> generate: ≥1 source
    retrieve --> insufficient_evidence: no sources
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
| `error` (non-HTTP callers only; HTTP validation rejects first) | validate_request → finalize |
