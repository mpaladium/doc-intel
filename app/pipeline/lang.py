"""lang.detect + text.normalize (SKILLS.md).

`text.normalize`: NFC-normalize every extracted string so two byte-different
but canonically-equal strings don't diff downstream (prevents phantom-diff
drift, ARCHITECTURE.md §2.1).

`lang.detect`: per-node BCP-47 language tag into `Node.lang`. The Goal-1
tool of record is fastText `lid.176`, but it doesn't build on this
Python/toolchain; `lingua` is used instead -- fully offline (all language
models bundled in the wheel, no runtime download, satisfying AGENTS.md
§1.13), deterministic, and more accurate on short blocks. The detector is
restricted to a curated language set (the ~15 languages the section-role
rulepack already covers, plus a few common EMC/standards-publishing
languages) to keep memory bounded rather than loading all ~75 lingua models.

Short/ambiguous strings stay `None` rather than guessing: language is a
booster signal here (like the section-role dictionary), never a gate, so an
absent tag is safe and a wrong tag is worse than none.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache

from lingua import Language, LanguageDetectorBuilder

from canonical_schema import Node

# Curated set: covers the section_roles.yaml rulepack languages plus common
# standards-publishing ones. Kept explicit so memory/startup cost is bounded
# and predictable, and so adding a language is a conscious, reviewable change.
_LANGUAGES = [
    Language.ENGLISH, Language.FRENCH, Language.GERMAN, Language.SPANISH,
    Language.ITALIAN, Language.PORTUGUESE, Language.DUTCH, Language.POLISH,
    Language.SWEDISH, Language.DANISH, Language.CZECH, Language.TURKISH,
    Language.CHINESE, Language.JAPANESE, Language.KOREAN, Language.RUSSIAN,
    Language.ARABIC,
]

# Below this length a detection is too unreliable to trust; leave lang=None.
_MIN_CHARS = 12
# Minimum detector confidence to accept a tag (booster, not a gate).
_MIN_CONFIDENCE = 0.55


def normalize_text(text: str) -> str:
    """NFC normalization -- the single canonical form for all stored text."""
    return unicodedata.normalize("NFC", text)


@lru_cache(maxsize=1)
def _detector():
    # preload_all_languages keeps detection latency predictable after the
    # first call (models loaded once, at build time, not lazily per call).
    return LanguageDetectorBuilder.from_languages(*_LANGUAGES).with_preloaded_language_models().build()


def detect_lang(text: str) -> str | None:
    """Return a BCP-47 (ISO 639-1) tag, or None when too short/uncertain."""
    stripped = text.strip()
    if len(stripped) < _MIN_CHARS:
        return None
    det = _detector()
    lang = det.detect_language_of(stripped)
    if lang is None:
        return None
    conf = det.compute_language_confidence(stripped, lang)
    if conf < _MIN_CONFIDENCE:
        return None
    return lang.iso_code_639_1.name.lower()


def annotate_node(node: Node) -> Node:
    """Depth-first: NFC-normalize `text` and set `lang` on any text-bearing
    node that doesn't already have one. Same rebuild-children-first
    `model_copy` pattern as the other pipeline passes."""
    children = [annotate_node(c) for c in node.children]
    update: dict = {"children": children}

    if node.text:
        normalized = normalize_text(node.text)
        if normalized != node.text:
            update["text"] = normalized
        if node.lang is None:
            detected = detect_lang(normalized)
            if detected is not None:
                update["lang"] = detected

    return node.model_copy(update=update)


def dominant_lang(node: Node) -> str | None:
    """Most common non-None `lang` across all nodes -- the document's
    `lang_primary`. Ties broken by first-seen for determinism."""
    counts: dict[str, int] = {}
    order: list[str] = []

    def _walk(n: Node):
        if n.lang:
            if n.lang not in counts:
                order.append(n.lang)
            counts[n.lang] = counts.get(n.lang, 0) + 1
        for c in n.children:
            _walk(c)

    _walk(node)
    if not counts:
        return None
    return max(order, key=lambda l: counts[l])
