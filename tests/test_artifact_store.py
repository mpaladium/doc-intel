from canonical_schema import CanonicalEdition, Node, Provenance
from app.store.artifact_store import ArtifactStore, compute_key


def _tiny_edition(edition_id: str) -> CanonicalEdition:
    root = Node(id="root", type="section", children=[],
                provenance=Provenance(page=1, bbox=(0, 0, 1, 1), parser="assemble",
                                      model_version="v1", confidence=1.0))
    return CanonicalEdition(edition_id=edition_id, source_sha256="abc123",
                             schema_version="1.0", root=root)


def test_same_pdf_same_pipeline_version_converges_on_same_key():
    key_a = compute_key(b"%PDF-1.4 fake bytes", "0.1.0")
    key_b = compute_key(b"%PDF-1.4 fake bytes", "0.1.0")
    assert key_a == key_b


def test_different_pipeline_version_yields_different_key():
    key_a = compute_key(b"%PDF-1.4 fake bytes", "0.1.0")
    key_b = compute_key(b"%PDF-1.4 fake bytes", "0.2.0")
    assert key_a != key_b


def test_put_then_get_roundtrips(tmp_path):
    store = ArtifactStore(tmp_path)
    key = "somekey"
    assert not store.exists(key)

    edition = _tiny_edition(key)
    store.put_edition(key, edition)

    assert store.exists(key)
    fetched = store.get_edition(key)
    assert fetched is not None
    assert fetched.edition_id == key


def test_edition_path_none_until_written_then_points_at_the_file(tmp_path):
    store = ArtifactStore(tmp_path)
    key = "somekey"
    assert store.edition_path(key) is None

    store.put_edition(key, _tiny_edition(key))
    path = store.edition_path(key)
    assert path is not None
    assert path.name == "edition.json"
    assert path.exists()


def test_put_is_idempotent_second_write_is_a_noop_semantically(tmp_path):
    store = ArtifactStore(tmp_path)
    key = "somekey"
    edition = _tiny_edition(key)
    store.put_edition(key, edition)
    store.put_edition(key, edition)  # same content, safe to retry after a crash
    assert store.get_edition(key).edition_id == key


def test_page_images_roundtrip(tmp_path):
    store = ArtifactStore(tmp_path)
    key = "somekey"
    store.put_page_image(key, 1, b"fake-png-bytes")
    store.put_page_image(key, 2, b"fake-png-bytes-2")

    assert store.list_page_numbers(key) == [1, 2]
    assert store.page_image_path(key, 1).read_bytes() == b"fake-png-bytes"
    assert store.page_image_path(key, 3) is None
