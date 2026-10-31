import re
from pathlib import Path

import pymupdf
import pytest
from support import EXERCISES_PAGE, PRIMER, PRIMER_PAGES, png, write_epub, write_text_epub

from pgnify.doc_tools import doc_images, doc_info, doc_search, doc_text, page_lines

PICTURES = PRIMER / "OEBPS" / "images"
RETI = {"level": 2, "title": "1.2 The Réti Manoeuvre", "page": 8}
CHAPTER_1 = "Chapter 1 The Square of the Pawn"
# PyMuPDF's layout of an EPUB page, in points.
EPUB_PAGE = (400, 600)


def test_doc_info_gives_an_epubs_pages_contents_and_metadata(primer: Path) -> None:
    info = doc_info(str(primer))
    assert info["page_count"] == PRIMER_PAGES
    assert RETI in info["toc"]
    assert info["metadata"]["title"] == "The Pocket Endgame Primer"
    assert info["metadata"]["author"] == "Hester Quill"


def test_doc_info_gives_a_pdfs_pages_contents_and_metadata(two_pages: Path) -> None:
    info = doc_info(str(two_pages))
    assert info["page_count"] == 2
    assert info["toc"] == [
        {"level": 1, "title": "Chapter 1 Opening", "page": 1},
        {"level": 1, "title": "Chapter 2 Endgame", "page": 2},
    ]
    assert info["metadata"]["title"] == "Two Pages"


def test_doc_text_gives_an_epub_page_with_the_contents_entries_it_falls_under(primer: Path) -> None:
    pages = doc_text(str(primer), 9)["pages"]
    assert [page["page"] for page in pages] == [9]
    assert pages[0]["section"] == [CHAPTER_1, RETI["title"]]
    assert "1.Kg7! h4 2.Kf6 Kb6" in pages[0]["text"]


def test_doc_text_gives_each_page_of_a_pdf_range(two_pages: Path) -> None:
    pages = doc_text(str(two_pages), 1, 2)["pages"]
    assert [page["page"] for page in pages] == [1, 2]
    assert [page["section"] for page in pages] == [["Chapter 1 Opening"], ["Chapter 2 Endgame"]]
    assert "1. e4 e5 2. Nf3 Nc6" in pages[0]["text"]


def test_doc_text_cuts_a_long_range_after_the_last_whole_page_that_fits(long_pages: Path) -> None:
    result = doc_text(str(long_pages), 1, 12)
    given = sum(len(page["text"]) for page in result["pages"])
    following = len(doc_text(str(long_pages), result["next_page"])["pages"][0]["text"])
    assert [page["page"] for page in result["pages"]] == list(range(1, result["next_page"]))
    assert given <= 20_000 < given + following


def test_doc_text_continues_from_the_page_where_it_cut_a_range(long_pages: Path) -> None:
    first = doc_text(str(long_pages), 1, 12)
    rest = doc_text(str(long_pages), first["next_page"], 12)
    pages = [page["page"] for page in first["pages"] + rest["pages"]]
    assert pages == list(range(1, 13))
    assert rest["next_page"] is None


def test_doc_text_gives_no_next_page_when_the_whole_range_fits(primer: Path) -> None:
    assert doc_text(str(primer), 1, PRIMER_PAGES)["next_page"] is None


def test_doc_text_refuses_a_range_past_the_last_page_even_one_it_would_cut_short(
    long_pages: Path,
) -> None:
    with pytest.raises(ValueError, match="no page 99: pages run from 1 to 12"):
        doc_text(str(long_pages), 1, 99)


def test_doc_text_refuses_a_range_that_runs_backwards(primer: Path) -> None:
    with pytest.raises(ValueError, match="pages 10 to 5 run backwards"):
        doc_text(str(primer), 10, 5)


def test_doc_text_marks_each_picture_where_it_sits_in_an_epub_page(primer: Path) -> None:
    text = doc_text(str(primer), EXERCISES_PAGE)["pages"][0]["text"]
    order = ["Exercise 1", "[picture 1:", "White to move", "[picture 2:", "Exercise 2"]
    places = [text.index(part) for part in order]
    assert places == sorted(places)


def test_doc_text_marks_a_picture_on_a_pdf_page(two_pages: Path) -> None:
    text = doc_text(str(two_pages), 1)["pages"][0]["text"]
    assert text.index("1. e4 e5 2. Nf3 Nc6") < text.index("[picture 1:")


def test_doc_text_gives_a_picture_the_same_id_wherever_it_appears(repeated_pictures: Path) -> None:
    text = doc_text(str(repeated_pictures), 1)["pages"][0]["text"]
    marks = re.findall(r"\[picture (\d+): ([0-9a-f]{6})\]", text)
    assert [number for number, _ in marks] == ["1", "2", "3"]
    first, other, again = (picture for _, picture in marks)
    assert first == again
    assert first != other


def test_doc_text_marks_a_picture_inside_the_line_of_text_it_sits_in(inline_picture: Path) -> None:
    lines = doc_text(str(inline_picture), 1)["pages"][0]["text"].splitlines()
    assert re.fullmatch(r"2 \[picture 1: [0-9a-f]{6}\] c3", lines[0]), lines
    assert lines[1] == "White develops."
    assert re.fullmatch(r"\[picture 2: [0-9a-f]{6}\]", lines[2]), lines
    assert lines[3] == "After the diagram."


def test_doc_text_keeps_a_hyphen_that_ends_a_line_and_a_caption_as_laid_out(
    reflowed: Path,
) -> None:
    """The report reads these both ways when it compares comments with the book; the text the
    model is given stays as it is, but for the empty lines before its paragraphs."""
    lines = [line for line in doc_text(str(reflowed), 1)["pages"][0]["text"].splitlines() if line]
    assert lines[:2] == ["White aims to win the e6-", "pawn by doubling rooks on the d-file."]
    assert re.fullmatch(r"\[picture 1: [0-9a-f]{6}\]", lines[5]), lines
    assert lines[6:8] == ["DIAGRAM 2. White to play", "trade the bishops and attack the weak pawn."]


KNIGHT = (
    "<h2>The Knight</h2>"
    "<p>The knight is the only piece that jumps, and it moves the same way in every position it "
    "reaches on the board.</p>"
    "<p>Amateurs distrust it because:</p>"
    "<ul><li>it forks their pieces;</li><li>they misuse it.</li></ul>"
    "<p>DIAGRAM 1. White to play<br/>Smith-Jones, London 1900</p>"
)
FIGURINE = '<img src="board.png" alt="N" style="width:8px;height:8px"/>'


def epub_text(tmp_path: Path, body: str, numbered: bool = False) -> list[str]:
    """The text of each page doc_text gives for an EPUB of the body."""
    epub = str(write_text_epub(tmp_path / "book.epub", body))
    pages = doc_text(epub, 1, doc_info(epub)["page_count"], numbered=numbered)["pages"]
    return [page["text"] for page in pages]


def test_doc_text_puts_an_empty_line_before_each_paragraph_heading_and_list_item_of_an_epub(
    tmp_path: Path,
) -> None:
    (text,) = epub_text(tmp_path, KNIGHT)
    blocks = text.removeprefix("\n").removesuffix("\n").split("\n\n")
    assert blocks[:3] == [
        "The Knight",
        "The knight is the only piece that jumps, and it moves the same way\n"
        "in every position it reaches on the board.",
        "Amateurs distrust it because:",
    ]
    assert [block.split()[-1] for block in blocks[3:5]] == ["pieces;", "it."]
    # A line break within a paragraph has no empty line before it.
    assert blocks[5] == "DIAGRAM 1. White to play\nSmith-Jones, London 1900"


def test_doc_text_shows_whether_an_epub_page_goes_on_with_a_paragraph(tmp_path: Path) -> None:
    sentences = " ".join(f"Sentence {number} of a long paragraph." for number in range(200))
    pages = epub_text(tmp_path, f"<p>{sentences}</p><p>A new paragraph after it.</p>")
    assert len(pages) > 1
    assert pages[0].startswith("\nSentence 0 ")
    assert not any(page.startswith("\n") for page in pages[1:])
    assert pages[-1].endswith("\n\nA new paragraph after it.\n")


def test_doc_text_begins_a_paragraph_at_a_figurine_that_opens_it_not_at_one_inside_it(
    tmp_path: Path,
) -> None:
    body = f"<p>{FIGURINE}f3 develops a piece.</p><p>After 1 e4 e5 2 {FIGURINE}f3 hits e5.</p>"
    (text,) = epub_text(tmp_path, body)
    picture = r"\[picture \d: [0-9a-f]{6}\]"
    assert re.fullmatch(
        rf"\n{picture}f3 develops a piece\.\n\nAfter 1 e4 e5 2 {picture}f3 hits e5\.\n", text
    ), text


def test_doc_text_numbers_an_epubs_lines_as_spans_name_them_beside_its_paragraphs(
    tmp_path: Path,
) -> None:
    (text,) = epub_text(tmp_path, KNIGHT, numbered=True)
    numbered = [line for line in text.splitlines() if line]
    lines = page_lines(str(tmp_path / "book.epub"), 1)
    assert numbered == [f"{index}| {line}" for index, line in enumerate(lines, start=1)]
    assert text.startswith("\n1| The Knight\n\n2| ")


def test_doc_text_adds_no_empty_line_to_a_pdf(two_pages: Path) -> None:
    for page in doc_text(str(two_pages), 1, 2)["pages"]:
        assert not page["text"].startswith("\n") and "\n\n" not in page["text"]


def test_doc_search_finds_text_in_an_epub_with_its_page_box_and_line(primer: Path) -> None:
    hits = doc_search(str(primer), "Kxc6")["hits"]
    assert [(hit["page"], hit["section"]) for hit in hits] == [(9, [CHAPTER_1, RETI["title"]])]
    assert hits[0]["line"] == "3.Ke5! Kxc6 4.Kf4 h3 5.Kg3 h2 6.Kxh2"
    x0, y0, x1, y1 = hits[0]["box"]
    width, height = EPUB_PAGE
    assert 0 < x0 < x1 < width and 0 < y0 < y1 < height


def test_doc_search_finds_text_in_a_pdf(two_pages: Path) -> None:
    hits = doc_search(str(two_pages), "Exercise 1")["hits"]
    assert [(hit["page"], hit["section"]) for hit in hits] == [(2, ["Chapter 2 Endgame"])]
    assert hits[0]["line"] == "Exercise 1: White to move"


def test_doc_search_refuses_an_empty_query(primer: Path) -> None:
    with pytest.raises(ValueError, match="the query is empty"):
        doc_search(str(primer), " ")


def test_doc_images_extracts_the_pictures_on_an_epub_page_byte_for_byte(
    primer: Path, tmp_path: Path
) -> None:
    images = doc_images(str(primer), EXERCISES_PAGE, str(tmp_path / "images"))["images"]
    assert [Path(image["path"]).read_bytes() for image in images] == [
        (PICTURES / "exercise1.gif").read_bytes(),
        (PICTURES / "exercise2.png").read_bytes(),
    ]


def test_doc_images_extracts_a_pdf_pages_picture_with_its_box(
    two_pages: Path, tmp_path: Path
) -> None:
    images = doc_images(str(two_pages), 1, str(tmp_path))["images"]
    assert [image["box"] for image in images] == [[40.0, 120.0, 200.0, 280.0]]
    assert (images[0]["width"], images[0]["height"]) == (32, 32)
    assert pymupdf.Pixmap(images[0]["path"]).width == 32


@pytest.mark.parametrize("lines_before", [0, 30, 46], ids=["top", "middle", "no-room-left"])
def test_doc_images_gives_a_diagram_sized_by_its_height_whole(
    tmp_path: Path, lines_before: int
) -> None:
    """Calibre styles some books' diagrams as height: 100%; width: auto. Before PyMuPDF 2.0 such
    a picture was squeezed into the room left on its page."""
    line = '<p style="margin:0pt; border:0pt; height:1em"> </p>'
    files = {
        "META-INF/container.xml": (PRIMER / "META-INF" / "container.xml").read_bytes(),
        "OEBPS/content.opf": (
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
            'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>Diagrams</dc:title><dc:identifier id="id">pgnify-test</dc:identifier>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="style" href="style.css" media-type="text/css"/>'
            '<item id="board" href="board.png" media-type="image/png"/></manifest>'
            '<spine><itemref idref="text"/></spine></package>'
        ),
        "OEBPS/style.css": (
            ".diagram { display: block; text-align: center }\n"
            ".board { height: 100%; width: auto }\n"
        ),
        "OEBPS/text.xhtml": (
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
            '<link rel="stylesheet" href="style.css" type="text/css"/></head><body>'
            f'{line * lines_before}<div class="diagram"><img src="board.png" alt="images" '
            'class="board"/></div></body></html>'
        ),
        "OEBPS/board.png": png(345, 293, 200),
    }
    epub = write_epub(tmp_path / "diagrams.epub", files)
    pages = doc_info(str(epub))["page_count"]
    (picture,) = [
        picture
        for page in range(1, pages + 1)
        for picture in doc_images(str(epub), page, str(tmp_path / "out"))["images"]
    ]
    _, top, _, bottom = picture["box"]
    assert (picture["width"], picture["height"]) == (345, 293)
    assert bottom - top > 250  # laid out whole, not squeezed to a few points
