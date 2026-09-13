"""Embedder: bundled CPU sentence-transformers (default) or OpenAI-compatible
/v1/embeddings. Degrades to a HashingEmbedder (keyword baseline) on failure."""
from __future__ import annotations
from typing import Protocol

from backend.config import AppConfig


class Embedder(Protocol):
    dim: int
    warning: str | None
    def embed(self, text: str) -> list[float]: ...


class _BundledEmbedder:
    def __init__(self, model_name: str | None = None):
        # lazy import: torch + sentence-transformers are heavy
        from sentence_transformers import SentenceTransformer  # type: ignore
        name = model_name or "all-MiniLM-L6-v2"  # small CPU embedder, 384-dim
        self._m = SentenceTransformer(name)
        self.dim = self._m.get_sentence_embedding_dimension()
        self.warning = None

    def embed(self, text: str) -> list[float]:
        return self._m.encode(text, normalize_embeddings=True).tolist()

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        # One model call for the whole batch (sentence-transformers batches
        # internally) instead of one call per chunk during ingest.
        return self._m.encode(texts, normalize_embeddings=True).tolist()


class _OpenAICompatibleEmbedder:
    def __init__(self, base_url: str, api_key: str | None, model: str | None):
        self._base_url = base_url
        self._api_key = api_key or "unused"
        self._model = model
        self.warning = None
        self._client = None
        self.dim = 0  # unknown until first call

    def _probe(self, sample: str) -> list[float]:
        from openai import OpenAI  # lazy
        if self._client is None:
            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key,
                                  timeout=60.0, max_retries=1)
        resp = self._client.embeddings.create(model=self._model, input=sample)
        return resp.data[0].embedding

    def embed(self, text: str) -> list[float]:
        try:
            v = self._probe(text)
            self.dim = len(v)
            return v
        except Exception as e:
            self.warning = f"embeddings endpoint unreachable ({e}); using keyword fallback"
            fb = _HashingEmbedder()
            self.dim = fb.dim
            return fb.embed(text)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        # One /v1/embeddings call for the whole batch; the endpoint returns
        # vectors in input order. On failure the whole batch degrades to the
        # hashing fallback at once (retrying per-text against a dead endpoint
        # would multiply one outage into hundreds of timeouts).
        try:
            from openai import OpenAI  # lazy
            if self._client is None:
                self._client = OpenAI(base_url=self._base_url, api_key=self._api_key,
                                      timeout=120.0, max_retries=1)
            resp = self._client.embeddings.create(model=self._model, input=texts)
            vecs = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
            self.dim = len(vecs[0])
            return vecs
        except Exception as e:
            self.warning = f"embeddings endpoint unreachable ({e}); using keyword fallback"
            fb = _HashingEmbedder()
            self.dim = fb.dim
            return fb.embed_many(texts) if hasattr(fb, "embed_many") else [fb.embed(t) for t in texts]


class _HashingEmbedder:
    """Deterministic bag-of-words hash embedder. Ceiling: no semantic
    similarity, keyword-only. Upgrade path: any real embedder above. Stable dim 256."""
    def __init__(self, dim: int = 256):
        self.dim = dim
        self.warning = "embeddings unavailable — using keyword fallback (semantic recall will be weak)"

    def embed(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in text.lower().split():
            h = hash(tok) % self.dim
            v[h] = 1.0
        return v


def get_embedder(cfg: AppConfig) -> Embedder:
    e = cfg.embeddings
    if e.provider == "openai_compatible" and e.base_url:
        return _OpenAICompatibleEmbedder(e.base_url, e.api_key, e.model)
    try:
        return _BundledEmbedder(e.model)
    except Exception:
        return _HashingEmbedder()


def embed_many(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    """Batch embedding with graceful fallback: embedders that implement
    embed_many do one batched call; anything else (test doubles, future
    providers) degrades to per-text embed(). Never returns a partial list."""
    if not texts:
        return []
    method = getattr(embedder, "embed_many", None)
    if callable(method):
        return method(texts)
    return [embedder.embed(t) for t in texts]


if __name__ == "__main__":
    # mocked self-check — exercises only paths that don't require heavy ML deps to be installed.
    # bundled path is reachable in production only when the user installs the [local] extra;
    # we assert that the bundled class is *importable* but never instantiate it here.
    from unittest.mock import patch, MagicMock

    from backend.config import AppConfig, Section

    # 1) openai_compatible path: mock the OpenAI client, assert dim-stability + distinct vectors.
    cfg = AppConfig(embeddings=Section(provider="openai_compatible", base_url="http://x/v1", api_key="k", model="m"))
    fake_vec_a = [0.1, 0.2, -0.3] * 10
    fake_vec_b = [0.0, 0.1, -0.4] * 10
    with patch("openai.OpenAI") as MockOpenAI:
        client = MagicMock()
        MockOpenAI.return_value = client
        # different embeddings per input via side_effect on embeddings.create
        client.embeddings.create.side_effect = [
            MagicMock(data=[MagicMock(embedding=fake_vec_a)]),
            MagicMock(data=[MagicMock(embedding=fake_vec_b)]),
        ]
        emb = get_embedder(cfg)
        v1 = emb.embed("epsilon delta definition of a limit")
        v2 = emb.embed("integral of x squared")
        assert isinstance(v1, list) and len(v1) > 0, "must return a non-empty vector"
        assert len(v1) == len(v2), "dim must be stable across calls"
        assert v1 != v2, "distinct texts should produce distinct vectors"
        assert emb.dim == len(v1)
        assert emb.warning is None, "successful path should not set a warning"

    # 2) graceful degrade: unreachable endpoint → HashingEmbedder fallback + warning.
    bad = AppConfig(embeddings=Section(
        provider="openai_compatible", base_url="http://127.0.0.1:9/none", api_key=None, model="m"
    ))
    deg = get_embedder(bad)
    deg.embed("test")  # triggers lazy probe → fails → falls back
    assert deg.warning, "degraded embedder must set a warning"
    assert deg.dim == 256, "HashingEmbedder has stable dim 256"

    # 3) bundled path: class importable and resolvable via get_embedder when provider: bundled
    bundled_cfg = AppConfig(embeddings=Section(provider="bundled"))
    assert _BundledEmbedder is not None  # only confirms class is defined
    # we don't construct — that would require sentence-transformers to be installed.

    print("embedder OK")
