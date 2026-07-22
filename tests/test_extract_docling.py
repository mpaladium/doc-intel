"""Regression coverage for app/pipeline/extract_docling.py's per-item
mapping. Uses stub items (not a real Docling conversion) to exercise item
types that lack a `.text` field -- TableItem and PictureItem both do, which
previously crashed extraction with an AttributeError on any real-world PDF
containing a table or an image (the synthetic e2e fixture only exercised
tables, so the picture case shipped unnoticed until a real document hit it).

Also covers caption handling: captions should never become spurious
top-level sections or generic paragraphs disconnected from the table/figure
they describe (see CHANGELOG). Docling's own TableItem/PictureItem.captions
back-reference is used when Docling populates it, but empirically that link
isn't always populated even for an adjacent caption -- so an "unclaimed"
caption must still become a correctly-typed node, never silently dropped.
"""

import os
from types import SimpleNamespace

from docling_core.types.doc import DocItemLabel

from app.pipeline.extract_docling import _build_tree, _claimed_caption_ids, _content_builder


def _fake_prov(page=1, bbox=(0.0, 0.0, 10.0, 10.0)):
    return SimpleNamespace(page_no=page, bbox=SimpleNamespace(l=bbox[0], t=bbox[1], r=bbox[2], b=bbox[3]))


def _fake_ref(resolved_item):
    return SimpleNamespace(resolve=lambda doc: resolved_item)


def _item(label, text=None, level=None, children=(), captions=(), **extra):
    """A fake Docling content item. `children`/`captions` are the raw child
    items; they're wrapped as resolvable refs (matching Docling's model)."""
    ns = SimpleNamespace(label=label, prov=[_fake_prov()],
                         children=[_fake_ref(c) for c in children], **extra)
    if text is not None:
        ns.text = text
    if level is not None:
        ns.level = level
    if captions:
        ns.captions = [_fake_ref(c) for c in captions]
    return ns


def _group(children=(), label="group"):
    """A fake Docling GroupItem/ListGroup: its `.label` is NOT a DocItemLabel,
    which is how _build_tree tells a structural container from content."""
    return SimpleNamespace(label=label, children=[_fake_ref(c) for c in children])


def _fake_doc(top_items):
    """A fake DoclingDocument: a `body` group whose children are the given
    top-level items (each a resolvable ref)."""
    return SimpleNamespace(body=_group(children=top_items))


def _section_header(text, level=1):
    return _item(DocItemLabel.SECTION_HEADER, text=text, level=level)


def _text_item(text, label=DocItemLabel.TEXT):
    return _item(label, text=text)


def test_table_item_without_text_attribute_does_not_raise():
    # Mirrors docling_core.types.doc.TableItem: no `.text` field at all.
    fake_table_item = SimpleNamespace(prov=[_fake_prov()], data=None)

    builder = _content_builder(fake_table_item, "table")
    node = builder.to_node()

    assert node is not None
    assert node.type == "table"
    assert node.text is None


def _fake_table_cell(row, col, text, bbox=(1.0, 2.0, 3.0, 4.0)):
    return SimpleNamespace(
        start_row_offset_idx=row, end_row_offset_idx=row + 1,
        start_col_offset_idx=col, end_col_offset_idx=col + 1,
        column_header=(row == 0), text=text,
        bbox=SimpleNamespace(l=bbox[0], t=bbox[1], r=bbox[2], b=bbox[3]),
    )


def test_table_cells_carry_source_page_and_bbox():
    # Cells must record their own page (the table's page) so a stitched
    # continuation table can attribute each cell to where it came from.
    table_item = SimpleNamespace(
        prov=[_fake_prov(page=7)],
        data=SimpleNamespace(table_cells=[
            _fake_table_cell(0, 0, "Parameters"),
            _fake_table_cell(1, 0, "Electrical slow transient"),
        ]),
    )
    node = _content_builder(table_item, "table").to_node()
    assert node.cells is not None and len(node.cells) == 2
    assert all(c.page == 7 for c in node.cells)
    assert node.cells[0].bbox == (1.0, 2.0, 3.0, 4.0)


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


def test_formula_item_text_becomes_node_latex():
    # After formula enrichment, FormulaItem.text is the LaTeX string.
    fake_formula = SimpleNamespace(prov=[_fake_prov()], text="E = mc^2")

    node = _content_builder(fake_formula, "equation").to_node()

    assert node.type == "equation"
    assert node.latex == "E = mc^2"
    assert node.text == "E = mc^2"


def test_formula_item_without_text_yields_no_latex():
    fake_formula = SimpleNamespace(prov=[_fake_prov()])  # enrichment off/failed

    node = _content_builder(fake_formula, "equation").to_node()

    assert node.type == "equation"
    assert node.latex is None


# --------------------------------------------------------------------------- #
# Caption resolution via TableItem/PictureItem.captions
# --------------------------------------------------------------------------- #
def test_content_builder_resolves_captions_via_dldoc():
    cap_item = SimpleNamespace(text="Table 1 -- caption text", prov=[_fake_prov()])
    table_item = SimpleNamespace(prov=[_fake_prov()], data=None, captions=[_fake_ref(cap_item)])

    builder = _content_builder(table_item, "table", dldoc=object())
    node = builder.to_node()

    assert node.type == "table"
    assert len(node.children) == 1
    assert node.children[0].type == "caption"
    assert node.children[0].text == "Table 1 -- caption text"


def test_content_builder_without_dldoc_skips_caption_resolution():
    cap_item = SimpleNamespace(text="Table 1 -- caption text", prov=[_fake_prov()])
    table_item = SimpleNamespace(prov=[_fake_prov()], data=None, captions=[_fake_ref(cap_item)])

    builder = _content_builder(table_item, "table")  # dldoc defaults to None
    node = builder.to_node()

    assert node.children == []


def test_claimed_caption_ids_records_resolved_caption_identity():
    cap_item = _item(DocItemLabel.CAPTION, text="cap")
    table_item = _item(DocItemLabel.TABLE, captions=[cap_item])
    claimed = _claimed_caption_ids(_fake_doc([table_item]))
    assert id(cap_item) in claimed


def test_claimed_caption_ids_skips_unresolvable_refs():
    def _raise(doc):
        raise RuntimeError("broken ref")
    table_item = SimpleNamespace(label=DocItemLabel.TABLE, children=[],
                                 captions=[SimpleNamespace(resolve=_raise)])
    assert _claimed_caption_ids(_fake_doc([table_item])) == set()


# --------------------------------------------------------------------------- #
# _build_tree: caption-like heading guard + claimed/unclaimed caption handling
# --------------------------------------------------------------------------- #
def test_caption_like_heading_does_not_open_new_section():
    nodes = _build_tree(_fake_doc([
        _section_header("1 Scope", level=1),
        _text_item("Scope body text."),
        _section_header("Table 1 continued", level=2),  # Docling mislabel
        _text_item("More text after."),
    ]))

    assert len(nodes) == 1  # "Table 1 continued" never became a top-level section
    scope = nodes[0]
    assert scope.text == "1 Scope"
    assert not any(c.type == "section" for c in scope.children)
    assert any(c.type == "caption" and c.text == "Table 1 continued" for c in scope.children)


def test_claimed_caption_nested_under_table_not_duplicated_as_sibling():
    cap_item = _item(DocItemLabel.CAPTION, text="Table 1 -- caption")
    table_item = _item(DocItemLabel.TABLE, captions=[cap_item], data=None)
    nodes = _build_tree(_fake_doc([
        _section_header("4.2.3 Limits"),
        table_item,
        cap_item,
    ]))
    section = nodes[0]

    table_node = next(c for c in section.children if c.type == "table")
    assert any(cc.type == "caption" for cc in table_node.children)
    # not duplicated as a top-level sibling of the table
    assert not any(c.type == "caption" for c in section.children)


def test_unclaimed_caption_still_becomes_a_node_not_dropped():
    # Mirrors the real-world finding: Docling can label an item CAPTION
    # without linking it via any table/figure's .captions. Losing it
    # entirely would violate "no content silently lost".
    cap_item = _item(DocItemLabel.CAPTION, text="Figure 1 -- diagram")
    nodes = _build_tree(_fake_doc([
        _section_header("4.2 Radiated emissions"),
        cap_item,
    ]))
    section = nodes[0]

    assert len(section.children) == 1
    assert section.children[0].type == "caption"
    assert section.children[0].text == "Figure 1 -- diagram"


# --------------------------------------------------------------------------- #
# _build_tree: deep nesting (group/list hierarchy)
# --------------------------------------------------------------------------- #
def test_section_nesting_from_heading_levels():
    nodes = _build_tree(_fake_doc([
        _section_header("4 Test methods", level=1),
        _text_item("intro"),
        _section_header("4.1 General", level=2),
        _text_item("general body"),
        _section_header("4.2 Radiated", level=2),
    ]))
    assert len(nodes) == 1
    top = nodes[0]
    assert top.text == "4 Test methods"
    subsections = [c for c in top.children if c.type == "section"]
    assert [s.text for s in subsections] == ["4.1 General", "4.2 Radiated"]


def test_nested_list_preserves_depth():
    # A ListGroup with a ListItem that itself contains a nested ListGroup.
    inner_li = _item(DocItemLabel.LIST_ITEM, text="inner a")
    inner_group = _group(children=[inner_li])
    outer_li_with_sub = _item(DocItemLabel.LIST_ITEM, text="outer 1", children=[inner_group])
    outer_li_plain = _item(DocItemLabel.LIST_ITEM, text="outer 2")
    list_group = _group(children=[outer_li_with_sub, outer_li_plain])

    nodes = _build_tree(_fake_doc([
        _section_header("4 Requirements"),
        list_group,
    ]))
    section = nodes[0]

    top_items = [c for c in section.children if c.type == "list_item"]
    assert [li.text for li in top_items] == ["outer 1", "outer 2"]
    # "outer 1" carries the nested list item as a child -> real depth preserved
    nested = [c for c in top_items[0].children if c.type == "list_item"]
    assert [n.text for n in nested] == ["inner a"]


def test_group_container_is_flattened_but_contents_kept():
    # A bare (unspecified) group directly under a section: the group emits no
    # node of its own, but its children still land under the section.
    grp = _group(children=[_text_item("para in group")])
    nodes = _build_tree(_fake_doc([_section_header("1 Scope"), grp]))
    section = nodes[0]
    assert [c.type for c in section.children] == ["paragraph"]
    assert section.children[0].text == "para in group"


def test_tableformer_v2_is_the_default_table_model(monkeypatch):
    """Table geometry (cell grid, per-cell bbox, spans) is Docling-owned and is
    what the rectangularity gate and the evaluator's per-cell overlays read, so
    which TableFormer builds it is a correctness-relevant choice, not a tuning
    knob. V2 is the default; V1 stays reachable as an escape hatch."""
    from docling.datamodel.pipeline_options import (
        TableStructureOptions, TableStructureV2Options,
    )
    from app.pipeline.extract_docling import _table_structure_options

    monkeypatch.delenv("INGESTION_TABLEFORMER", raising=False)
    assert isinstance(_table_structure_options(), TableStructureV2Options)

    monkeypatch.setenv("INGESTION_TABLEFORMER", "v1")
    assert isinstance(_table_structure_options(), TableStructureOptions)

    # unknown value falls forward to the default rather than crashing extraction
    monkeypatch.setenv("INGESTION_TABLEFORMER", "nonsense")
    assert isinstance(_table_structure_options(), TableStructureV2Options)


def test_tableformer_options_reach_the_converter(monkeypatch):
    """Guard the wiring, not just the helper: a converter built with the default
    must actually carry the V2 options (a silently-ignored pipeline option would
    otherwise look identical from the outside)."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import TableStructureV2Options
    from app.pipeline.extract_docling import build_converter

    monkeypatch.delenv("INGESTION_TABLEFORMER", raising=False)
    conv = build_converter(ocr_enabled=False, formulas=False)
    opts = conv.format_to_options[InputFormat.PDF].pipeline_options
    assert opts.do_table_structure is True
    assert isinstance(opts.table_structure_options, TableStructureV2Options)


def test_tableformer_variant_is_part_of_the_content_address_key():
    """Regression: INGESTION_TABLEFORMER changes extraction output, so it has to
    change the artifact-store key too. When it didn't, flipping to v1 silently
    served the cached v2 editions (an A/B eval run reported byte-identical
    metrics for both models, and a "reprocessed" document finished in 0.2s)."""
    import importlib
    import app.version

    def _version_with(env_value):
        if env_value is None:
            os.environ.pop("INGESTION_TABLEFORMER", None)
        else:
            os.environ["INGESTION_TABLEFORMER"] = env_value
        return importlib.reload(app.version).PIPELINE_VERSION

    previous = os.environ.get("INGESTION_TABLEFORMER")
    try:
        default_v = _version_with(None)
        v2_v = _version_with("v2")
        v1_v = _version_with("v1")

        assert default_v == v2_v, "v2 is the default, so it keeps the bare version"
        assert v1_v != v2_v, "v1 must not share v2's content-address namespace"
        assert v1_v.startswith(v2_v)
        # exactly one "+" in the resulting key, so `sha256(pdf)+version` stays parseable
        assert "+" not in v1_v
    finally:
        if previous is None:
            os.environ.pop("INGESTION_TABLEFORMER", None)
        else:
            os.environ["INGESTION_TABLEFORMER"] = previous
        importlib.reload(app.version)


def test_page_confidence_is_json_safe_and_nan_becomes_null():
    """Docling leaves unimplemented/N-A components as NaN. `float('nan')`
    serializes as the bare token `NaN` -- invalid JSON, which would break the
    evaluator page's fetch() on the edition. They must come back as None."""
    import json
    from app.pipeline.extract_docling import _page_confidence, _score

    assert _score(float("nan")) is None
    assert _score(None) is None
    assert _score(0.87654321) == 0.8765

    page = SimpleNamespace(
        layout_score=0.9048, parse_score=1.0,
        table_score=float("nan"), ocr_score=float("nan"),
        mean_grade=SimpleNamespace(value="excellent"),
    )
    got = _page_confidence(SimpleNamespace(confidence=SimpleNamespace(pages={1: page})))
    assert got == {"1": {"layout": 0.9048, "parse": 1.0, "table": None,
                         "ocr": None, "mean_grade": "excellent"}}
    assert "NaN" not in json.dumps(got)

    # a Docling version without the confidence field must not break extraction
    assert _page_confidence(SimpleNamespace()) == {}
    assert _page_confidence(SimpleNamespace(confidence=None)) == {}


def test_page_confidence_is_diagnostic_only_and_never_gated_on():
    """Guard the decision, not just the data: Docling's confidence measured
    r=+0.009 against page coverage and 0.26-0.31 precision as a gate, so it is
    captured as inert provenance. If someone later wires it into a gate or into
    consensus, this fails and sends them to the measurements first."""
    from pathlib import Path

    src = Path("app/pipeline")
    readers = []
    for path in list((src / "gates").glob("*.py")) + [
        src / "consensus.py", src / "assemble.py",
    ]:
        text = path.read_text()
        if "page_confidence" not in text:
            continue
        # assemble.py is the one legitimate writer (into pipeline_provenance);
        # anything else touching it, or assemble branching on it, is a gate.
        for line in text.splitlines():
            if "page_confidence" not in line or line.strip().startswith("#"):
                continue
            is_write = path.name == "assemble.py" and (
                '"page_confidence": page_confidence' in line
                or "page_confidence = extract_with_confidence" in line
                or "top_sections, page_confidence" in line
            )
            if not is_write:
                readers.append(f"{path.name}: {line.strip()}")
    assert readers == [], (
        "Docling page_confidence must stay diagnostic-only; found: " + "; ".join(readers)
    )


# --------------------------------------------------------------------------- #
# Multilingual caption guard + standalone block labels
# --------------------------------------------------------------------------- #
def test_german_caption_like_heading_does_not_open_section():
    """Docling labels "Tabelle 3 - ..." a SectionHeaderItem. Unguarded it opened
    a section that then ADOPTED the very table it captions. Worse, because
    gates/cross_reference._caption_inventory only scans type=="caption", every
    "siehe Tabelle 3" in the document then dangled -- this single English-only
    regex accounted for 14 spurious sections and 9 quarantines in the German
    samples."""
    for text in ("Tabelle 3 - FPSC Luftentladung",
                 "Bild 4 - Indirekte Entladung",
                 "Abbildung 12 - Aufbau",
                 "Tabelle 19 (fortgesetzt)"):
        nodes = _build_tree(_fake_doc([
            _section_header("5 Anforderungen"),
            _section_header(text),
            _item(DocItemLabel.TABLE),
        ]))
        section = nodes[0]
        kinds = [c.type for c in section.children]
        assert "caption" in kinds, f"{text!r} should become a caption, got {kinds}"
        assert all(c.type != "section" for c in section.children), \
            f"{text!r} must not open a section (got {kinds})"


def test_english_caption_guard_still_applies():
    nodes = _build_tree(_fake_doc([
        _section_header("5 Requirements"),
        _section_header("Table 3 - Limits"),
    ]))
    assert [c.type for c in nodes[0].children] == ["caption"]


def test_legend_block_label_becomes_note_not_section():
    """"Legende" labels the symbols of the adjacent object. It must not open a
    section (it was swallowing the following table/figure), but it is not the
    caption of a numbered object either -- so it becomes a `note`, which also
    keeps it out of the xref caption inventory."""
    for text in ("Legende", "Legende:", "Zeichenerklärung", "Legend", "Key"):
        nodes = _build_tree(_fake_doc([
            _section_header("5 Anforderungen"),
            _section_header(text),
            _item(DocItemLabel.TABLE),
        ]))
        kinds = [c.type for c in nodes[0].children]
        assert "note" in kinds and "section" not in kinds, f"{text!r} -> {kinds}"


def test_real_heading_still_opens_a_section():
    # The guard must not swallow genuine numbered headings.
    for text in ("5.3.2 Prüfaufbau", "6 Normative references", "Anhang A"):
        nodes = _build_tree(_fake_doc([_section_header(text)]))
        assert nodes[0].type == "section" and nodes[0].text == text
