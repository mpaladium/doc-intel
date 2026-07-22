"""FastAPI surface for ingestion-engine. Stateless handlers only
(TECHSTACK.md): every request either computes the content-address key and
defers to the artifact store, or runs the pipeline and writes to it. No
session, no job table, no server-side workflow state -- any replica can serve
any request (ARCHITECTURE.md §0).

    GET  /                                          -> document picker (all PDFs under DOCS_DIR)
    GET  /documents                                 -> JSON listing, same data as the picker
    POST /documents/{relative_path}/parse            -> parse one DOCS_DIR file on demand
    POST /parse                 body: PDF bytes     -> 202 Location: /editions/{hash}
    GET  /editions/{hash}                            -> 202 (not yet) | 200 CanonicalEdition
    GET  /editions/{hash}/pages/{page_no}.png        -> rasterized page image
    GET  /editions/{hash}/ui                         -> confidence-sorted verification inspector

DOCS_DIR is a read-only input volume, the same way the artifact store is a
dumb output sink (ARCHITECTURE.md §0): nothing about "which files exist" or
"which are processed" is cached or owned here. `app/store/documents.py`
re-derives the listing on every request by re-reading DOCS_DIR and checking
the artifact store -- any replica gets the same answer with no shared
in-memory state. For evaluating many documents at once, see
`app/cli/evaluate_dir.py` / `scripts/evaluate_dir.sh`, which write to the same
artifact store out-of-process so results just show up here once done.

This iteration runs the pipeline inside the request handler rather than a
separate worker (no worker/queue split yet -- ARCHITECTURE.md's stateless
design doesn't require one, it's a throughput optimization for later, not a
correctness requirement now), but it's offloaded to a thread and bounded by
a concurrency limit so it doesn't block the event loop or oversubscribe a
single shared GPU/CPU -- see `_parse_semaphore` below and
`app/pipeline/extract_docling.py`'s device/thread selection.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.pipeline.extract_docling import get_converter
from app.pipeline.run import process_pdf
from app.store.artifact_store import ArtifactStore, compute_key
from app.store.documents import list_documents
from app.version import PIPELINE_VERSION

log = logging.getLogger("ingestion_api")
log.setLevel(logging.INFO)
if not log.handlers:
    # Uvicorn configures its own loggers but not ours; without a handler
    # here, log.info() below would be silently dropped rather than shown.
    log.addHandler(logging.StreamHandler())

ARTIFACT_STORE_PATH = os.environ.get("ARTIFACT_STORE_PATH", "./data/artifacts")
store = ArtifactStore(ARTIFACT_STORE_PATH)

DOCS_DIR = Path(os.environ.get("DOCS_DIR", "./data/docs")).resolve()
DOCS_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "ui" / "templates"))

# Single-process stand-in for the full Redis VRAM lease (AGENTS.md §3, still
# deferred): bounds how many pipeline runs execute at once so this process
# never asks a shared GPU (or a Mac's CPU/MPS) for more concurrent heavy-model
# work than it can hold resident at a time. Default 1 = "one document at a
# time" (the safe default for a single-GPU box); raise it on a machine with
# more VRAM/CPU headroom. The batch CLI (app/cli/evaluate_dir.py) has its own,
# separate limit -- running both at once on a single GPU can still
# oversubscribe it; see README's resource-usage notes.
MAX_CONCURRENT_PARSES = int(os.environ.get("INGESTION_MAX_CONCURRENT_PARSES", "1"))
_parse_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PARSES)


def _corroborator_config() -> str:
    """Which N-version corroborators this process is CONFIGURED to use.

    Deliberately reads env only and never calls `engine.available()`: on the
    in-process backend that would load GLM-OCR's weights at startup, spending
    VRAM even for documents with no equations. Reachability is what
    `scripts/start_stack.sh` verifies up front; the engines' own lazy-load lines
    ("GLM-OCR via ollama at ..." / "GLM-OCR loaded in-process on cuda") confirm
    which backend actually initialized, on first use."""
    from app.pipeline.extract_docling import resolved_ocr_engine

    parts = []
    parts.append(f"docling_ocr_engine={resolved_ocr_engine()}")
    if os.environ.get("INGESTION_GLM_OCR", "1").lower() in ("0", "false", "no"):
        parts.append("glm_ocr=off")
    elif (url := os.environ.get("INGESTION_GLM_OCR_URL")):
        model = os.environ.get("INGESTION_GLM_OCR_MODEL", "glm-ocr")
        parts.append(f"glm_ocr=ollama({url}, {model})")
    else:
        parts.append("glm_ocr=in-process")
    parts.append(f"mineru={os.environ.get('INGESTION_MINERU_URL') or 'unset'}")
    parts.append(f"surya={os.environ.get('INGESTION_SURYA_URL') or 'unset'}")
    return "  ".join(parts)


@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI):
    """Load Docling's model weights once at startup (not on the first
    request) so they're resident before traffic arrives, and so a
    device/config problem fails fast instead of on a user's first request."""
    from docling.datamodel.base_models import InputFormat

    converter = get_converter(False)
    accel = converter.format_to_options[InputFormat.PDF].pipeline_options.accelerator_options
    log.info("ingestion-engine ready: docs_dir=%s device=%s num_threads=%s max_concurrent_parses=%s",
              DOCS_DIR, accel.device, accel.num_threads, MAX_CONCURRENT_PARSES)
    log.info("corroborator engines: %s", _corroborator_config())
    yield


app = FastAPI(title="ingestion-engine", version=PIPELINE_VERSION, lifespan=_lifespan)


def _resolve_in_docs_dir(relative_path: str) -> Path:
    """Resolves a URL-supplied relative path against DOCS_DIR, rejecting
    anything that escapes it (`..` traversal, symlink tricks, absolute paths)."""
    path = (DOCS_DIR / relative_path).resolve()
    if path != DOCS_DIR and DOCS_DIR not in path.parents:
        raise HTTPException(status_code=400, detail="invalid path")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    return path


@app.get("/", response_class=HTMLResponse)
def document_picker(request: Request):
    entries = list_documents(DOCS_DIR, store)
    return templates.TemplateResponse(request, "documents.html", {
        "docs_dir": str(DOCS_DIR),
        "entries": entries,
    })


@app.get("/documents")
def documents_json():
    entries = list_documents(DOCS_DIR, store)
    return {"docs_dir": str(DOCS_DIR), "documents": [e.__dict__ for e in entries]}


@app.post("/documents/{relative_path:path}/parse")
async def parse_document(relative_path: str):
    path = _resolve_in_docs_dir(relative_path)
    pdf_bytes = path.read_bytes()
    async with _parse_semaphore:
        result = await run_in_threadpool(process_pdf, pdf_bytes, store)
    if result.status == "quarantined":
        raise HTTPException(status_code=422, detail=f"quarantined: {result.cause}")
    return RedirectResponse(url="/", status_code=303)


@app.post("/parse", status_code=202)
async def parse(request: Request, response: Response):
    pdf_bytes = await request.body()

    # Fast pre-check outside the semaphore: an already-processed lookup
    # shouldn't queue behind a slow in-flight parse of a different document.
    precheck_key = compute_key(pdf_bytes, PIPELINE_VERSION)
    if store.exists(precheck_key):
        response.status_code = 200
        response.headers["Location"] = f"/editions/{precheck_key}"
        return {"edition_id": precheck_key, "status": "already_processed"}

    async with _parse_semaphore:
        result = await run_in_threadpool(process_pdf, pdf_bytes, store)

    if result.status == "quarantined":
        raise HTTPException(status_code=422, detail=f"quarantined: {result.cause}")

    location = f"/editions/{result.key}"
    response.headers["Location"] = location
    response.status_code = 200 if result.status == "already_processed" else 202
    return {"edition_id": result.key, "status": result.status}


@app.get("/editions/{key}")
def get_edition(key: str):
    edition = store.get_edition(key)
    if edition is None:
        return JSONResponse(status_code=202, content={"status": "not_ready"})
    page_numbers = store.list_page_numbers(key)
    body = edition.model_dump(mode="json")
    body["page_image_urls"] = [f"/editions/{key}/pages/{n}.png" for n in page_numbers]
    return body


@app.get("/editions/{key}/pages/{page_no}.png")
def get_page_image(key: str, page_no: int):
    path = store.page_image_path(key, page_no)
    if path is None:
        raise HTTPException(status_code=404, detail="page image not found")
    return Response(content=path.read_bytes(), media_type="image/png")


@app.get("/editions/{key}/ui", response_class=HTMLResponse)
def inspector_ui(request: Request, key: str):
    """The visual accuracy evaluator. This route only checks readiness and hands
    the template the `key`; the page itself fetches `GET /editions/{key}` (the
    full post-consensus CanonicalEdition JSON, incl. page_image_urls and
    pipeline_provenance.page_sizes/raster_dpi) and renders the source<->canonical
    linking, section map, and document graph entirely client-side."""
    if store.get_edition(key) is None:
        raise HTTPException(status_code=202, detail="not ready")
    return templates.TemplateResponse(request, "inspector.html", {"key": key})
