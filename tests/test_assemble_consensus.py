"""Text-consensus wiring in assemble (_apply_text_consensus): Docling's
reflow-derived text is a recorded corroborator, not a voter, so a PyMuPDF-vs-
Docling difference does NOT quarantine; a genuine alternate transcriber
(surya/mineru) dissenting DOES. This is what keeps the 2-parser text pair from
re-flooding the review queue while still honoring the consensus invariant once a
real second transcriber exists."""

from decimal import Decimal

import canonical_schema as cs
from app.pipeline.assemble import _apply_text_consensus


def _prov():
    return cs.Provenance(page=1, bbox=(0, 0, 10, 10), parser="pymupdf",
                         model_version="v", confidence=0.9)


def _node(**kw):
    kw.setdefault("id", "n")
    kw.setdefault("type", "paragraph")
    kw.setdefault("provenance", _prov())
    return cs.Node(**kw)


def _root(children):
    return cs.Node(id="root", type="section", provenance=_prov(), children=children)


def test_docling_only_dissent_does_not_quarantine():
    # PyMuPDF (authority) vs Docling differ -- Docling isn't a genuine text
    # voter, so this stays unanimous (no flood)
    n = _node(id="p", raw_text="Status I",
              parsers={"pymupdf": "Status I", "docling": "1 Status I"})
    out = _apply_text_consensus(_root([n]))
    assert out.children[0].consensus == "unanimous"


def test_genuine_transcriber_dissent_on_normative_quarantines():
    # a genuine second transcriber (surya) disagrees on a normative object ->
    # quarantine per the consensus invariant (normative requires unanimity)
    n = _node(id="p", cdm_type="Requirement", raw_text="10 V/m",
              parsers={"pymupdf": "10 V/m", "surya": "1O V/m"})  # digit/letter confusable
    out = _apply_text_consensus(_root([n]))
    q = out.children[0]
    assert q.consensus == "quarantined"
    assert "consensus" in (q.quarantine_reason or "")
    assert q.review_required


def test_genuine_transcriber_agreement_is_unanimous():
    n = _node(id="p", raw_text="10 V/m",
              parsers={"pymupdf": "10 V/m", "surya": "10 V/m"})
    out = _apply_text_consensus(_root([n]))
    assert out.children[0].consensus == "unanimous"


def test_non_normative_majority_records_dissent_without_quarantine():
    # non-normative, 3 genuine transcribers, one dissents -> majority admit
    n = _node(id="p", raw_text="informative note",
              parsers={"pymupdf": "informative note", "surya": "informative note",
                       "mineru": "informative n0te"})
    out = _apply_text_consensus(_root([n]))
    assert out.children[0].consensus == "majority"
    assert "consensus_minority_dissent" in out.children[0].review_reasons
