"""modality -- the per-language modal lexicon (verification-rules.md "modal verb
preservation", parser-consensus.md multilingual note).

Modality is NOT translatable one-to-one and must never be inferred by
translating to English first: French `il convient de` is ISO's rendering of
`should`, not `must`, and getting that backwards inverts a requirement. So the
lexicon is keyed by language and maps each surface modal to the closed CDM role
it carries. Shared by the modal-verb gate (which checks the modals survive
normalization) and classify_type (which assigns the role).
"""

from __future__ import annotations

import re

# surface modal -> CDM role, per language. Ordered longest-first per language so
# multi-word modals ("il convient de", "ist zu") match before their substrings.
MODALS: dict[str, dict[str, str]] = {
    "en": {
        "shall": "Requirement", "must": "Requirement",
        "shall not": "Requirement", "must not": "Requirement",
        "should": "Recommendation", "should not": "Recommendation",
        "may": "Permission", "need not": "Permission",
    },
    "de": {
        "ist zu": "Requirement", "sind zu": "Requirement",
        "muss": "Requirement", "müssen": "Requirement", "darf nicht": "Requirement",
        "sollte": "Recommendation", "sollten": "Recommendation",
        "darf": "Permission", "kann": "Permission",
    },
    "fr": {
        "il convient de": "Recommendation", "il convient que": "Recommendation",
        "doit": "Requirement", "doivent": "Requirement", "ne doit pas": "Requirement",
        "devrait": "Recommendation", "peut": "Permission", "peuvent": "Permission",
    },
}

# Warnings/cautions are language-agnostic enough to key on the admonition word.
ADMONITIONS = {
    "en": {"warning": "Warning", "caution": "Warning", "danger": "Warning"},
    "de": {"warnung": "Warning", "vorsicht": "Warning", "gefahr": "Warning"},
    "fr": {"avertissement": "Warning", "attention": "Warning", "danger": "Warning"},
}

_ALL_LANGS = ("en", "de", "fr")


def _lang_key(lang: str | None) -> str:
    if not lang:
        return "en"
    return lang.split("-")[0].lower()


def modal_lexicon(lang: str | None) -> dict[str, str]:
    """The modal map for a language (BCP-47 or bare code), defaulting to English
    for an unknown/absent language -- the gate still runs, it just uses the most
    common standards language."""
    return MODALS.get(_lang_key(lang), MODALS["en"])


def find_modals(text: str, lang: str | None) -> list[str]:
    """Every modal SURFACE occurring in `text`, case-preserved, in order of
    appearance. Case is preserved because `SHALL` vs `shall` carries normative
    weight and the modal-verb gate compares them exactly; matching is
    case-insensitive but the returned surface is verbatim from the text."""
    lex = modal_lexicon(lang)
    # longest surface first so "shall not" wins over "shall"
    surfaces = sorted(lex, key=len, reverse=True)
    pattern = re.compile(r"\b(" + "|".join(re.escape(s) for s in surfaces) + r")\b",
                         re.IGNORECASE)
    return [m.group(0) for m in pattern.finditer(text)]
