"""Reranker: bundled cross-encoder (MVP). Interface pinned so a remote rerank
API (openai_compatible) slots in later without touching callers (spec §7c)."""
from __future__ import annotations
from typing import Protocol

from backend.config import AppConfig


class Reranker(Protocol):
    warning: str | None
    def rerank(self, query: str, chunks: list[dict]) -> list[dict]: ...


class _BundledReranker:
    def __init__(self, model_name: str | None = None):
        # lazy import
        from sentence_transformers import CrossEncoder  # type: ignore
        name = model_name or "cross-encoder/ms-marco-MiniLM-L-6-v2"
        self._ce = CrossEncoder(name)
        self.warning = None

    def rerank(self, query: str, chunks: list[dict]) -> list[dict]:
        if not chunks:
            return []
        pairs = [(query, c["text"]) for c in chunks]
        scores = self._ce.predict(pairs).tolist()
        order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
        return [chunks[i] for i in order]


class _RemoteReranker:
    """Deferred extension point (spec §7c). Not implemented in MVP."""
    def __init__(self, *a, **kw):
        raise NotImplementedError("openai_compatible reranker is a v1.5 extension; use provider: bundled")


def get_reranker(cfg: AppConfig) -> Reranker:
    r = cfg.reranker
    if r.provider == "openai_compatible":
        return _RemoteReranker()  # raises NotImplementedError — documents the seam
    return _BundledReranker(r.model)


if __name__ == "__main__":
    # mocked self-check — exercises the bundled path via a fake CrossEncoder.
    # In production this hits the real cross-encoder; here we sidestep model download
    # *and* the dependency: we inject a stub module into sys.modules so the lazy
    # `from sentence_transformers import CrossEncoder` resolves without installing the dep.
    import os, sys, tempfile, types
    from unittest.mock import patch, MagicMock
    from backend.config import AppConfig, Section

    os.environ["OPENNOTEBOOK_CONFIG_DIR"] = tempfile.mkdtemp()
    cfg = AppConfig(reranker=Section(provider="bundled"))
    q = "what is the definition of a limit"
    chunks = [
        {"id": 1, "text": "epsilon delta definition of a limit"},
        {"id": 2, "text": "proof of the intermediate value theorem"},
        {"id": 3, "text": "limit laws and how to apply them"},
    ]
    fake = MagicMock()
    # scores[1] > scores[3] > scores[2] → chunk 1 first, chunk 3 second.
    fake.predict.return_value.tolist.return_value = [0.95, 0.10, 0.55]
    # pre-inject sentence_transformers module so the lazy import resolves to our stub.
    if "sentence_transformers" not in sys.modules:
        st = types.ModuleType("sentence_transformers")
        st_class = MagicMock(return_value=fake)
        st.CrossEncoder = st_class
        sys.modules["sentence_transformers"] = st
    with patch("sys.modules['sentence_transformers'].CrossEncoder", st_class := MagicMock(return_value=fake)):
        rr = get_reranker(cfg)
        ranked = rr.rerank(q, chunks)
        assert ranked[0]["id"] == 1, f"most relevant chunk should rank first, got {ranked[0]}"
        assert [c["id"] for c in ranked] == [1, 3, 2], f"ordering should be 1,3,2, got {ranked}"
    # remote reranker → deferred extension: produce a useful NotImplementedError when invoked.
    try:
        get_reranker(AppConfig(reranker=Section(provider="openai_compatible")))
        assert False, "remote reranker should have raised NotImplementedError"
    except NotImplementedError:
        pass
    print("reranker OK")
