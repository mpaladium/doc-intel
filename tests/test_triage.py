import fitz

from app.pipeline import triage


def _pdf_with_text(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=10)
    return doc.tobytes()


def test_digital_clean_page_classified_clean():
    pdf_bytes = _pdf_with_text("This is a perfectly normal sentence of extracted text.")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    result = triage.classify_page(doc[0])
    assert result.page_class == "DIGITAL_CLEAN"


def test_garbage_heavy_page_classified_dirty():
    pdf_bytes = _pdf_with_text(
        "���� garbled (cid:12)(cid:13) text with enough characters to clear "
        "the minimum digital-text threshold ��"
    )
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    result = triage.classify_page(doc[0])
    assert result.page_class == "DIGITAL_DIRTY"


def test_blank_page_without_images_is_uncertain():
    doc = fitz.open()
    doc.new_page()
    result = triage.classify_page(doc[0])
    assert result.page_class == "UNCERTAIN"
