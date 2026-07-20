"""Content-addressed identity (app/pipeline/identity.py): clause-path ids,
determinism, and — critically — that re-stamping keeps every id-reference
(source_object_id, continues_from/to) pointing at the right node."""

from decimal import Decimal

import canonical_schema as cs
from app.pipeline import identity


def _prov():
    return cs.Provenance(page=1, bbox=(0, 0, 1, 1), parser="docling",
                         model_version="v", confidence=0.9)


def _n(id, type="section", **kw):
    return cs.Node(id=id, type=type, provenance=_prov(), **kw)


def test_clause_node_gets_section_path_id():
    root = _n("root", children=[_n("u1", clause_id="5.3.2", text="x")])
    out = identity.restamp_ids(root, doc_id="deadbeef", standard_id="IEC61000-4-3")
    assert out.children[0].id == "IEC61000-4-3#5.3.2"


def test_unnumbered_node_gets_content_hash_id():
    root = _n("root", children=[_n("u1", type="paragraph", text="some prose", clause_id=None)])
    out = identity.restamp_ids(root, doc_id="deadbeef")
    assert out.children[0].id.startswith("deadbeef#")
    assert len(out.children[0].id.split("#")[1]) == 12


def test_deterministic_same_tree_same_ids():
    def tree():
        return _n("root", children=[_n("a", clause_id="1", text="A"),
                                    _n("b", clause_id="2", text="B")])
    a = identity.restamp_ids(tree(), doc_id="d", standard_id="S")
    b = identity.restamp_ids(tree(), doc_id="d", standard_id="S")
    assert [c.id for c in a.children] == [c.id for c in b.children]


def test_continues_from_to_references_remapped():
    t1 = _n("t1", type="table", clause_id=None, text="frag1")
    t2 = _n("t2", type="table", clause_id=None, text="frag2")
    t1 = t1.model_copy(update={"continues_to": "t2"})
    t2 = t2.model_copy(update={"continues_from": "t1"})
    root = _n("root", children=[t1, t2])
    out = identity.restamp_ids(root, doc_id="d")
    new_t1, new_t2 = out.children
    assert new_t1.continues_to == new_t2.id  # remapped, not the stale "t2"
    assert new_t2.continues_from == new_t1.id
    assert new_t1.id != "t1" and new_t2.id != "t2"


def test_parameter_source_object_id_remapped():
    p = cs.Parameter(name="v", value=Decimal("10"), unit="V/m", comparator="gte",
                     source_object_id="u1")
    node = _n("u1", type="paragraph", clause_id="4.2", text="shall be 10 V/m",
              parameters=[p])
    root = _n("root", children=[node])
    out = identity.restamp_ids(root, doc_id="d", standard_id="S")
    child = out.children[0]
    assert child.id == "S#4.2"
    assert child.parameters[0].source_object_id == "S#4.2"  # follows the node's new id


def test_collision_disambiguated():
    # two nodes with the same clause_id -> unique ids
    root = _n("root", children=[_n("a", clause_id="3.1", text="x"),
                                _n("b", clause_id="3.1", text="y")])
    out = identity.restamp_ids(root, doc_id="d", standard_id="S")
    ids = [c.id for c in out.children]
    assert len(set(ids)) == 2 and "S#3.1" in ids


def test_derive_standard_id():
    root = _n("root", children=[_n("t", type="paragraph",
              text="DIN EN 60068-2-64 Umweltprüfungen", clause_id=None)])
    assert identity.derive_standard_id(root) == "DIN EN 60068-2-64"
