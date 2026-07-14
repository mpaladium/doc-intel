"""Unit coverage for lang.detect + text.normalize (app/pipeline/lang.py).
Detection uses the real lingua models (bundled, offline) but only on clearly
in-set languages so assertions are stable."""

import unicodedata

from canonical_schema import Node, Provenance
from app.pipeline import lang


def _prov():
    return Provenance(page=1, bbox=(0, 0, 1, 1), parser="docling",
                      model_version="v1", confidence=0.95)


def _node(text=None, lang_=None, children=None):
    return Node(id=f"n{id(text)}", type="paragraph", text=text, lang=lang_,
                provenance=_prov(), children=children or [])


def test_normalize_text_is_nfc():
    # "e" + combining acute -> single NFC codepoint é
    decomposed = "é"
    out = lang.normalize_text(decomposed)
    assert out == "é"
    assert unicodedata.is_normalized("NFC", out)


def test_detect_lang_english():
    assert lang.detect_lang("This standard specifies limits for radiated emissions.") == "en"


def test_detect_lang_french():
    assert lang.detect_lang("Cette norme spécifie les limites pour les émissions rayonnées.") == "fr"


def test_detect_lang_too_short_returns_none():
    assert lang.detect_lang("ok") is None


def test_annotate_node_sets_lang_and_normalizes():
    n = _node(text="This is a clearly English sentence about compliance testing.")
    out = lang.annotate_node(n)
    assert out.lang == "en"


def test_annotate_node_preserves_existing_lang():
    n = _node(text="This is a clearly English sentence about compliance.", lang_="de")
    out = lang.annotate_node(n)
    assert out.lang == "de"  # not overwritten


def test_annotate_node_normalizes_decomposed_text():
    n = _node(text="Préface de la norme internationale sur les mesures.")
    out = lang.annotate_node(n)
    assert unicodedata.is_normalized("NFC", out.text)


def test_dominant_lang_picks_most_common():
    root = _node(children=[
        _node(text="a", lang_="en"),
        _node(text="b", lang_="en"),
        _node(text="c", lang_="fr"),
    ])
    assert lang.dominant_lang(root) == "en"


def test_dominant_lang_none_when_untagged():
    root = _node(children=[_node(text="a"), _node(text="b")])
    assert lang.dominant_lang(root) is None
