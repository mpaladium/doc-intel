from pathlib import Path

from app.cli.evaluate_dir import run
from app.store.artifact_store import ArtifactStore
from app.store.documents import list_documents
from tests.fixtures.make_test_pdf import build

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "standard_sample.pdf"


def _ensure_fixture() -> bytes:
    if not FIXTURE_PDF.exists():
        build(FIXTURE_PDF)
    return FIXTURE_PDF.read_bytes()


def test_run_processes_all_pdfs_and_is_idempotent(tmp_path):
    pdf_bytes = _ensure_fixture()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "one.pdf").write_bytes(pdf_bytes)
    (docs_dir / "two.pdf").write_bytes(pdf_bytes)
    store = ArtifactStore(tmp_path / "artifacts")

    failed = run(docs_dir, store, workers=1)
    assert failed == 0

    entries = list_documents(docs_dir, store)
    assert all(e.ready for e in entries)
    assert len(entries) == 2

    # Same content twice -> same key -> both files converge on one artifact.
    assert entries[0].key == entries[1].key

    # Re-running is a fast no-op, not a re-process.
    failed_again = run(docs_dir, store, workers=1)
    assert failed_again == 0


def test_run_on_empty_directory_is_a_noop(tmp_path):
    docs_dir = tmp_path / "empty"
    docs_dir.mkdir()
    store = ArtifactStore(tmp_path / "artifacts")

    failed = run(docs_dir, store, workers=1)
    assert failed == 0
