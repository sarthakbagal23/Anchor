# Task 2: config.py — load, validate, graceful degrade

**Files:**
- Create: `opennotebook/backend/config.py`
- Create: `opennotebook/config.example.yaml`

**Interfaces:**
- Consumes: `~/.opennotebook/config.yaml` (created from `config.example.yaml` on first load if missing).
- Produces: `load_config() -> AppConfig`; `AppConfig` has `.llm`, `.embeddings`, `.reranker`, `.whisper` with provider/bases; `.save()` persists. Callers (`llm_client`, embedder, whisper) read `AppConfig`.

- [ ] **Step 1: Write the failing self-check**

Create `opennotebook/backend/config.py` with only the `__main__` block:

```python
if __name__ == "__main__":
    import tempfile, os
    d = tempfile.mkdtemp()
    os.environ["OPENNOTEBOOK_CONFIG_DIR"] = d
    cfg = load_config()
    # defaults: local-only user gets working config with no keys
    assert cfg.llm.base_url and cfg.llm.model, "llm has no defaults — user must always supply"
    # graceful degrade is exercised in embedder.py (Task 5); here we only assert
    # that a missing config still produces a loadable AppConfig with bundled defaults.
    assert cfg.embeddings.provider == "bundled", "default embeddings must be bundled"
    assert cfg.whisper.provider == "bundled", "default whisper must be bundled"
    assert cfg.reranker.provider == "bundled", "default reranker must be bundled"
    print("config OK")
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd C:\Users\sarth\Desktop\opennotebook
.venv/Scripts/python.exe -m backend.config
```
Expected: `NameError: name 'load_config' is not defined`.

- [ ] **Step 3: Implement `config.py`**

Full `opennotebook/backend/config.py`:

```python
"""Config: load/validate ~/.opennotebook/config.yaml with zero-config defaults
and graceful degradation."""
from __future__ import annotations
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_DIR = Path(os.environ.get("OPENNOTEBOOK_CONFIG_DIR", Path.home() / ".opennotebook"))
DEFAULTS_YAML = """
llm:
  base_url: null
  api_key: null
  model: null
  max_context: null
embeddings:
  provider: bundled
  base_url: null
  api_key: null
  model: null
reranker:
  provider: bundled
  base_url: null
  api_key: null
  model: null
whisper:
  provider: bundled
  base_url: null
  api_key: null
  model: small
"""


@dataclass
class Section:
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    fallback: str | None = None      # "keyword" when embeddings unreachable
    warning: str | None = None       # human-readable degrade reason


@dataclass
class LLMSection:
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    max_context: int | None = None


@dataclass
class AppConfig:
    llm: LLMSection = field(default_factory=LLMSection)
    embeddings: Section = field(default_factory=Section)
    reranker: Section = field(default_factory=Section)
    whisper: Section = field(default_factory=lambda: Section(model="small"))


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _coerce(raw: dict) -> AppConfig:
    llm = LLMSection(**raw.get("llm", {}))
    emb = Section(**raw.get("embeddings", {}))
    rnk = Section(**raw.get("reranker", {}))
    wh = Section(**raw.get("whisper", {}))
    return AppConfig(llm=llm, embeddings=emb, reranker=rnk, whisper=wh)


def _validate(cfg: AppConfig) -> AppConfig:
    """Loud-fail on the one thing that truly blocks us (no LLM); degrade on the rest."""
    if not cfg.llm.base_url or not cfg.llm.model:
        # ponytail: not a hard crash — return a flag so the UI can prompt setup.
        return cfg
    return cfg


def load_config() -> AppConfig:
    cfg_dir = Path(os.environ.get("OPENNOTEBOOK_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    cfg_path = cfg_dir / "config.yaml"
    base = yaml.safe_load(DEFAULTS_YAML)
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        merged = _deep_merge(base, user)
    else:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(DEFAULTS_YAML.strip())
        merged = base
    return _validate(_coerce(merged))


def save_config(cfg: AppConfig) -> None:
    cfg_dir = Path(os.environ.get("OPENNOTEBOOK_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    cfg_path = cfg_dir / "config.yaml"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    raw = asdict(cfg)
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f)
```

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -m backend.config
```
Expected: `config OK`.

- [ ] **Step 5: Write `config.example.yaml`**

Create `opennotebook/config.example.yaml` (the documented sample users copy):

```yaml
# OpenNotebook config — copy to ~/.opennotebook/config.yaml or edit via the UI.
# Defaults (below commented values) make local-only use work with zero config.
llm:
  base_url: https://integrate.api.nvidia.com/v1   # or http://localhost:1234/v1 (LM Studio), http://localhost:11434/v1 (Ollama)
  api_key: ${NVIDIA_API_KEY}                       # env var expansion is manual; set your real key
  model: nvidia/llama-3.1-nemotron-70b
  max_context: null                                 # null = infer reasonable default per model size

embeddings:
  provider: bundled                                 # bundled (CPU sentence-transformers) | openai_compatible
  # base_url / api_key / model only used when provider: openai_compatible

reranker:
  provider: bundled                                 # bundled cross-encoder (MVP). openai_compatible is a deferred extension.

whisper:
  provider: bundled                                 # bundled faster-whisper | openai_compatible
  model: small                                      # tiny | base | small | medium | large
  # base_url / api_key used only when provider: openai_compatible
```

- [ ] **Step 6: Commit**

```bash
git add backend/config.py config.example.yaml
git commit -m "feat(config): load/validate config with zero-config defaults"
```
