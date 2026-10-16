# Component diagram

```mermaid
flowchart TB
    subgraph app[app/ — HTTP layer]
      main[main.py<br/>lifespan · middleware]
      routes[routes.py<br/>/ask /ask-pdf /health /ready]
      ws[routes_workspace.py · routes_admin.py · deps.py]
      schemas[schemas.py]
      config[config.py<br/>Pydantic Settings]
      obs[observability.py<br/>logging · request IDs]
    end
    subgraph services[services/ — orchestration]
      search[search_service.py<br/>cache · response shaping]
      graph[research_graph.py<br/>LangGraph StateGraph]
      arxiv[arxiv_client.py]
      wsqa[workspace_qa.py · retrieval.py · ingestion.py · worker.py]
    end
    subgraph core[core/ — domain + adapters]
      lcr[lc_retriever.py<br/>LangChain BaseRetriever adapters]
      src[sources.py<br/>metadata validation · Document mapping]
      ret[retriever.py · vector_store.py<br/>FAISS]
      emb[embeddings.py<br/>fastembed / hash]
      gen[generator.py<br/>provider chain + extractive]
      llm[llm.py<br/>ChatOpenAI factory · error mapping]
      prm[prompts.py<br/>ChatPromptTemplate]
      cit[citations.py]
      db[db.py · storage.py · parsers.py · chunking.py]
    end

    main --> routes & ws & obs & config
    routes --> search
    search --> graph
    graph --> lcr & gen & cit
    lcr --> ret & emb & arxiv & src
    gen --> llm & prm
    ws --> wsqa --> gen
    wsqa --> db
```

LangChain is imported only in `lc_retriever.py`, `sources.py`, `llm.py`, `prompts.py`; LangGraph only in `research_graph.py`.
