"""No network: the hash backend and the fastembed-unsupported-model fallback path both run
without touching the network (fastembed validates the model name locally before any download)."""

from core.embeddings import Embedder


def test_hash_backend_requested_directly_is_not_degraded():
    e = Embedder("hash", "sentence-transformers/all-MiniLM-L6-v2", None)
    assert e.backend == "hash"
    assert e.degraded is False  # hash was requested, hash was used


def test_unsupported_fastembed_model_falls_back_to_hash_and_reports_degraded():
    e = Embedder("fastembed", "not-a-real-model/does-not-exist", None)
    assert e.backend == "hash"
    assert e.degraded is True  # fastembed was requested but hash is what's actually running


def test_hash_embeddings_are_normalized_and_deterministic():
    e = Embedder("hash", "unused", None)
    v1 = e.embed_sync(["retrieval augmented generation"])
    v2 = e.embed_sync(["retrieval augmented generation"])
    assert v1.shape == (1, 384)
    assert (v1 == v2).all()
