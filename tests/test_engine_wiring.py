"""Corroborator wiring (assemble._apply_equation_corroboration /
_backfill_ocr_candidates) with the engines monkeypatched -- the wiring contract,
independent of the heavy models. GLM-OCR is in-process; MinerU/Surya are
out-of-process HTTP sidecars, so their real path is covered by a live local
HTTP server test rather than a cached-weights smoke test."""

import json
import os
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import canonical_schema as cs
from app.pipeline import assemble as asm
from app.pipeline.engines import _sidecar, glm_ocr, mineru, surya
from app.pipeline.triage import TriageResult


def _prov(page=1, bbox=(50, 700, 300, 680)):
    return cs.Provenance(page=page, bbox=bbox, parser="docling",
                         model_version="v", confidence=0.9)


def _node(**kw):
    kw.setdefault("id", "n")
    kw.setdefault("type", "paragraph")
    kw.setdefault("provenance", _prov())
    return cs.Node(**kw)


def _pdf_bytes() -> bytes:
    import fitz
    doc = fitz.open()
    doc.new_page()
    return doc.tobytes()


def _patch_engine(monkeypatch, formula=None, text=None, avail=True):
    monkeypatch.setattr(glm_ocr, "available", lambda: avail)
    monkeypatch.setattr(glm_ocr, "recognize_formula", lambda img: formula)
    monkeypatch.setattr(glm_ocr, "recognize_text", lambda img: text)


# --- equation corroboration -----------------------------------------------------

def test_equation_agreement_stays_unanimous(monkeypatch):
    # transcription variants (\text vs \mathrm, $$/tag wrappers) agree
    _patch_engine(monkeypatch, formula="$$\nN _ {\\mathrm {d}} = 2 B \\tag {1}\n$$")
    eq = _node(id="e", type="equation", latex=r"N _ {\text {d}} = 2 \ B")
    root = _node(id="r", type="section", children=[eq])
    out = asm._apply_equation_corroboration(root, _pdf_bytes())
    e = out.children[0]
    assert e.parsers["glm_ocr"]  # candidate recorded
    assert e.consensus == "unanimous"


def test_equation_genuine_disagreement_quarantines_with_both_candidates(monkeypatch):
    # fraktur-vs-roman is a real glyph difference -> route to a human
    _patch_engine(monkeypatch, formula=r"a _ {\mathrm {c}} = w")
    eq = _node(id="e", type="equation", latex=r"a _ {\mathfrak {c}} = w")
    root = _node(id="r", type="section", children=[eq])
    out = asm._apply_equation_corroboration(root, _pdf_bytes())
    e = out.children[0]
    assert e.consensus == "quarantined"
    assert e.latex == r"a _ {\mathfrak {c}} = w"          # authority NOT overwritten
    assert "glm_ocr" in e.parsers                          # loser kept
    assert "equation_engine_disagreement" in e.review_reasons


def test_equation_pass_noop_when_engine_unavailable(monkeypatch):
    _patch_engine(monkeypatch, avail=False)
    eq = _node(id="e", type="equation", latex="x = y")
    root = _node(id="r", type="section", children=[eq])
    out = asm._apply_equation_corroboration(root, _pdf_bytes())
    assert out.children[0].parsers == {}
    assert out.children[0].consensus == "unanimous"


# --- scanned-page OCR candidates -------------------------------------------------

def _tc(page_class):
    return TriageResult(page_class=page_class, char_count=0, garbage_ratio=0.0,
                        cid_ratio=0.0, long_run_ratio=0.0)


def _scanned_classes(n=1):
    return [_tc("SCANNED") for _ in range(n)]


def test_ocr_candidates_recorded_on_scanned_pages(monkeypatch):
    _patch_engine(monkeypatch, text="10 V/m limit")
    n = _node(id="p", text="1O V/m limit")  # RapidOCR digit/letter confusable
    root = _node(id="r", type="section", children=[n])
    out = asm._backfill_ocr_candidates(root, _pdf_bytes(), _scanned_classes())
    p = out.children[0]
    assert p.parsers["rapidocr"] == "1O V/m limit"
    assert p.parsers["glm_ocr"] == "10 V/m limit"


def test_ocr_consensus_votes_between_engines(monkeypatch):
    # after backfill, text consensus votes: glm_ocr is the OCR-lane authority,
    # rapidocr the corroborator; disagreement on a normative node quarantines
    _patch_engine(monkeypatch, text="10 V/m")
    n = _node(id="p", text="1O V/m", cdm_type="Requirement")
    root = _node(id="r", type="section", children=[n])
    out = asm._backfill_ocr_candidates(root, _pdf_bytes(), _scanned_classes())
    out = asm._apply_text_consensus(out)
    assert out.children[0].consensus == "quarantined"
    assert "consensus_dissent" in out.children[0].review_reasons


def test_ocr_derived_parameter_quarantined_by_default(monkeypatch):
    _patch_engine(monkeypatch, avail=False)  # even with no second engine
    p = cs.Parameter(name="E", value=Decimal("10"), unit="V/m", comparator="gte")
    n = _node(id="p", text="shall be >= 10 V/m", parameters=[p])
    root = _node(id="r", type="section", children=[n])
    out = asm._backfill_ocr_candidates(root, _pdf_bytes(), _scanned_classes())
    q = out.children[0]
    assert q.consensus == "quarantined"
    assert "ocr_derived_parameter" in q.review_reasons


def test_digital_pages_untouched(monkeypatch):
    _patch_engine(monkeypatch, text="anything")
    n = _node(id="p", text="born digital")
    root = _node(id="r", type="section", children=[n])
    out = asm._backfill_ocr_candidates(root, _pdf_bytes(), [_tc("DIGITAL_CLEAN")])
    assert out.children[0].parsers == {}


# --- multi-engine equation consensus (MinerU alongside GLM-OCR) -----------------

def _patch_eq(monkeypatch, engine, formula, avail=True):
    monkeypatch.setattr(engine, "available", lambda: avail)
    monkeypatch.setattr(engine, "recognize_formula", lambda img: formula)


def test_three_way_equation_agreement_stays_unanimous(monkeypatch):
    # docling (authority) + glm_ocr + mineru all agree (variants folded)
    _patch_eq(monkeypatch, glm_ocr, r"N _ {\mathrm {d}} = 2 B")
    _patch_eq(monkeypatch, mineru, "$$\nN _ {\\text {d}} = 2 \\ B \\tag {1}\n$$")
    eq = _node(id="e", type="equation", latex=r"N _ {\text {d}} = 2 \ B")
    root = _node(id="r", type="section", children=[eq])
    out = asm._apply_equation_corroboration(root, _pdf_bytes())
    e = out.children[0]
    assert e.parsers["glm_ocr"] and e.parsers["mineru"]  # both candidates recorded
    assert e.consensus == "unanimous"


def test_mineru_disagreement_quarantines_with_all_candidates(monkeypatch):
    # glm_ocr corroborates docling; mineru is the lone genuine dissent -> quarantine
    _patch_eq(monkeypatch, glm_ocr, r"a _ {\mathfrak {c}} = w")
    _patch_eq(monkeypatch, mineru, r"a _ {\mathrm {c}} = w")  # roman vs fraktur
    eq = _node(id="e", type="equation", latex=r"a _ {\mathfrak {c}} = w")
    root = _node(id="r", type="section", children=[eq])
    out = asm._apply_equation_corroboration(root, _pdf_bytes())
    e = out.children[0]
    assert e.consensus == "quarantined"
    assert e.latex == r"a _ {\mathfrak {c}} = w"            # authority NOT overwritten
    assert e.parsers["glm_ocr"] and e.parsers["mineru"]     # every candidate kept
    assert "mineru" in e.quarantine_reason                  # the dissenter is named
    assert "glm_ocr" not in e.quarantine_reason             # the agreeing engine is not
    assert "equation_engine_disagreement" in e.review_reasons


def test_equation_lane_uses_only_available_engines(monkeypatch):
    # mineru unavailable (no sidecar) -> lane behaves as the glm_ocr-only case
    _patch_eq(monkeypatch, glm_ocr, "x = y")
    _patch_eq(monkeypatch, mineru, "should-not-be-called", avail=False)
    eq = _node(id="e", type="equation", latex="x = y")
    root = _node(id="r", type="section", children=[eq])
    out = asm._apply_equation_corroboration(root, _pdf_bytes())
    assert "mineru" not in out.children[0].parsers
    assert out.children[0].parsers["glm_ocr"] == "x = y"
    assert out.children[0].consensus == "unanimous"


# --- multi-engine OCR lane (Surya alongside GLM-OCR) ----------------------------

def test_surya_candidate_recorded_alongside_glm_ocr(monkeypatch):
    _patch_engine(monkeypatch, text="10 V/m limit")            # glm_ocr
    monkeypatch.setattr(surya, "available", lambda: True)
    monkeypatch.setattr(surya, "recognize_text", lambda img: "1O V/m limit")  # confusable
    n = _node(id="p", text="1O V/m limit")
    root = _node(id="r", type="section", children=[n])
    out = asm._backfill_ocr_candidates(root, _pdf_bytes(), _scanned_classes())
    p = out.children[0]
    assert p.parsers["rapidocr"] == "1O V/m limit"
    assert p.parsers["glm_ocr"] == "10 V/m limit"
    assert p.parsers["surya"] == "1O V/m limit"


def test_surya_dissent_on_normative_node_quarantines(monkeypatch):
    # glm_ocr (OCR-lane authority) vs surya disagree on a normative node
    _patch_engine(monkeypatch, text="10 V/m")                  # glm_ocr authority
    monkeypatch.setattr(surya, "available", lambda: True)
    monkeypatch.setattr(surya, "recognize_text", lambda img: "1O V/m")
    n = _node(id="p", text="1O V/m", cdm_type="Requirement")
    root = _node(id="r", type="section", children=[n])
    out = asm._backfill_ocr_candidates(root, _pdf_bytes(), _scanned_classes())
    out = asm._apply_text_consensus(out)
    assert out.children[0].consensus == "quarantined"
    assert "consensus_dissent" in out.children[0].review_reasons


# --- out-of-process sidecar client (real local HTTP server) ---------------------

class _StubSidecar(BaseHTTPRequestHandler):
    """Answers per the sidecar contract; behavior chosen by URL path so one
    server can exercise the ok / bad-json / missing-key / 500 branches."""
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))  # drain the PNG
        if self.path == "/ok-latex":
            body, code = json.dumps({"latex": "  E = m c^2  "}).encode(), 200
        elif self.path == "/ok-text":
            body, code = json.dumps({"text": "hello"}).encode(), 200
        elif self.path == "/missing-key":
            body, code = json.dumps({"oops": "x"}).encode(), 200
        elif self.path == "/bad-json":
            body, code = b"not json", 200
        elif self.path == "/json-list":
            body, code = json.dumps(["not", "an", "object"]).encode(), 200
        else:
            body, code = b"error", 500
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):  # keep the test output quiet
        pass


@pytest.fixture()
def sidecar_url():
    server = HTTPServer(("127.0.0.1", 0), _StubSidecar)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()


def _img():
    from PIL import Image
    return Image.new("RGB", (16, 16), "white")


def test_sidecar_post_image_returns_stripped_value(sidecar_url):
    import logging
    out = _sidecar.post_image(sidecar_url + "/ok-latex", _img(), "latex", logging.getLogger())
    assert out == "E = m c^2"  # stripped


@pytest.mark.parametrize("path", ["/missing-key", "/bad-json", "/json-list", "/500"])
def test_sidecar_failures_return_none(sidecar_url, path):
    import logging
    assert _sidecar.post_image(sidecar_url + path, _img(), "latex", logging.getLogger()) is None


def test_mineru_adapter_round_trips_via_sidecar(monkeypatch, sidecar_url):
    monkeypatch.setenv("INGESTION_MINERU_URL", sidecar_url + "/ok-latex")
    assert mineru.available() is True
    assert mineru.recognize_formula(_img()) == "E = m c^2"


def test_surya_adapter_round_trips_via_sidecar(monkeypatch, sidecar_url):
    monkeypatch.setenv("INGESTION_SURYA_URL", sidecar_url + "/ok-text")
    assert surya.available() is True
    assert surya.recognize_text(_img()) == "hello"


def test_sidecar_adapters_unavailable_without_url(monkeypatch):
    monkeypatch.delenv("INGESTION_MINERU_URL", raising=False)
    monkeypatch.delenv("INGESTION_SURYA_URL", raising=False)
    assert mineru.available() is False
    assert surya.available() is False
    assert mineru.recognize_formula(_img()) is None
    assert surya.recognize_text(_img()) is None


# --- OCR engine selection (INGESTION_OCR_ENGINE & docling-surya) ------------------

def test_default_ocr_engine_is_rapidocr(monkeypatch):
    monkeypatch.delenv("INGESTION_OCR_ENGINE", raising=False)
    from app.pipeline.extract_docling import resolved_ocr_engine
    # reset the lazy function by reloading the module
    import importlib
    import app.pipeline.extract_docling as edm
    importlib.reload(edm)
    assert edm.resolved_ocr_engine() == "rapidocr"


def test_surya_ocr_engine_falls_back_when_plugin_unavailable(monkeypatch, caplog):
    monkeypatch.setenv("INGESTION_OCR_ENGINE", "surya")
    from app.pipeline.extract_docling import resolved_ocr_engine, _surya_ocr_options
    import importlib
    import app.pipeline.extract_docling as edm
    importlib.reload(edm)
    # when the plugin is not installed, it should fall back and log
    assert edm.resolved_ocr_engine() == "rapidocr"
    assert "docling-surya" in caplog.text or "surya" in caplog.text.lower()


def test_surya_sidecar_excluded_when_primary_is_surya(monkeypatch):
    """Mutual exclusion: if Surya is Docling's OCR engine, the Surya sidecar must
    not run (assemble._ocr_text_engines drops it). The same model cannot be both
    the primary transcription and its own independent second opinion."""
    monkeypatch.setenv("INGESTION_OCR_ENGINE", "surya")
    # Patch the Surya plugin as available
    from app.pipeline.extract_docling import _surya_ocr_options
    def mock_surya_opts():
        try:
            from docling_surya import SuryaOcrOptions
        except ImportError:
            # If not installed, create a mock
            class SuryaOcrOptions:
                pass
            return SuryaOcrOptions()
        return SuryaOcrOptions()

    # Mock both the option loader and the primary engine resolver
    monkeypatch.setattr("app.pipeline.extract_docling._surya_ocr_options", mock_surya_opts)

    import importlib
    import app.pipeline.extract_docling as edm
    import app.pipeline.assemble as asm
    importlib.reload(edm)

    primary = edm.resolved_ocr_engine()
    if primary == "surya":  # only test mutual exclusion if Surya actually loaded
        engines = asm._ocr_text_engines(primary)
        engine_names = [e.ENGINE_NAME for e in engines]
        assert "surya" not in engine_names, "Surya sidecar should be excluded when it's the primary engine"


# --- opt-in real-inference smoke test (GLM-OCR, in-process) ----------------------

def _weights_cached() -> bool:
    from pathlib import Path
    return (Path.home() / ".cache/huggingface/hub/models--zai-org--GLM-OCR").exists()


@pytest.mark.skipif(not _weights_cached(), reason="GLM-OCR weights not cached")
def test_real_glm_ocr_formula_smoke(monkeypatch):
    """One real inference: a rendered formula image round-trips through the
    actual model. Slow (~10s incl. load); runs only where the weights exist."""
    monkeypatch.setenv("INGESTION_GLM_OCR", "1")
    # reset the lazy singleton so the env change takes effect
    glm_ocr._state.update({"tried": False, "model": None, "processor": None})
    import fitz
    from PIL import Image
    import io
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "E = m c 2", fontsize=18)
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=fitz.Rect(60, 80, 220, 115))
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    out = glm_ocr.recognize_formula(img)
    glm_ocr._state.update({"tried": False, "model": None, "processor": None})
    assert out and "E" in out and "m" in out

# --- GLM-OCR via an Ollama server ---------------------------------------------

class _StubOllama(BaseHTTPRequestHandler):
    """Mimics the two Ollama endpoints this engine uses."""
    models = ["glm-ocr:latest", "llama3:8b"]

    def do_GET(self):
        if self.path == "/api/tags":
            body = json.dumps({"models": [{"name": n} for n in self.models]}).encode()
            code = 200
        else:
            body, code = b"error", 404
        self.send_response(code); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        # the engine must send base64 images and pin determinism
        assert payload["images"] and isinstance(payload["images"][0], str)
        assert payload["stream"] is False
        assert payload["options"]["temperature"] == 0
        body = json.dumps({"response": f"  {payload['prompt']}|ok  "}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_):
        pass


@pytest.fixture()
def ollama_url():
    server = HTTPServer(("127.0.0.1", 0), _StubOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()


def _reset_glm():
    from app.pipeline.engines import glm_ocr
    glm_ocr._state.update({"tried": False, "model": None, "processor": None, "ollama": None})


def test_glm_ocr_uses_ollama_when_url_set(monkeypatch, ollama_url):
    """With INGESTION_GLM_OCR_URL set the engine must talk to Ollama and never
    import/load the in-process transformers model."""
    from app.pipeline.engines import glm_ocr
    monkeypatch.setenv("INGESTION_GLM_OCR", "1")   # conftest gates it off suite-wide
    monkeypatch.setenv("INGESTION_GLM_OCR_URL", ollama_url)
    monkeypatch.delenv("INGESTION_GLM_OCR_MODEL", raising=False)
    _reset_glm()
    try:
        assert glm_ocr.available() is True
        assert glm_ocr._state["model"] is None      # in-process path untouched
        assert glm_ocr.recognize_formula(_img()) == "Formula Recognition:|ok"
        assert glm_ocr.recognize_text(_img()) == "Text Recognition:|ok"
    finally:
        _reset_glm()


def test_glm_ocr_ollama_without_the_model_is_unavailable(monkeypatch, ollama_url):
    """A reachable Ollama that hasn't pulled glm-ocr must degrade to 'no
    candidate' -- one log line at startup, not a failure per equation."""
    from app.pipeline.engines import glm_ocr
    monkeypatch.setenv("INGESTION_GLM_OCR", "1")
    monkeypatch.setenv("INGESTION_GLM_OCR_URL", ollama_url)
    monkeypatch.setenv("INGESTION_GLM_OCR_MODEL", "not-pulled")
    _reset_glm()
    try:
        assert glm_ocr.available() is False
        assert glm_ocr.recognize_formula(_img()) is None
    finally:
        _reset_glm()


def test_glm_ocr_ollama_unreachable_is_unavailable(monkeypatch):
    from app.pipeline.engines import glm_ocr
    monkeypatch.setenv("INGESTION_GLM_OCR", "1")
    monkeypatch.setenv("INGESTION_GLM_OCR_URL", "http://127.0.0.1:1")  # nothing listening
    _reset_glm()
    try:
        assert glm_ocr.available() is False
    finally:
        _reset_glm()


def test_glm_ocr_gate_off_beats_ollama_url(monkeypatch, ollama_url):
    from app.pipeline.engines import glm_ocr
    monkeypatch.setenv("INGESTION_GLM_OCR_URL", ollama_url)
    monkeypatch.setenv("INGESTION_GLM_OCR", "0")
    _reset_glm()
    try:
        assert glm_ocr.available() is False
    finally:
        _reset_glm()


def test_ollama_url_accepts_root_or_full_endpoint():
    from app.pipeline.engines._ollama import _generate_url
    assert _generate_url("http://h:11434") == "http://h:11434/api/generate"
    assert _generate_url("http://h:11434/") == "http://h:11434/api/generate"
    assert _generate_url("http://h:11434/api/generate") == "http://h:11434/api/generate"
