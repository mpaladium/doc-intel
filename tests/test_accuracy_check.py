"""Unit coverage for the factual-accuracy primitives (app/cli/accuracy.py):
furniture classification, token-subset coverage (which makes hyphenation
fragments non-misses), numeric tokenization, and reading-order tau. Pure
functions -- no PDF/model needed."""

from app.cli import accuracy as acc


def _line(text, y0, y1, horizontal=True, x0=50.0):
    return acc.SourceLine(text=text, y0=y0, y1=y1, x0=x0, horizontal=horizontal)


PAGE_H = 842.0


def test_furniture_top_and_bottom_band():
    header = _line("DIN EN 60068", y0=30, y1=40)          # ~4% -> furniture
    footer = _line("Seite 29", y0=800, y1=810)            # ~96% -> furniture
    body = _line("This is a normative requirement.", y0=400, y1=412)
    assert acc.is_furniture(header, PAGE_H, set())
    assert acc.is_furniture(footer, PAGE_H, set())
    assert not acc.is_furniture(body, PAGE_H, set())


def test_rotated_line_is_furniture_watermark():
    # The Beuth licensing watermark runs vertically up the left margin.
    watermark = _line("Lizenziert fuer ... elektronische Fassung", y0=400, y1=600, horizontal=False)
    assert acc.is_furniture(watermark, PAGE_H, set())


def test_repeated_line_is_furniture():
    running = _line("Class guideline DNVGL-CG-0339", y0=400, y1=412)  # body band, but repeats
    repeated = {acc.norm("Class guideline DNVGL-CG-0339")}
    assert acc.is_furniture(running, PAGE_H, repeated)
    assert not acc.is_furniture(running, PAGE_H, set())


def test_find_repeated_lines_threshold():
    pages = [
        [_line("Header X", 30, 40), _line("Unique 1", 400, 412)],
        [_line("Header X", 30, 40), _line("Unique 2", 400, 412)],
        [_line("Header X", 30, 40), _line("Unique 3", 400, 412)],
    ]
    repeated = acc.find_repeated_lines(pages, min_pages=3)
    assert acc.norm("Header X") in repeated
    assert acc.norm("Unique 1") not in repeated


def test_token_coverage_treats_rejoined_word_as_covered():
    # source physical line is a wrap fragment; extraction rejoined the word.
    src = acc.content_words("ische Sensitivitaet aufweist")
    ext = acc.content_words("das spezifische verhalten sensitivitaet aufweist den")
    cov, missed = acc.token_coverage(src, ext)
    assert missed == {"ische"}  # only the bare fragment; the real words matched
    assert cov > 0.6


def test_numeric_tokens_keep_values_whole():
    assert acc.numeric_tokens("limit 0,5 m/s2 over 30-230 MHz") == {"0,5", "m/s2", "30-230"}


def test_kendall_tau_reading_order():
    assert acc.kendall_tau([0, 1, 2, 3]) == 1.0
    assert acc.kendall_tau([3, 2, 1, 0]) == -1.0
    assert acc.kendall_tau([0]) == 1.0


def test_reading_order_tau_bottom_left_origin():
    # Docling bottom-left origin: reading order is DESCENDING y. Tree order
    # already top-to-bottom (high y first) -> perfect.
    assert acc.reading_order_tau([699.0, 668.0, 649.0, 600.0]) == 1.0
    # Tree order bottom-to-top (ascending y) -> fully reversed reading order.
    assert acc.reading_order_tau([100.0, 200.0, 300.0]) == -1.0
    assert acc.reading_order_tau([500.0]) == 1.0


def test_content_words_ignores_short_and_numeric():
    assert acc.content_words("a 12 of the requirement") == {"the", "requirement"}


def test_clause_heading_candidate_accepts_real_headings():
    assert acc.clause_heading_candidate(_line("Grenzwertklassen 5.3.4", 400, 412)) == "5.3.4"
    assert acc.clause_heading_candidate(_line("4.2.3 Limits", 400, 412)) == "4.2.3"


def test_clause_heading_candidate_rejects_toc_sentences_and_bare_ints():
    # TOC-shaped (dot leader + page number)
    assert acc.clause_heading_candidate(_line("5.3 Störemission ........ 27", 400, 412)) is None
    # sentence mentioning a clause
    assert acc.clause_heading_candidate(
        _line("Die Anforderungen gelten wie in Abschnitt beschrieben, siehe auch die Tabelle 5.4.1.8", 400, 412)) is None
    # bare top-level integer ("1 Scope" handled by role gold set, not this probe)
    assert acc.clause_heading_candidate(_line("1 Scope", 400, 412)) is None


def test_heading_matches_by_clause_id_or_text():
    assert acc.heading_matches("5.3.4", "Grenzwertklassen 5.3.4", {"5.3.4"}, [])
    assert acc.heading_matches("9.9.9", "Prüfaufbau 9.9.9", set(), ["Prüfaufbau 9.9.9 extra"])
    assert not acc.heading_matches("9.9.9", "Prüfaufbau 9.9.9", set(), ["unrelated"])


def test_has_strong_math():
    assert acc.has_strong_math("a_eff = √(∑ a_i²)")
    assert not acc.has_strong_math("limit ≤ 40 dBµV/m at 30-230 MHz")  # units/comparators alone


def test_extracted_by_page_attributes_cells_to_their_own_page():
    # A stitched table node lives on page 7 but has cells from pages 7 and 8;
    # each cell's text must be attributed to its own page.
    from app.cli.accuracy_check import _extracted_by_page
    from canonical_schema import CanonicalEdition, Cell, Node, Provenance

    prov = Provenance(page=7, bbox=(0, 0, 1, 1), parser="docling", model_version="v1", confidence=0.9)
    table = Node(id="t", type="table", provenance=prov, cells=[
        Cell(row=0, col=0, text="Parameters", page=7),
        Cell(row=1, col=0, text="Electrical slow transient", page=8),
    ])
    root = Node(id="r", type="section", provenance=prov, children=[table])
    edition = CanonicalEdition(edition_id="e", source_sha256="s", schema_version="1.0", root=root)

    by_page = _extracted_by_page(edition)
    assert "Parameters" in " ".join(by_page.get(7, []))
    assert "Electrical slow transient" in " ".join(by_page.get(8, []))
    assert "Electrical slow transient" not in " ".join(by_page.get(7, []))


def test_check_document_gold_source_scores_against_original_not_scanned_copy(tmp_path):
    # A text-layer-free "scanned" copy has no lines of its own to score
    # against; --gold-source must pull source lines from the ORIGINAL PDF
    # instead (see tests/fixtures/make_scanned_pdf.py). No Docling/OCR run
    # needed here -- the edition is hand-built and pre-seeded into the store,
    # isolating just the accuracy_check plumbing.
    import fitz
    from canonical_schema import CanonicalEdition, Node, Provenance
    from app.cli.accuracy_check import check_document
    from app.store.artifact_store import ArtifactStore, compute_key
    from app.version import PIPELINE_VERSION

    original = fitz.open()
    page = original.new_page()
    page.insert_text((72, 700), "The device shall comply with the limit.")
    original_bytes = original.tobytes()
    original.close()

    scanned = fitz.open()
    scanned.new_page()  # blank page, no text layer -- stands in for a raster-wrapped copy
    scanned_bytes = scanned.tobytes()
    scanned.close()

    prov = Provenance(page=1, bbox=(72, 700, 400, 712), parser="ocr",
                      model_version="v1", confidence=0.6)
    root = Node(id="r", type="section", provenance=prov, children=[
        Node(id="t", type="paragraph", text="The device shall comply with the limit.", provenance=prov),
    ])
    edition = CanonicalEdition(edition_id="e", source_sha256="s", schema_version="1.0", root=root)

    store = ArtifactStore(tmp_path)
    store.put_edition(compute_key(scanned_bytes, PIPELINE_VERSION), edition)

    scanned_path = tmp_path / "scanned.pdf"
    original_path = tmp_path / "original.pdf"
    scanned_path.write_bytes(scanned_bytes)
    original_path.write_bytes(original_bytes)

    result = check_document(scanned_path, store, gold_source=original_path)
    assert result.pages == 1
    assert result.mean_coverage == 1.0  # extraction matches the ORIGINAL's real text exactly
