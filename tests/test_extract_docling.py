"""Regression coverage for app/pipeline/extract_docling.py's per-item
mapping. Uses stub items (not a real Docling conversion) to exercise item
types that lack a `.text` field -- TableItem and PictureItem both do, which
previously crashed extraction with an AttributeError on any real-world PDF
containing a table or an image (the synthetic e2e fixture only exercised
tables, so the picture case shipped unnoticed until a real document hit it).
"""

from types import SimpleNamespace

from app.pipeline.extract_docling import _content_builder


def _fake_prov(page=1, bbox=(0.0, 0.0, 10.0, 10.0)):
    return SimpleNamespace(page_no=page, bbox=SimpleNamespace(l=bbox[0], t=bbox[1], r=bbox[2], b=bbox[3]))


def test_table_item_without_text_attribute_does_not_raise():
    # Mirrors docling_core.types.doc.TableItem: no `.text` field at all.
    fake_table_item = SimpleNamespace(prov=[_fake_prov()], data=None)

    builder = _content_builder(fake_table_item, "table")
    node = builder.to_node()

    assert node is not None
    assert node.type == "table"
    assert node.text is None


def test_picture_item_without_text_attribute_does_not_raise():
    # Mirrors docling_core.types.doc.PictureItem: no `.text` field at all.
    fake_picture_item = SimpleNamespace(prov=[_fake_prov()])

    builder = _content_builder(fake_picture_item, "figure")
    node = builder.to_node()

    assert node is not None
    assert node.type == "figure"
    assert node.text is None


def test_text_bearing_item_keeps_its_text():
    fake_text_item = SimpleNamespace(prov=[_fake_prov()], text="Hello clause body.")

    builder = _content_builder(fake_text_item, "paragraph")
    node = builder.to_node()

    assert node is not None
    assert node.text == "Hello clause body."


def test_item_without_provenance_yields_no_node():
    fake_item = SimpleNamespace(prov=[], text="orphaned")

    builder = _content_builder(fake_item, "paragraph")
    assert builder.to_node() is None
