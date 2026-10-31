"""Where an EPUB's paragraphs begin on the lines of its pages' text. An EPUB's XHTML marks each
paragraph, heading and item of a list as a block, and PyMuPDF lays each block out from a new
line. A line begins a block where its text, white space aside, starts at a block's start in the
spine's text: the line is found there by its own text, so no paragraph is inferred from the
layout, and a line that cannot be found is left unmarked. The layout draws a list's marker,
such as a bullet, that the XHTML does not hold, so a line whose first short word cannot be found
is looked for again without it."""

import functools
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from posixpath import dirname, join, normpath

import pymupdf

from pgnify.doc_tools import _lines, page_text

# Elements a browser lays out as blocks, each from a new line, by default.
BLOCKS = frozenset(
    {
        *("address", "article", "aside", "blockquote", "body", "caption", "dd", "div", "dl"),
        *("dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header"),
        *("hr", "li", "nav", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul"),
    }
)
HIDDEN = frozenset({"head", "script", "style", "title"})
PICTURE = re.compile(r"\[picture \d+: [0-9a-f]{6}\]")
SPACE = re.compile(r"\s+")
# How far ahead in the spine's text a line is looked for, in characters: a short line only near
# where the line before it ended, so that the same few characters later on are not taken for it.
SHORT, NEAR, FAR = 12, 400, 20_000
# The longest first word of a line that may be a list's marker, as "•" or "12.".
MARKER = 3


def paragraph_starts(path: str) -> dict[int, frozenset[int]]:
    """For each page of the EPUB at path, counting from 1, the lines of its text, counting from
    0, that begin a block of the book's markup; empty if the markup cannot be read."""
    file = Path(path).resolve()
    stat = file.stat()
    return _starts(str(file), stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=4)
def _starts(path: str, _mtime: int, _size: int) -> dict[int, frozenset[int]]:
    """paragraph_starts, kept for the file as it was when last changed."""
    try:
        text, starts = _markup(path)
    except (KeyError, zipfile.BadZipFile):
        return {}
    found: dict[int, frozenset[int]] = {}
    position = 0
    with pymupdf.open(path) as doc:
        for page in doc:
            begun = set()
            for index, line in enumerate(_lines(page_text(page))):
                if (at := _find(text, line, position)) is None:
                    continue
                if at[0] in starts:
                    begun.add(index)
                position = at[1]
            found[page.number + 1] = frozenset(begun)
    return found


def _find(text: str, line: str, position: int) -> tuple[int, int] | None:
    """Where the line's text, white space aside, is found in the spine's text from position on,
    as its start and end, or None."""
    plain = PICTURE.sub("", line)
    first, _, rest = plain.strip().partition(" ")
    for tried in [plain] + ([rest] if rest and len(first) <= MARKER else []):
        chars = SPACE.sub("", tried.replace("­", ""))
        if not chars:
            return None
        ahead = NEAR if len(chars) < SHORT else FAR
        start = text.find(chars, position, position + ahead + len(chars))
        if start >= 0:
            return start, start + len(chars)
    return None


def _markup(path: str) -> tuple[str, set[int]]:
    """The spine's text, without white space, and where in it each block's text begins."""
    with zipfile.ZipFile(path) as book:
        container = book.read("META-INF/container.xml").decode("utf-8", errors="replace")
        if (package := re.search(r'full-path="([^"]+)"', container)) is None:
            raise KeyError("META-INF/container.xml names no package")
        opf = book.read(package[1]).decode("utf-8", errors="replace")
        items = {}
        for tag in re.findall(r"<item\b[^>]*>", opf):
            ident, href = re.search(r'\bid="([^"]+)"', tag), re.search(r'\bhref="([^"]+)"', tag)
            if ident and href:
                items[ident[1]] = href[1]
        text: list[str] = []
        starts: set[int] = set()
        size = 0
        for idref in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf):
            if idref not in items:
                continue
            blocks = _Blocks()
            name = normpath(join(dirname(package[1]), items[idref]))
            blocks.feed(book.read(name).decode("utf-8", errors="replace"))
            blocks.close()
            starts |= {size + start for start in blocks.starts}
            text += blocks.text
            size += blocks.size
    return "".join(text), starts


class _Blocks(HTMLParser):
    """An XHTML document's text, without white space, and the offsets where a block's text
    begins."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.size = 0
        self.starts: set[int] = set()
        # Whether a block has opened or closed since the last text.
        self.opened = True
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in HIDDEN:
            self.hidden += 1
        elif tag in BLOCKS:
            self.opened = True

    def handle_endtag(self, tag: str) -> None:
        if tag in HIDDEN:
            self.hidden = max(0, self.hidden - 1)
        elif tag in BLOCKS:
            self.opened = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCKS:
            self.opened = True

    def handle_data(self, data: str) -> None:
        chars = SPACE.sub("", data.replace("­", ""))
        if self.hidden or not chars:
            return
        if self.opened:
            self.starts.add(self.size)
            self.opened = False
        self.text.append(chars)
        self.size += len(chars)
