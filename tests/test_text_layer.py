"""Where a book's text layer comes from, by the file's own properties: the five kinds of book
pgnify reads, made small for the tests."""

from pathlib import Path

import pytest
from support import born_digital, clearscan, image_only, ocr_scan

from pgnify.text_layer import text_layer


@pytest.mark.parametrize(
    ("make", "machine_read", "named", "pages_with_text", "over_scans"),
    [
        (clearscan, True, True, 3, 0),
        (ocr_scan, True, False, 3, 3),
        (image_only, False, False, 0, 0),
        (born_digital, False, False, 4, 1),
    ],
    ids=["clearscan", "ocr-scan", "image-only", "born-digital"],
)
def test_a_pdfs_text_layer_is_told_by_its_producer_and_its_pages_pictures(
    tmp_path: Path, make, machine_read: bool, named: bool, pages_with_text: int, over_scans: int
) -> None:
    layer = text_layer(make(tmp_path))
    assert layer.machine_read is machine_read
    assert (layer.tool is not None) is named
    assert (len(layer.with_text), len(layer.over_scans)) == (pages_with_text, over_scans)


def test_an_epubs_text_layer_is_its_own_text(primer: Path) -> None:
    layer = text_layer(primer)
    assert not layer.machine_read and layer.tool is None and layer.with_text


def test_the_evidence_names_what_the_file_shows_and_no_more(tmp_path: Path) -> None:
    named = text_layer(clearscan(tmp_path))
    assert "Paper Capture Plug-in with ClearScan" in named.evidence()
    assert "picture" not in named.evidence()
    pictured = text_layer(ocr_scan(tmp_path))
    assert "3 of its 3 pages with text" in pictured.evidence()
    assert all(pictured.machine_read_page(page) for page in (1, 2, 3))
    assert not text_layer(born_digital(tmp_path)).machine_read_page(1)
