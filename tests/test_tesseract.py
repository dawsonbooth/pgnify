"""Tesseract's reading of a page. The tests that run Tesseract are skipped where it is not
installed."""

from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest
from support import SENTENCE, needs_tesseract, scanned_text

from pgnify import tesseract

# Tesseract's TSV, its columns spaced here: two lines of one paragraph, a third line in another
# block, a word that is only a space, and the rows for the page and a block, which hold no word.
TSV = "\n".join(
    "\t".join(row.split("|"))
    for row in [
        "level|page_num|block_num|par_num|line_num|word_num|left|top|width|height|conf|text",
        "1|1|0|0|0|0|0|0|600|800|-1|",
        "2|1|1|0|0|0|10|10|300|60|-1|",
        "5|1|1|1|1|1|10|10|50|20|96.5|The",
        "5|1|1|1|1|2|70|10|60|20|91.0|pawn",
        "5|1|1|1|2|1|10|40|80|20|88.2|queens.",
        "5|1|1|1|2|2|100|40|10|20|95.0| ",
        "5|1|2|1|1|1|400|10|40|20|90.0|7",
    ]
)


def test_lines_gives_tesseracts_lines_in_its_order_with_each_words_box() -> None:
    lines = tesseract.lines(TSV)
    assert [line.text for line in lines] == ["The pawn", "queens.", "7"]
    first = lines[0]
    assert [word.box for word in first.words] == [
        pymupdf.Rect(10, 10, 60, 30),
        pymupdf.Rect(70, 10, 130, 30),
    ]
    assert first.box == pymupdf.Rect(10, 10, 130, 30)


def test_lines_takes_each_words_box_back_to_the_page_by_the_matrix_given() -> None:
    lines = tesseract.lines(TSV, pymupdf.Matrix(0.5, 0.5))
    assert lines[2].words[0].box == pymupdf.Rect(200, 5, 220, 15)


def test_ocr_says_how_to_install_tesseract_where_it_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tesseract.version.cache_clear()
    monkeypatch.setattr(tesseract.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match=r"OCR needs Tesseract.*brew install tesseract"):
        tesseract.version()
    tesseract.version.cache_clear()


@pytest.mark.parametrize(
    ("script", "why"),
    [
        ("echo 'Error opening data file' >&2; exit 1", "Tesseract failed: Error opening data file"),
        ("exit 0", "Tesseract failed: tesseract --version printed nothing"),
        ("sleep 5", "Tesseract gave no answer within 2 seconds"),
    ],
)
def test_ocr_says_why_where_the_tesseract_command_does_not_run(
    fake_tesseract: Callable[[str], None], monkeypatch: pytest.MonkeyPatch, script: str, why: str
) -> None:
    fake_tesseract(script)
    monkeypatch.setattr(tesseract, "TIMEOUT", 2)
    with pytest.raises(RuntimeError, match=why):
        tesseract.version()


@needs_tesseract
def test_read_gives_a_scanned_pages_words_where_they_are_printed(tmp_path: Path) -> None:
    with pymupdf.open(scanned_text(tmp_path, [[SENTENCE]])) as doc:
        (line,) = tesseract.read(doc[0])
    assert line.text == SENTENCE
    # Printed from (40, 60) at 12 points: its box, in points, is where the sentence is.
    assert 35 < line.box.x0 < 45 and 45 < line.box.y0 < 55
