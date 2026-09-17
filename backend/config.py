"""Config: load/validate ~/.anchor/config.yaml with zero-config defaults
and graceful degradation."""
from __future__ import annotations
import os
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

LEGACY_ENV_VAR = "OPENNOTEBOOK_CONFIG_DIR"
DEFAULT_CONFIG_DIR = Path(os.environ.get("ANCHOR_CONFIG_DIR", Path.home() / ".anchor"))
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
    reranker: Section = field(default_factory=Section)
    whisper: Section = field(default_factory=lambda: Section(model="small"))


REDACTED = "***"
_SECRET_KEYS = frozenset({"api_key"})

# Provider names we know how to drive; anything else is a config typo worth
# failing loudly on at startup instead of degrading mysteriously at runtime.
_KNOWN_PROVIDERS: dict[str, frozenset[str]] = {
    "embeddings": frozenset({"bundled", "openai_compatible"}),
    "reranker": frozenset({"bundled"}),
    "whisper": frozenset({"bundled"}),
}


def redact_secrets(obj: Any) -> Any:
    """Return a copy of an asdict()-shaped config with every set secret
    replaced by REDACTED. The live secret never leaves the server process;
    clients (including our own settings UI) only ever see the marker."""
    if isinstance(obj, dict):
        return {
            k: (REDACTED if k in _SECRET_KEYS and v else redact_secrets(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_secrets(v) for v in obj]
    return obj


def strip_redacted(obj: Any) -> Any:
    """Inverse for inbound PATCH payloads: drop secret fields still carrying
    the REDACTED marker so a settings form that echoes get_config output can
    never persist "***" over the real key. A deliberately cleared (empty)
    secret is also dropped — clearing a key is done by editing the file."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _SECRET_KEYS and (v == REDACTED or not v):
                continue
            out[k] = strip_redacted(v)
        return out
    if isinstance(obj, list):
        return [strip_redacted(v) for v in obj]
    return obj


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _coerce(raw: dict) -> AppConfig:
    def section(cls, data: Any, name: str):
        data = data or {}
        if not isinstance(data, dict):
            raise TypeError(f"config section {name!r} must be a mapping, got {type(data).__name__}")
        # A typo'd key (baseurl:) must never prevent boot: ignore unknown keys
        # with a loud stderr warning so the typo is visible but harmless.
        known = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = sorted(k for k in data if k not in known)
        if unknown:
            import sys
            print(
                f"[Anchor] WARNING: ignoring unknown key(s) {unknown} "
                f"in config section {name!r} (known keys: {sorted(known)})",
                file=sys.stderr,
            )
        return cls(**{k: v for k, v in data.items() if k in known})

    llm = section(LLMSection, raw.get("llm"), "llm")
    emb = section(Section, raw.get("embeddings"), "embeddings")
    rnk = section(Section, raw.get("reranker"), "reranker")
    wh = section(Section, raw.get("whisper"), "whisper")
    return AppConfig(llm=llm, embeddings=emb, reranker=rnk, whisper=wh,
                     data_dir=raw.get("data_dir"))


def _validate(cfg: AppConfig) -> AppConfig:
    """Loud-fail on config values that would otherwise degrade mysteriously at
    runtime (unknown provider names). A missing LLM is NOT fatal by design —
    the app boots and the UI prompts for setup (main.py prints a warning)."""
    for section_name in ("embeddings", "reranker", "whisper"):
        provider = getattr(getattr(cfg, section_name), "provider", None)
        if provider is not None and provider not in _KNOWN_PROVIDERS[section_name]:
            raise ValueError(
                f"config section {section_name!r} has unknown provider {provider!r}; "
                f"known providers are {sorted(_KNOWN_PROVIDERS[section_name])}"
            )
    return cfg


def _migration_tmp(target: Path) -> Path:
    return target.parent / (target.name + ".migrating")


def _discard_tmp(tmp: Path) -> None:
    """Best-effort removal of a leftover temp path (file or dir), so crashed
    runs can't accumulate junk in the config dir across restarts."""
    try:
        if tmp.is_dir() and not tmp.is_symlink():
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            tmp.unlink(missing_ok=True)
    except Exception:
        pass


def migrate_file(target: Path, legacy: Path, label: str) -> Path:
    """Return `target`, copying `legacy` there first when the target is missing.

    One-time, copy-never-move (the original stays intact), never raises: on
    any failure it warns and returns whichever path exists, preferring the
    target. This is how the OpenNotebook→Anchor rename moves settings without
    stranding user data.

    Atomicity: the copy lands on a temp sibling and is os.replace()d into
    place only after fully succeeding — a crash mid-copy can never leave a
    partial file at the real target path (which would look "already migrated"
    on the next boot and never be retried).
    """
    try:
        if not target.exists() and legacy.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = _migration_tmp(target)
            _discard_tmp(tmp)  # leftover from an interrupted run, if any
            try:
                if legacy.is_dir():
                    shutil.copytree(legacy, tmp)
                else:
                    shutil.copy2(legacy, tmp)
                os.replace(tmp, target)
            finally:
                _discard_tmp(tmp)
            print(f"[Anchor] Migrated {label} from {legacy} to {target} (original kept).")
    except Exception as e:
        print(f"[Anchor] WARNING: {label} migration failed ({e}); continuing.")
    if target.exists() or not legacy.exists():
        return target
    return legacy


def migrate_db(target: Path, legacy: Path, label: str) -> Path:
    """Like migrate_file, but correct for SQLite databases in WAL mode: a live
    database is main-file + -shm/-wal sidecars, and copying only the main file
    silently drops every transaction since the last checkpoint. The online
    backup API copies a transactionally consistent snapshot instead, safe even
    if another process currently holds the source open. Like migrate_file, the
    backup lands on a temp sibling first: a failed backup never leaves a
    partial database at the real target path."""
    import sqlite3

    try:
        if not target.exists() and legacy.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = _migration_tmp(target)
            _discard_tmp(tmp)
            try:
                src = sqlite3.connect(str(legacy))
                try:
                    dst = sqlite3.connect(str(tmp))
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                finally:
                    src.close()
                os.replace(tmp, target)
            finally:
                _discard_tmp(tmp)
            print(f"[Anchor] Migrated {label} from {legacy} to {target} (original kept).")
    except Exception as e:
        print(f"[Anchor] WARNING: {label} migration failed ({e}); continuing.")
    if target.exists() or not legacy.exists():
        return target
    return legacy


def config_dir() -> Path:
    """Effective config dir. ANCHOR_CONFIG_DIR wins, OPENNOTEBOOK_CONFIG_DIR is
    honored for pre-rename setups, otherwise ~/.anchor with a one-time copy
    from ~/.opennotebook when upgrading."""
    override = os.environ.get("ANCHOR_CONFIG_DIR")
    if override:
        return Path(override)
    legacy_env = os.environ.get(LEGACY_ENV_VAR)
    if legacy_env:
        return Path(legacy_env)
    target = Path.home() / ".anchor"
    legacy = Path.home() / ".opennotebook"
    if not target.exists() and legacy.exists():
        migrate_file(target, legacy, "settings")
    return target


def load_config() -> AppConfig:
    cfg_dir = config_dir()
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
    return config_dir() / "data"


def save_config(cfg: AppConfig) -> None:
    cfg_dir = config_dir()
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
