"""Security contract: secret redaction, merge-safe PATCH, CSRF enforcement.

Uses FastAPI TestClient (no Origin headers, like curl/python) plus forged
Origin/Referer cases. The app under test gets an isolated Store; module
singletons are restored afterwards so test order can't leak state.
"""
import os

import pytest
from fastapi.testclient import TestClient

from backend import api as api_mod
from backend.config import AppConfig, LLMSection, Section, _coerce, _validate
from backend.store import Store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOTEBOOK_CONFIG_DIR", str(tmp_path / "cfg"))
    db = os.path.join(str(tmp_path), "sec.db")
    store = Store(db)
    store.init()
    cfg = AppConfig(
        llm=LLMSection(base_url="http://x/v1", api_key="SECRET-LLM", model="m"),
        embeddings=Section(provider="bundled", api_key="SECRET-EMB"),
    )
    app = api_mod.build_app(store, cfg)
    api_mod.add_security_middleware(app, token="test-token")
    prev_store, prev_cfg = api_mod._STORE, api_mod._CFG
    # build_app already assigned the singletons; yield, then restore
    c = TestClient(app)
    c.cookies.set("onb_xsrf", "test-token")
    yield c, store, cfg
    api_mod._STORE, api_mod._CFG = prev_store, prev_cfg


def test_config_redacts_all_api_keys(client):
    c, _, _ = client
    body = c.get("/api/config").json()
    assert body["llm"]["api_key"] == "***"
    assert body["embeddings"]["api_key"] == "***"
    assert "SECRET-LLM" not in str(body) and "SECRET-EMB" not in str(body)
    # non-secrets still visible (frontend needs them for the model toggle)
    assert body["llm"]["model"] == "m"


def test_patch_redacted_marker_preserves_real_key(client):
    c, _, _ = client
    r = c.patch("/api/config", json={"llm": {"api_key": "***", "model": "m2"}})
    assert r.status_code == 200
    assert r.json()["llm"]["api_key"] == "***"  # still redacted on the wire
    assert api_mod._CFG.llm.api_key == "SECRET-LLM"  # real key untouched
    assert api_mod._CFG.llm.model == "m2"  # other fields merge normally


def test_patch_real_key_updates_and_survives_reload(client):
    c, _, _ = client
    c.patch("/api/config", json={"llm": {"api_key": "NEWKEY"}})
    assert api_mod._CFG.llm.api_key == "NEWKEY"
    assert c.get("/api/config").json()["llm"]["api_key"] == "***"


def test_mutating_without_origin_passes(client):
    # curl/python/service clients send no Origin: unchanged behavior
    c, _, _ = client
    assert c.post("/api/workspaces", json={"title": "w"}).status_code == 200


def test_foreign_origin_blocked_without_token(client):
    c, _, _ = client
    r = c.post("/api/workspaces", json={"title": "evil"},
               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.get("/api/config", headers={"Referer": "https://evil.example/page"})
    assert r.status_code == 403


def test_same_origin_and_token_pass(client):
    c, _, _ = client
    same = {"Origin": "http://127.0.0.1:8765"}
    assert c.get("/api/config", headers=same).status_code == 200
    # same-origin page must still echo the token on mutating calls
    assert c.post("/api/workspaces", json={"title": "w1"}, headers=same).status_code == 403
    same_token = {"Origin": "http://127.0.0.1:8765", "X-Auth-Token": "test-token"}
    assert c.post("/api/workspaces", json={"title": "w2"}, headers=same_token).status_code == 200
    # foreign origins are refused outright — a valid token must NOT override
    # that (a bypass here would turn any token leak into remote execution)
    evil_token = {"Origin": "https://evil.example", "X-Auth-Token": "test-token"}
    assert c.post("/api/workspaces", json={"title": "w3"}, headers=evil_token).status_code == 403


def test_untrusted_host_rejected(client):
    c, _, _ = client
    # TestClient defaults to host "testserver" (allowlisted); override it
    r = c.post("/api/workspaces", json={"title": "x"},
               headers={"Host": "evil.example"})
    assert r.status_code == 403


def test_coerce_tolerates_unknown_keys():
    cfg = _coerce({"llm": {"model": "m", "baseurl": "typo"}, "embeddings": {"provider": "bundled"}})
    assert cfg.llm.model == "m"


def test_validate_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unknown provider"):
        _validate(AppConfig(embeddings=Section(provider="mystery")))
    # sane configs pass through untouched
    assert _validate(AppConfig(embeddings=Section(provider="bundled"))) is not None
