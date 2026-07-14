"""Unit coverage for caption_attach.attach_captions_by_proximity: an unclaimed
caption sibling adjacent to exactly one table/figure on the same page becomes
its child; ambiguous or unrelated cases stay untouched (fail-safe)."""

from canonical_schema import Node, Provenance
from app.pipeline.caption_attach import attach_captions_by_proximity


def _prov(page=1):
    return Provenance(page=page, bbox=(0, 0, 1, 1), parser="docling", model_version="v1", confidence=0.9)


def _node(type_, text=None, page=1, children=None):
    return Node(id=f"n{id(text) if text else id(children)}{page}{type_}", type=type_, text=text,
                children=children or [], provenance=_prov(page))


def test_caption_after_table_same_page_attaches_to_it():
    table = _node("table", page=1)
    caption = _node("caption", text="Table 1 -- Limits", page=1)
    out = attach_captions_by_proximity([table, caption])
    assert len(out) == 1
    assert out[0].type == "table"
    assert [c.type for c in out[0].children] == ["caption"]


def test_caption_before_figure_same_page_attaches_to_it():
    caption = _node("caption", text="Figure 1 -- Setup", page=1)
    figure = _node("figure", page=1)
    out = attach_captions_by_proximity([caption, figure])
    assert len(out) == 1
    assert out[0].type == "figure"
    assert [c.type for c in out[0].children] == ["caption"]


def test_caption_between_two_tables_stays_ambiguous():
    t1 = _node("table", page=1)
    caption = _node("caption", text="Table 1", page=1)
    t2 = _node("table", page=1)
    out = attach_captions_by_proximity([t1, caption, t2])
    assert [n.type for n in out] == ["table", "caption", "table"]
    assert out[0].children == [] and out[2].children == []


def test_caption_with_no_adjacent_table_or_figure_stays_sibling():
    para = _node("paragraph", text="intro", page=1)
    caption = _node("caption", text="Table 1", page=1)
    out = attach_captions_by_proximity([para, caption])
    assert [n.type for n in out] == ["paragraph", "caption"]


def test_caption_and_table_on_different_pages_not_attached():
    table = _node("table", page=1)
    caption = _node("caption", text="Table 1", page=2)  # stitched-table edge case
    out = attach_captions_by_proximity([table, caption])
    assert [n.type for n in out] == ["table", "caption"]
    assert out[0].children == []


def test_already_claimed_caption_untouched():
    # A claimed caption already lives inside the table's own children -- this
    # pass must not touch it or duplicate it.
    inner_caption = _node("caption", text="Table 1", page=1)
    table = _node("table", page=1, children=[inner_caption])
    out = attach_captions_by_proximity([table])
    assert len(out) == 1
    assert [c.type for c in out[0].children] == ["caption"]


def test_recurses_into_nested_sections():
    table = _node("table", page=1)
    caption = _node("caption", text="Table 1", page=1)
    section = _node("section", text="4.2 Radiated", page=1, children=[table, caption])
    out = attach_captions_by_proximity([section])
    assert len(out) == 1
    inner = out[0].children
    assert len(inner) == 1 and inner[0].type == "table"
    assert [c.type for c in inner[0].children] == ["caption"]
