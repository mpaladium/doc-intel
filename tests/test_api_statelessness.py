"""Statelessness contract (ARCHITECTURE.md §0): POST /parse is idempotent and
content-addressed; a fresh ArtifactStore instance pointed at the same volume
(standing in for "a different replica picks up the request") sees exactly what
the first one wrote, with no in-memory/job-table state required."""

import hashlib
import importlib
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACT_STORE_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setenv("DOCS_DIR", str(tmp_path / "docs"))
    sys.modules.pop("app.api", None)
    api_module = importlib.import_module("app.api")
    return TestClient(api_module.app), api_module


@pytest.fixture(scope="module")
def sample_pdf():
    from tests.fixtures.make_test_pdf import build
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "sample.pdf"
        build(path)
        yield path.read_bytes()


def test_repeat_parse_is_idempotent_and_converges_on_same_key(client, sample_pdf):
    test_client, _ = client

    first = test_client.post("/parse", content=sample_pdf)
    assert first.status_code == 202
    key = first.json()["edition_id"]
    assert first.headers["location"] == f"/editions/{key}"

    second = test_client.post("/parse", content=sample_pdf)
    assert second.status_code == 200  # already processed -- no reprocessing
    assert second.json()["edition_id"] == key


def test_a_second_process_reading_the_same_volume_sees_the_artifact(client, sample_pdf, tmp_path, monkeypatch):
    test_client, _ = client
    resp = test_client.post("/parse", content=sample_pdf)
    key = resp.json()["edition_id"]

    # Simulate a second stateless replica: fresh module import, fresh ArtifactStore
    # instance, same underlying volume -- no shared in-memory state at all.
    sys.modules.pop("app.api", None)
    replica_module = importlib.import_module("app.api")
    replica_client = TestClient(replica_module.app)

    got = replica_client.get(f"/editions/{key}")
    assert got.status_code == 200
    assert got.json()["edition_id"] == key


def test_hash_is_deterministic_from_content_before_processing(sample_pdf):
    from app.store.artifact_store import compute_key
    from app.version import PIPELINE_VERSION

    expected = hashlib.sha256(sample_pdf).hexdigest() + "+" + PIPELINE_VERSION
    assert compute_key(sample_pdf, PIPELINE_VERSION) == expected
