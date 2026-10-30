"""Shared fixtures: the CC0 Pocket Endgame Primer packed as an EPUB, and small PDFs drawn for the
tests."""

from pathlib import Path

import pymupdf
import pytest
from support import PRIMER, png, write_epub


@pytest.fixture(scope="session")
def primer(tmp_path_factory: pytest.TempPathFactory) -> Path:
    files = {
        path.relative_to(PRIMER).as_posix(): path.read_bytes()
        for path in sorted(PRIMER.rglob("*"))
        if path.is_file() and not path.name.startswith(".") and path.name != "mimetype"
    }
    return write_epub(tmp_path_factory.mktemp("epub") / "pocket-primer.epub", files)


@pytest.fixture(scope="session")
def two_pages(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A PDF of two 400 by 600 point pages, each page a chapter in the outline: the first has a
    line of moves and a 160-point picture at (40, 120), the second an exercise."""
    doc = pymupdf.open()
    first = doc.new_page(width=400, height=600)
    first.insert_text((40, 60), "Chapter 1 Opening", fontsize=16)
    first.insert_text((40, 100), "1. e4 e5 2. Nf3 Nc6", fontsize=11)
    first.insert_image(pymupdf.Rect(40, 120, 200, 280), stream=png(32, 32, 128))
    second = doc.new_page(width=400, height=600)
    second.insert_text((40, 60), "Chapter 2 Endgame", fontsize=16)
    second.insert_text((40, 100), "Exercise 1: White to move", fontsize=11)
    doc.set_toc([[1, "Chapter 1 Opening", 1], [1, "Chapter 2 Endgame", 2]])
    doc.set_metadata({"title": "Two Pages", "author": "pgnify tests"})
    path = tmp_path_factory.mktemp("pdf") / "two-pages.pdf"
    doc.save(path)
    return path


@pytest.fixture(scope="session")
def repeated_pictures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A PDF page with three pictures in reading order: one, another, and the first again, as a
    book repeats a figurine's picture."""
    first, other = png(16, 16, 64), png(16, 16, 192)
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    for top, picture in zip((60, 200, 340), (first, other, first), strict=True):
        page.insert_text((40, top - 10), f"Move at {top}", fontsize=11)
        page.insert_image(pymupdf.Rect(40, top, 72, top + 32), stream=picture)
    path = tmp_path_factory.mktemp("pdf") / "repeated-pictures.pdf"
    doc.save(path)
    return path


@pytest.fixture(scope="session")
def inline_picture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A PDF page laid out as PyMuPDF lays out an EPUB with figurines: "2 ", a small picture and
    " c3" on one line; a line of prose; a diagram; and a line after it."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    x = 40 + pymupdf.get_text_length("2 ", fontsize=11)
    page.insert_text((40, 100), "2 ", fontsize=11)
    figurine_box = pymupdf.Rect(x, 91, x + 9, 102)  # inside the text line on baseline 100
    page.insert_image(figurine_box, stream=png(18, 22, 64))
    page.insert_text((x + 9, 100), " c3", fontsize=11)
    page.insert_text((40, 120), "White develops.", fontsize=11)
    page.insert_image(pymupdf.Rect(40, 130, 200, 290), stream=png(64, 64, 192))
    page.insert_text((40, 310), "After the diagram.", fontsize=11)
    path = tmp_path_factory.mktemp("pdf") / "inline-picture.pdf"
    doc.save(path)
    return path


@pytest.fixture(scope="session")
def long_pages(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A PDF of twelve pages of about 2,700 characters each, together more than one doc_text call
    gives."""
    doc = pymupdf.open()
    for number in range(1, 13):
        page = doc.new_page(width=400, height=1200)
        for line in range(45):
            page.insert_text(
                (20, 20 + 26 * line), f"Page {number} line {line} " + "x" * 44, fontsize=6
            )
    path = tmp_path_factory.mktemp("pdf") / "long-pages.pdf"
    doc.save(path)
    return path


@pytest.fixture(scope="session")
def hyphenated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A PDF page whose first line ends in a hyphen, within a compound."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    for index, line in enumerate(["The pawn is a well-", "known runner, as everyone sees."]):
        page.insert_text((40, 60 + 16 * index), line, fontsize=11)
    path = tmp_path_factory.mktemp("pdf") / "hyphenated.pdf"
    doc.save(path)
    return path
