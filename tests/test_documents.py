"""Coverage for the document picker: DOCS_DIR is a read-only input volume
(ARCHITECTURE.md §0 spirit), so listing status must be re-derivable purely by
re-reading DOCS_DIR + the artifact store -- no owned index in between."""

import importlib
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.store.artifact_store import ArtifactStore
from app.store.documents import list_documents
from tests.fixtures.make_test_pdf import build

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "standard_sample.pdf"


@pytest.fixture(scope="module")
def sample_pdf_bytes():
    if not FIXTURE_PDF.exists():
        build(FIXTURE_PDF)
    return FIXTURE_PDF.read_bytes()


def test_list_documents_reports_not_ready_before_processing(tmp_path, sample_pdf_bytes):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a.pdf").write_bytes(sample_pdf_bytes)
    store = ArtifactStore(tmp_path / "artifacts")

    entries = list_documents(docs_dir, store)

    assert len(entries) == 1
    assert entries[0].filename == "a.pdf"
    assert entries[0].ready is False


def test_list_documents_reports_ready_after_processing(tmp_path, sample_pdf_bytes):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a.pdf").write_bytes(sample_pdf_bytes)
    store = ArtifactStore(tmp_path / "artifacts")

    from app.pipeline.run import process_pdf
    process_pdf(sample_pdf_bytes, store)

    entries = list_documents(docs_dir, store)
    assert entries[0].ready is True


def test_list_documents_finds_nested_pdfs(tmp_path, sample_pdf_bytes):
    docs_dir = tmp_path / "docs"
    (docs_dir / "sub").mkdir(parents=True)
    (docs_dir / "sub" / "b.pdf").write_bytes(sample_pdf_bytes)
    store = ArtifactStore(tmp_path / "artifacts")

    entries = list_documents(docs_dir, store)
    assert entries[0].relative_path == "sub/b.pdf"


@pytest.fixture
def api_client(tmp_path, monkeypatch, sample_pdf_bytes):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    shutil.copy(FIXTURE_PDF, docs_dir / "standard_sample.pdf")

    monkeypatch.setenv("ARTIFACT_STORE_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setenv("DOCS_DIR", str(docs_dir))
    sys.modules.pop("app.api", None)
    api_module = importlib.import_module("app.api")
    return TestClient(api_module.app)


def test_picker_page_lists_the_document(api_client):
    resp = api_client.get("/")
    assert resp.status_code == 200
    assert "standard_sample.pdf" in resp.text
    assert "not processed" in resp.text


def test_documents_json_endpoint(api_client):
    resp = api_client.get("/documents")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["documents"]) == 1
    assert body["documents"][0]["ready"] is False


def test_parse_document_by_relative_path_then_picker_shows_ready(api_client):
    resp = api_client.post("/documents/standard_sample.pdf/parse", follow_redirects=False)
    assert resp.status_code == 303

    picker = api_client.get("/")
    assert "not processed" not in picker.text
    assert "view extraction" in picker.text


def _parse_and_key(api_client):
    api_client.post("/documents/standard_sample.pdf/parse", follow_redirects=False)
    return api_client.get("/documents").json()["documents"][0]["key"]


def test_evaluator_ui_route_serves_the_page(api_client):
    """The accuracy-evaluator route is now a thin shell: it only checks
    readiness and serves the template (which fetches /editions/{key} itself)."""
    key = _parse_and_key(api_client)
    resp = api_client.get(f"/editions/{key}/ui")
    assert resp.status_code == 200
    # the template carries the key for the client fetch and is the evaluator page
    assert f'data-key="{key}"' in resp.text
    assert "Accuracy evaluator" in resp.text


def test_evaluator_ui_not_ready_is_202(api_client):
    resp = api_client.get("/editions/deadbeef+0.0.0/ui")
    assert resp.status_code == 202


def test_edition_json_exposes_fields_the_evaluator_needs(api_client):
    """The client renders entirely from GET /editions/{key}; guard that the
    fields it reads stay present: page images, page_sizes/raster_dpi for the
    bbox overlay transform, and per-node consensus/parsers on the tree."""
    key = _parse_and_key(api_client)
    body = api_client.get(f"/editions/{key}").json()
    assert body["page_image_urls"]
    pp = body["pipeline_provenance"]
    assert "page_sizes" in pp and "raster_dpi" in pp

    def walk(n, acc):
        acc.append(n)
        for c in n.get("children", []):
            walk(c, acc)
    nodes = []
    walk(body["root"], nodes)
    assert len(nodes) > 1
    for n in nodes:
        assert "consensus" in n and "parsers" in n and "xrefs" in n
        assert "bbox" in n["provenance"] and "page" in n["provenance"]


def test_parse_document_rejects_path_traversal(api_client):
    resp = api_client.post("/documents/../../etc/passwd/parse")
    assert resp.status_code in (400, 404)


def test_parse_document_missing_file_404s(api_client):
    resp = api_client.post("/documents/does-not-exist.pdf/parse")
    assert resp.status_code == 404
