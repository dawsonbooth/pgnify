"""Document tools for the model: plain functions on PyMuPDF, which opens PDF and EPUB alike, each
returning a JSON-serialisable dict. PyMuPDF lays an EPUB out as pages, so an EPUB's page numbers
hold only for this layout."""

import hashlib
from pathlib import Path
from typing import Any

import pymupdf

# The most page text one doc_text call gives, in characters, as its docstring says. Claude Code
# saves a tool result longer than about 50,000 characters to a file instead of passing it on,
# and a model with only these tools cannot read that file.
TEXT_LIMIT = 20_000


def doc_info(path: str) -> dict[str, Any]:
    """Describe a PDF or EPUB: its page count, table of contents and metadata. Pages count from
    1; an EPUB's pages are those of PyMuPDF's layout, not the book's printed numbers.

    Returns page_count; toc, each entry with its level (1 for the top), title and the page it
    starts on; and metadata, such as format, title and author, where the file gives them.
    """
    with pymupdf.open(path) as doc:
        return {
            "page_count": doc.page_count,
            "toc": [
                {"level": level, "title": title, "page": page}
                for level, title, page in doc.get_toc()
            ],
            "metadata": {key: value for key, value in doc.metadata.items() if value},
        }


def doc_text(
    path: str, first_page: int, last_page: int | None = None, numbered: bool = False
) -> dict[str, Any]:
    """Extract the text of a page or a range of pages, both ends included, pages counting from 1.
    Each picture on a page appears in the text at its place, as [picture N: id], inside its line
    when it is part of one, numbered as doc_images numbers them; id, six hex digits, is the same
    wherever the same picture appears in the book. Some books print the pieces in their moves as
    pictures, which have no letter in the text: give a move printed as
    "2 [picture 1: 0ebed3] c3" to validate_moves and build_pgn as "2 [0ebed3]c3", and they read
    which piece the picture is from the moves. Do not work out a picture's piece yourself, from
    its image or otherwise. In an EPUB, an empty line comes before each line that begins a
    paragraph, a heading or an item of a list, as the book's markup marks them, the page's first
    line included: a page whose text does not start with an empty line goes on with the
    paragraph the page before ends, and a line break with no empty line stays within its
    paragraph. One call gives at most 20,000 characters of text, or one longer page: a longer
    range is cut after the last whole page that fits, and next_page says where to continue.

    Returns pages, each with its page number, its section (the table-of-contents entries it falls
    under, outermost first) and its text; and next_page, the first page of the range not given,
    or null when the whole range was.
    """
    last = first_page if last_page is None else last_page
    if last < first_page:
        raise ValueError(f"pages {first_page} to {last} run backwards")
    starts: dict[int, frozenset[int]] = {}
    if Path(path).suffix.lower() == ".epub":
        from pgnify.paragraphs import paragraph_starts

        starts = paragraph_starts(path)
    with pymupdf.open(path) as doc:
        _page(doc, last)  # Check the last page now: a range cut short never reaches it.
        toc = doc.get_toc()
        pages: list[dict[str, Any]] = []
        size = 0
        for number in range(first_page, last + 1):
            text = _given(page_text(_page(doc, number)), starts.get(number, frozenset()), numbered)
            if pages and size + len(text) > TEXT_LIMIT:
                return {"pages": pages, "next_page": number}
            pages.append({"page": number, "section": _section(toc, number), "text": text})
            size += len(text)
        return {"pages": pages, "next_page": None}


def doc_search(path: str, query: str) -> dict[str, Any]:
    """Find text in every page, ignoring case.

    Returns hits in page order, each with its page, its section (the table-of-contents entries
    it falls under, outermost first), its box on the page as [x0, y0, x1, y1] in points, and the
    line of text that holds it.
    """
    if not query.strip():
        raise ValueError("the query is empty")
    with pymupdf.open(path) as doc:
        toc = doc.get_toc()
        hits = []
        for page in doc:
            boxes = page.search_for(query)
            lines = _boxed_lines(page) if boxes else []
            for box in boxes:
                middle = (box.tl + box.br) / 2
                hits.append(
                    {
                        "page": page.number + 1,
                        "section": _section(toc, page.number + 1),
                        "box": [round(value, 1) for value in box],
                        "line": next((text for rect, text in lines if middle in rect), ""),
                    }
                )
        return {"hits": hits}


def doc_images(path: str, page: int, out_dir: str) -> dict[str, Any]:
    """Extract the pictures laid out on a page, such as diagrams, numbered in reading order as
    doc_text marks them.

    Returns the page, its section, and images in reading order, each with path, which
    read_diagram takes; box, where it sits on the page as [x0, y0, x1, y1] in points; and its
    width and height in pixels.
    """
    folder = Path(out_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(path) as doc:
        blocks = [
            block for block in _page(doc, page).get_text("dict")["blocks"] if block["type"] == 1
        ]
        images = []
        for number, block in enumerate(blocks, start=1):
            file = folder / f"page{page}-{number}.{block['ext']}"
            file.write_bytes(block["image"])
            images.append(
                {
                    "path": str(file),
                    "box": [round(value, 1) for value in block["bbox"]],
                    "width": block["width"],
                    "height": block["height"],
                }
            )
        return {"page": page, "section": _section(doc.get_toc(), page), "images": images}


def page_text(page: pymupdf.Page) -> str:
    """The page's text in reading order, with each picture marked where it sits, by its number on
    the page and a digest of its bytes, the same wherever the same picture appears. A picture
    inside a line of text, as a piece printed in a move is, is marked inside the line."""
    parts: list[str] = []
    pictures, last, after_picture = 0, None, False
    for block in page.get_text("dict")["blocks"]:
        if block["type"] == 1:
            pictures += 1
            digest = hashlib.sha1(block["image"]).hexdigest()[:6]
            items = [(block["bbox"], f"[picture {pictures}: {digest}]", True)]
        else:
            items = [(line["bbox"], _line_text(line), False) for line in block["lines"]]
        for box, text, picture in items:
            if last is not None and (picture or after_picture) and _continues(last, box):
                parts[-1] = parts[-1].removesuffix("\n")
            parts.append(text + "\n")
            last, after_picture = box, picture
    return "".join(parts)


def page_lines(path: str, number: int) -> list[str]:
    """The lines of a page's text, as doc_text numbers them from 1."""
    with pymupdf.open(path) as doc:
        return _lines(page_text(_page(doc, number)))


def _lines(text: str) -> list[str]:
    return text.removesuffix("\n").split("\n") if text else []


def _given(text: str, starts: frozenset[int], numbered: bool) -> str:
    """A page's text as doc_text gives it: an empty line before each line, counted from 0, that
    starts begins, and with numbered each line numbered from 1, the empty lines unnumbered, so
    that the numbers stay those of page_lines."""
    return "".join(
        ("\n" if index in starts else "") + (f"{index + 1}| " if numbered else "") + line + "\n"
        for index, line in enumerate(_lines(text))
    )


def _line_text(line: dict[str, Any]) -> str:
    return "".join(span["text"] for span in line["spans"])


def _continues(before: tuple[float, ...], after: tuple[float, ...]) -> bool:
    """Whether the box after continues the line of the box before: to its right, allowing a
    point of overlap, and sharing some of its height."""
    return after[0] >= before[2] - 1 and min(before[3], after[3]) > max(before[1], after[1])


def _page(doc: pymupdf.Document, number: int) -> pymupdf.Page:
    if not 1 <= number <= doc.page_count:
        raise ValueError(f"no page {number}: pages run from 1 to {doc.page_count}")
    return doc[number - 1]


def _section(toc: list[list[Any]], number: int) -> list[str]:
    """The titles of the table-of-contents entries that page `number` falls under: at each level,
    the last entry starting on or before it."""
    section: list[str] = []
    for level, title, page in toc:
        if 1 <= page <= number:
            section = [*section[: level - 1], title]
    return section


def _boxed_lines(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    return [
        (pymupdf.Rect(line["bbox"]), _line_text(line))
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
    ]
