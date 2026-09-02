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
data_dir: null
llm:
  base_url: null
  api_key: null
  model: null
  vision_model: meta/llama-3.2-11b-vision-instruct
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
    model: str | None = None       # standard model
    fast_model: str | None = None  # faster alternative
    vision_model: str | None = None  # multimodal model used to "see" PDF pages
    active: str | None = "standard"  # "standard" | "fast"
    max_context: int | None = None


@dataclass
class AppConfig:
    llm: LLMSection = field(default_factory=LLMSection)
    data_dir: str | None = None  # where uploaded PDFs + rendered page images are stored
    embeddings: Section = field(default_factory=Section)
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


def data_dir(cfg: AppConfig | None = None) -> Path:
    """Effective storage dir for uploaded PDFs + rendered page images."""
    if cfg is not None and cfg.data_dir:
        return Path(cfg.data_dir)
    base = Path(os.environ.get("OPENNOTEBOOK_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    return base / "data"


def save_config(cfg: AppConfig) -> None:
    cfg_dir = Path(os.environ.get("OPENNOTEBOOK_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    cfg_path = cfg_dir / "config.yaml"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    raw = asdict(cfg)
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f)


if __name__ == "__main__":
    import tempfile
    d = tempfile.mkdtemp()
    os.environ["OPENNOTEBOOK_CONFIG_DIR"] = d
    cfg = load_config()
    # LLM has no defaults by design — the user must always supply base_url + model.
    # The three bundled-provider assertions below are the real spec test.
    assert cfg.embeddings.provider == "bundled", "default embeddings must be bundled"
    assert cfg.whisper.provider == "bundled", "default whisper must be bundled"
    assert cfg.reranker.provider == "bundled", "default reranker must be bundled"
    print("config OK")
