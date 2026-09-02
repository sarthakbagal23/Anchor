"""FastAPI routes (spec §9): workspace CRUD, source add + status, chat (SSE),
ingestion progress (SSE), config read/update. Thin — delegates to store, pipeline, ingest."""
from __future__ import annotations
import asyncio
import json
import os

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from backend.config import AppConfig, load_config, save_config, data_dir
from backend.store import Store
from backend.grounding.pipeline import Pipeline
from backend.ingestion.ingest import ingest_url
from backend.ingestion.document import SUPPORTED, ingest_pdf
from backend.ingestion import pdfrender

router = APIRouter(prefix="/api")

# module-level singletons set by build_app(); kept simple for a single-user local tool.
_STORE: Store | None = None
_CFG: AppConfig | None = None
_INGEST_TASKS: set = set()


def _run_ced_after_ingest(source_id: int, status: str) -> dict:
    """After a source reaches 'ready', detect whether its course is an AP CED
    course; if so, auto-apply CED import + chunk-matching. Called on the same
    background thread as ingest so the DB writes are not interleaved."""
    if status != "ready":
        return {"source_id": source_id, "detected": False, "reason": f"status={status}"}
    from backend.objectives import ced
    from backend.grounding.embedder import get_embedder
    return ced.apply_ced_to_source(_STORE, get_embedder(_CFG), source_id)


def _wrap_ingest(source_id: int, run: callable) -> dict:
    """Run `run` (a zero-arg ingest callable), then apply the CED path if the
    source finished ingesting as an AP CED course. Called on a background thread."""
    status = "queued"
    try:
        run()
    except Exception as e:  # ingest fns isolate their own errors, but be safe
        _STORE.set_source_status(source_id, "failed", error=str(e))
        return
    src = _STORE.get_source(source_id)
    status = src["status"] if src else "failed"
    return _run_ced_after_ingest(source_id, status)


class WorkspaceIn(BaseModel):
    title: str


class CourseIn(BaseModel):
    title: str


class UnitIn(BaseModel):
    title: str


class SourceIn(BaseModel):
    url: str


class ConfigIn(BaseModel):
    llm: dict | None = None
    embeddings: dict | None = None
    reranker: dict | None = None
    whisper: dict | None = None


@router.post("/workspaces")
def create_workspace(body: WorkspaceIn):
    # A new workspace is backed by a real Course + Unit so sources live under the
    # Course -> Unit -> Sources structure from the start (source/chat stays as-is).
    ws_id, course_id, unit_id = _STORE.create_course_unit_workspace(body.title)
    return {"id": ws_id, "title": body.title, "course_id": course_id, "unit_id": unit_id}


@router.get("/workspaces")
def list_workspaces():
    return _STORE.list_workspaces()


@router.get("/workspaces/{ws_id}")
def get_workspace(ws_id: int):
    ws = _STORE.get_workspace(ws_id)
    if not ws:
        raise HTTPException(404, "workspace not found")
    ws["sources"] = _STORE.list_sources(ws_id)
    ws["messages"] = _STORE.list_messages(ws_id)
    unit = _STORE.get_unit(ws["unit_id"]) if ws.get("unit_id") else None
    ws["unit"] = unit
    ws["course"] = _STORE.get_course(unit["course_id"]) if unit else None
    return ws


# --- study guide (persisted, objective-driven) ---
def _workspace_unit(ws_id: int) -> tuple[dict, dict]:
    ws = _STORE.get_workspace(ws_id)
    if not ws:
        raise HTTPException(404, "workspace not found")
    unit = _STORE.get_unit(ws["unit_id"]) if ws.get("unit_id") else None
    return ws, unit


@router.get("/workspaces/{ws_id}/objectives")
def list_workspace_objectives(ws_id: int):
    ws, unit = _workspace_unit(ws_id)
    if not unit:
        return {"objectives": []}
    objectives = _STORE.list_unit_objectives(unit["id"])
    for o in objectives:
        o["chunks"] = _STORE.get_objective_chunks(o["id"])
    return {"objectives": objectives}


@router.get("/workspaces/{ws_id}/study-guide")
def get_study_guide(ws_id: int):
    from backend.study.guide import StudyGuideGenerator
    gen = StudyGuideGenerator(_STORE, _CFG)
    result = gen.fetch(ws_id)
    if not result:
        return {"guide": None, "sections": []}
    return result


@router.post("/workspaces/{ws_id}/study-guide")
def generate_study_guide(ws_id: int):
    from backend.study.guide import StudyGuideGenerator
    gen = StudyGuideGenerator(_STORE, _CFG)
    result = gen.generate(ws_id)
    if not result:
        return {"guide": None, "sections": [], "error": "No learning objectives for this workspace yet."}
    return result


@router.get("/workspaces/{ws_id}/study-guide/stream")
def stream_study_guide(ws_id: int):
    """SSE variant of study-guide generation (GET so the frontend can use
    EventSource). Instead of blocking until all 24-26 sections are written (one LLM
    call each), it streams each completed section to the client as it's ready.
      meta    -> {"objectives": N}
      section -> {"index": i, "total": N, "section": {...}}
      done    -> {"guide": {...}, "sections": [...], "uncovered": N}
      error   -> {"error": "..."}
    """
    from backend.study.guide import StudyGuideGenerator

    def gen():
        try:
            generator = StudyGuideGenerator(_STORE, _CFG)
            sgen = generator.generate_stream(ws_id)
            for index, total, payload in sgen:
                if index is None:
                    yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                    return
                if index == 1:
                    yield f"event: meta\ndata: {json.dumps({'objectives': total})}\n\n"
                yield f"event: section\ndata: {json.dumps({'index': index, 'total': total, 'section': payload})}\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.delete("/workspaces/{ws_id}")
def delete_workspace_endpoint(ws_id: int):
    if not _STORE.get_workspace(ws_id):
        raise HTTPException(404, "workspace not found")
    _STORE.delete_workspace(ws_id)
    return {"status": "ok"}


# --- courses ---
@router.post("/courses")
def create_course(body: CourseIn):
    course_id = _STORE.add_course(body.title)
    return {"id": course_id, "title": body.title}


@router.get("/courses")
def list_courses():
    return _STORE.list_courses()


@router.get("/courses/{course_id}")
def get_course(course_id: int):
    c = _STORE.get_course(course_id)
    if not c:
        raise HTTPException(404, "course not found")
    c["units"] = _STORE.list_units(course_id)
    return c


@router.delete("/courses/{course_id}")
def delete_course_endpoint(course_id: int):
    if not _STORE.get_course(course_id):
        raise HTTPException(404, "course not found")
    _STORE.delete_course(course_id)
    return {"status": "ok"}


# --- units ---
@router.post("/courses/{course_id}/units")
def create_unit(course_id: int, body: UnitIn):
    if not _STORE.get_course(course_id):
        raise HTTPException(404, "course not found")
    unit_id = _STORE.add_unit(course_id, body.title)
    return {"id": unit_id, "course_id": course_id, "title": body.title}


@router.get("/courses/{course_id}/units")
def list_units(course_id: int):
    if not _STORE.get_course(course_id):
        raise HTTPException(404, "course not found")
    return _STORE.list_units(course_id)


@router.get("/units/{unit_id}")
def get_unit(unit_id: int):
    u = _STORE.get_unit(unit_id)
    if not u:
        raise HTTPException(404, "unit not found")
    u["sources"] = _STORE.list_unit_sources(unit_id)
    return u


@router.delete("/units/{unit_id}")
def delete_unit_endpoint(unit_id: int):
    if not _STORE.get_unit(unit_id):
        raise HTTPException(404, "unit not found")
    _STORE.delete_unit(unit_id)
    return {"status": "ok"}


@router.post("/units/{unit_id}/workspaces")
def create_unit_workspace(unit_id: int, body: WorkspaceIn):
    if not _STORE.get_unit(unit_id):
        raise HTTPException(404, "unit not found")
    ws_id = _STORE.add_workspace(body.title, unit_id)
    return {"id": ws_id, "title": body.title, "unit_id": unit_id}


@router.post("/units/{unit_id}/sources/{source_id}")
def assign_source(unit_id: int, source_id: int):
    if not _STORE.get_unit(unit_id):
        raise HTTPException(404, "unit not found")
    if not _STORE.get_source(source_id):
        raise HTTPException(404, "source not found")
    _STORE.assign_source_to_unit(source_id, unit_id)
    return {"status": "ok", "source_id": source_id, "unit_id": unit_id}



@router.post("/workspaces/{ws_id}/sources")
async def add_source(ws_id: int, body: SourceIn):
    if not _STORE.get_workspace(ws_id):
        raise HTTPException(404, "workspace not found")
    # title/youtube_id filled by ingest after download; pre-create with url as title.
    src_id = _STORE.add_source(ws_id, "youtube", body.url, None, None)
    # kick off background ingestion (error-isolated inside ingest_url), then apply CED if AP
    task = asyncio.create_task(asyncio.to_thread(
        _wrap_ingest, src_id, lambda: ingest_url(src_id, body.url, _CFG, _STORE)
    ))
    _INGEST_TASKS.add(task)
    task.add_done_callback(_INGEST_TASKS.discard)
    return {"source_id": src_id}


@router.post("/workspaces/{ws_id}/uploads")
async def upload_source(ws_id: int, request: Request):
    if not _STORE.get_workspace(ws_id):
        raise HTTPException(404, "workspace not found")
    # Parse the single multipart file with the stdlib. This keeps YouTube startup
    # independent of python-multipart; the upload route still accepts browser FormData.
    from email import policy
    from email.parser import BytesParser
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(400, "Upload a file as multipart form data")
    raw = await request.body()
    envelope = (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw
    message = BytesParser(policy=policy.default).parsebytes(envelope)
    file_part = next((part for part in message.iter_attachments() if part.get_filename()), None)
    if file_part is None:
        raise HTTPException(400, "No file was included")
    filename = file_part.get_filename() or "uploaded-source"
    from pathlib import PurePath
    if PurePath(filename).suffix.lower() not in SUPPORTED:
        raise HTTPException(415, "PDF is the only document source supported in this step")
    data = file_part.get_payload(decode=True) or b""
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Files must be 25 MB or smaller")
    src_id = _STORE.add_source(ws_id, PurePath(filename).suffix.lower().lstrip("."), filename, None, None)
    # Persist the original PDF so the Source Preview can render it (PDF.js) and the
    # vision path can send page images.
    from backend.config import data_dir
    from pathlib import Path
    ddir = data_dir(_CFG)
    (ddir / "pdf").mkdir(parents=True, exist_ok=True)
    file_path = str(ddir / "pdf" / f"{src_id}.pdf")
    with open(file_path, "wb") as f:
        f.write(data)
    _STORE.set_source_file_path(src_id, file_path)
    task = asyncio.create_task(asyncio.to_thread(
        _wrap_ingest, src_id, lambda: ingest_pdf(src_id, filename, data, _CFG, _STORE, file_path)
    ))
    _INGEST_TASKS.add(task)
    task.add_done_callback(_INGEST_TASKS.discard)
    return {"source_id": src_id, "filename": filename}


@router.get("/workspaces/{ws_id}/sources/{src_id}")
def get_source(ws_id: int, src_id: int):
    s = _STORE.get_source(src_id)
    if not s or s["workspace_id"] != ws_id:
        raise HTTPException(404, "source not found")
    return s


@router.get("/workspaces/{ws_id}/sources/{src_id}/file")
def get_pdf_file(ws_id: int, src_id: int):
    s = _STORE.get_source(src_id)
    if not s or s["workspace_id"] != ws_id or s["type"] != "pdf":
        raise HTTPException(404, "source not found")
    if not s.get("file_path") or not os.path.exists(s["file_path"]):
        raise HTTPException(404, "pdf file not stored")
    return FileResponse(s["file_path"], media_type="application/pdf",
                        filename=os.path.basename(s["file_path"]))


@router.get("/workspaces/{ws_id}/sources/{src_id}/pages/{page_num}/image")
def get_page_image(ws_id: int, src_id: int, page_num: int):
    s = _STORE.get_source(src_id)
    if not s or s["workspace_id"] != ws_id or s["type"] != "pdf":
        raise HTTPException(404, "source not found")
    if not s.get("file_path") or not os.path.exists(s["file_path"]):
        raise HTTPException(404, "pdf file not stored")
    try:
        path = pdfrender.render_page(data_dir(_CFG), src_id, s["file_path"], page_num)
    except IndexError:
        raise HTTPException(404, "page out of range")
    return FileResponse(path, media_type="image/png")


@router.delete("/workspaces/{ws_id}/sources/{src_id}")
def delete_source_endpoint(ws_id: int, src_id: int):
    s = _STORE.get_source(src_id)
    if not s or s["workspace_id"] != ws_id:
        raise HTTPException(404, "source not found")
    _STORE.delete_source(src_id)
    return {"status": "ok"}


@router.get("/workspaces/{ws_id}/sources/{src_id}/stream")
def source_progress(ws_id: int, src_id: int):
    """SSE: emit status until ready/failed, then close."""
    def gen():
        import time
        last = None
        for _ in range(600):  # 10 min ceiling
            s = _STORE.get_source(src_id)
            if not s:
                yield f"event: error\ndata: not found\n\n"
                return
            if s["status"] != last:
                yield f"data: {json.dumps({'status': s['status'], 'error': s.get('error')})}\n\n"
                last = s["status"]
                if s["status"] in ("ready", "failed"):
                    return
            time.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/workspaces/{ws_id}/chat")
async def chat(ws_id: int, body: dict):
    if not _STORE.get_workspace(ws_id):
        raise HTTPException(404, "workspace not found")
    query = body.get("message", "")
    _STORE.add_message(ws_id, "user", query)
    raw_history = _STORE.list_messages(ws_id)
    # Strip citation blobs from stored assistant messages before sending to LLM
    clean_history = []
    for m in raw_history[:-1]:  # exclude the just-added user msg
        if m["role"] == "assistant" and "|||CITATIONS|||" in m["content"]:
            clean_history.append({"role": "assistant", "content": m["content"].split("|||CITATIONS|||")[0]})
        else:
            clean_history.append(m)

    def gen():
        try:
            pipe = Pipeline(_STORE, _CFG)
            acc = []
            for delta in pipe.stream_answer(query, ws_id, chat_history=clean_history):
                if delta.startswith("__CITATIONS__"):
                    cmap = json.loads(delta[len("__CITATIONS__"):])
                    yield f"event: citations\ndata: {json.dumps(cmap)}\n\n"
                    _STORE.add_message(ws_id, "assistant", "".join(acc) + "|||CITATIONS|||" + json.dumps(cmap))
                    return
                acc.append(delta)
                yield f"data: {json.dumps({'token': delta})}\n\n"
            # If stream ended without a __CITATIONS__ marker (no passages), still save and close
            if acc:
                _STORE.add_message(ws_id, "assistant", "".join(acc))
                yield f"event: citations\ndata: {{}}\n\n"
        except Exception as e:
            err = str(e)
            yield f"event: error\ndata: {json.dumps({'error': err})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


class ChatIn(BaseModel):
    message: str


@router.post("/workspaces/{ws_id}/chat/visual")
async def chat_visual(ws_id: int, body: ChatIn):
    """JSON variant for PDF 'see the page' questions: answer via the vision model,
    returns {answer, citations, annotations, page}."""
    if not _STORE.get_workspace(ws_id):
        raise HTTPException(404, "workspace not found")
    query = body.message
    _STORE.add_message(ws_id, "user", query)
    raw_history = _STORE.list_messages(ws_id)
    clean_history = []
    for m in raw_history[:-1]:
        if m["role"] == "assistant" and "|||CITATIONS|||" in m["content"]:
            clean_history.append({"role": "assistant", "content": m["content"].split("|||CITATIONS|||")[0]})
        else:
            clean_history.append(m)
    try:
        from backend.grounding.visual import VisualPipeline
        vp = VisualPipeline(_STORE, _CFG)
        result = await asyncio.to_thread(vp.answer, query, ws_id, clean_history)
        cmap = result.get("citations", {})
        _STORE.add_message(ws_id, "assistant", result["answer"] + "|||CITATIONS|||" + json.dumps(cmap))
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/config")
def get_config():
    from dataclasses import asdict
    return asdict(_CFG)


@router.patch("/config")
def patch_config(body: ConfigIn):
    from dataclasses import asdict
    global _CFG
    d = asdict(_CFG)
    for k, v in body.model_dump(exclude_none=True).items():
        if v:
            d[k].update(v)
    # reload into a new AppConfig and persist
    from backend.config import _coerce
    _CFG = _coerce(d)
    save_config(_CFG)
    return asdict(_CFG)


@router.get("/health")
def health():
    return {"status": "ok"}


def build_app(store: Store, cfg: AppConfig) -> FastAPI:
    global _STORE, _CFG
    _STORE = store
    _CFG = cfg
    app = FastAPI(title="OpenNotebook")
    app.include_router(router)
    return app

if __name__ == "__main__":
    import tempfile, os
    from fastapi.testclient import TestClient
    from backend.store import Store
    from backend.config import load_config
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    store = Store(db); store.init()
    cfg = load_config()
    app = build_app(store, cfg)
    c = TestClient(app)
    
    r = c.post("/api/workspaces", json={"title": "L3"})
    assert r.status_code == 200 and r.json()["title"] == "L3"
    ws_id = r.json()["id"]
    # creating a workspace must also create its backing Course + Unit
    assert "course_id" in r.json() and "unit_id" in r.json()
    ws_json = c.get(f"/api/workspaces/{ws_id}").json()
    assert ws_json["unit"]["id"] == r.json()["unit_id"]
    assert ws_json["course"]["title"] == "L3"
    assert len(c.get("/api/workspaces").json()) == 1

    assert "llm" in c.get("/api/config").json()
    
    r2 = c.post(f"/api/workspaces/{ws_id}/sources", json={"url": "https://youtu.be/vid123"})
    assert r2.status_code == 200 and "source_id" in r2.json()
    src_id = r2.json()["source_id"]
    # the source auto-attaches to the workspace's own unit
    unit_id = r.json()["unit_id"]
    assert [s["id"] for s in c.get(f"/api/units/{unit_id}").json()["sources"]] == [src_id]
    # and the workspace overview includes unit/course context
    assert c.get(f"/api/workspaces/{ws_id}").json()["sources"][0]["unit_id"] == unit_id

    # stand-alone course + unit CRUD + assignment
    r3 = c.post("/api/courses", json={"title": "Stats"})
    assert r3.status_code == 200
    course2 = r3.json()["id"]
    assert len(c.get("/api/courses").json()) == 2
    ru = c.post(f"/api/courses/{course2}/units", json={"title": "Unit B"})
    assert ru.status_code == 200
    unit2 = ru.json()["id"]
    assert c.get(f"/api/courses/{course2}").json()["units"][0]["id"] == unit2

    ra = c.post(f"/api/units/{unit2}/sources/{src_id}")
    assert ra.status_code == 200
    assert [s["id"] for s in c.get(f"/api/units/{unit2}").json()["sources"]] == [src_id]
    # deleting the unit clears the source's unit_id
    assert c.delete(f"/api/units/{unit2}").status_code == 200
    assert c.get(f"/api/workspaces/{ws_id}").json()["sources"][0]["unit_id"] is None

    # 404 guards
    assert c.get("/api/courses/99999").status_code == 404
    assert c.get("/api/units/99999").status_code == 404

    # `_wrap_ingest` runs the CED path after a source becomes ready. Feed a real
    # AP source (course title matches the CED manifest), patch the embedder so the
    # coverage pass stays cheap, and confirm objectives get imported at ready-time.
    from unittest.mock import patch
    ap_course = _STORE.add_course("AP Statistics")
    ap_unit = _STORE.add_unit(ap_course, "Unit 1: Exploring One-Variable Data")
    ap_src = _STORE.add_source(ws_id, "pdf", "1A.4 AP Statistics Notes", None, None, unit_id=ap_unit)
    _STORE.set_source_status(ap_src, "ready")
    assert len(_STORE.list_unit_objectives(ap_unit, source_type="ced_import")) == 0
    with patch("backend.grounding.embedder.get_embedder") as mock_emb:
        from backend.grounding.embedder import _HashingEmbedder
        mock_emb.return_value = _HashingEmbedder()
        result = _wrap_ingest(ap_src, lambda: None)  # no-op ingest, source already ready
    assert result["detected"] is True and result["pipeline"] == "ced_import"
    assert len(_STORE.list_unit_objectives(ap_unit, source_type="ced_import")) == 11
    assert _STORE.get_course(ap_course)["exam_type"] == "AP"
    # and when the source never became ready, nothing runs
    bad = _STORE.add_source(ws_id, "pdf", "x", None, None)
    _STORE.set_source_status(bad, "failed")
    assert _wrap_ingest(bad, lambda: None)["reason"].startswith("status")

    # study guide: POST generates + persists a guide from the workspace's
    # unit objectives (LLM + embedder mocked); GET returns the latest guide.
    from unittest.mock import MagicMock
    import backend.study.guide as _guide_mod
    ws_unit = _STORE.get_workspace(ws_id)["unit_id"]
    guide_src = _STORE.add_source(ws_id, "pdf", "Calc Notes", None, None, unit_id=ws_unit)
    _STORE.set_source_status(guide_src, "ready")
    for i, t in enumerate([
        "To describe a distribution of a quantitative variable, report its shape, center, and spread.",
        "The median is the midpoint of the data; the mean is the arithmetic average.",
        "Standard deviation measures how spread out values are around the mean.",
    ]):
        _STORE.add_chunk(guide_src, i, t, i * 60, i * 60 + 30, 25, _HashingEmbedder().embed(t))
    _STORE.add_learning_objective(ws_unit, "Describe the distribution of a quantitative variable",
                                  source_type="ced_import", ced_unit_number=1, ced_topic_number=6,
                                  ced_skill_code="VAR-1.A")
    _STORE.add_learning_objective(ws_unit, "Summarize a quantitative variable with center and spread",
                                  source_type="ced_import", ced_unit_number=1, ced_topic_number=7,
                                  ced_skill_code="VAR-1.B")
    assert _STORE.get_latest_study_guide(ws_id) is None
    fake_llm = MagicMock()
    fake_llm.complete.return_value = "Summarize center and spread clearly. [1]"
    with patch("backend.grounding.embedder.get_embedder") as _emb, \
         patch.object(_guide_mod, "get_llm") as _llm:
        _emb.return_value = _HashingEmbedder()
        _llm.return_value = fake_llm
        rg = c.post(f"/api/workspaces/{ws_id}/study-guide")
    assert rg.status_code == 200 and rg.json()["guide"] is not None
    assert len(rg.json()["sections"]) == 2
    assert all(sec["body"] for sec in rg.json()["sections"])
    assert rg.json()["sections"][0]["skill_code"] == "VAR-1.A"  # CED skill code surfaced
    assert isinstance(rg.json()["uncovered"], int)  # gap signal surfaced alongside the guide
    rg2 = c.get(f"/api/workspaces/{ws_id}/study-guide")
    assert rg2.status_code == 200 and rg2.json()["guide"]["id"] == rg.json()["guide"]["id"]
    assert rg2.json()["uncovered"] == rg.json()["uncovered"]
    assert all(s.get("covered") is not None for s in rg2.json()["sections"])
    assert len(c.get(f"/api/workspaces/{ws_id}/objectives").json()["objectives"]) == 2

    # streaming guide generation: sections arrive one at a time over SSE, then a
    # final 'done' with the persisted full guide.
    with patch("backend.grounding.embedder.get_embedder") as _emb, \
         patch.object(_guide_mod, "get_llm") as _llm:
        _emb.return_value = _HashingEmbedder()
        _llm.return_value = fake_llm
        rstream = c.get(f"/api/workspaces/{ws_id}/study-guide/stream")
    assert rstream.status_code == 200
    events, cur = [], None
    for line in rstream.text.splitlines():
        if not line:
            continue
        if line.startswith("event:"):
            cur = line[6:].strip()
        elif line.startswith("data:"):
            events.append((cur, json.loads(line[5:].strip())))
    by_event = {}
    for name, data in events:
        by_event.setdefault(name, []).append(data)
    assert "meta" in by_event and by_event["meta"][0]["objectives"] == 2
    assert len(by_event["section"]) == 2, "one section event per objective"
    # section events arrive in index order 1,2 with the same objective count
    assert [s["index"] for s in by_event["section"]] == [1, 2]
    assert all(s["total"] == 2 for s in by_event["section"])
    assert "done" in by_event
    done_guide = by_event["done"][0]["guide"]
    assert done_guide and done_guide["id"] == by_event["done"][0]["sections"][0]["guide_id"]
    assert by_event["done"][0]["uncovered"] == rg.json()["uncovered"]

    print("api OK")
