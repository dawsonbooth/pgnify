"""Helpers and constants shared by the test modules."""

import asyncio
import shutil
import zipfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from mcp import Client
from mcp.client.session import ClientRequestContext
from mcp.types import CallToolResult, ElicitRequestParams, ElicitResult

from pgnify.chess_tools import MoveSpec, render_board
from pgnify.server import build_server

ROOT = Path(__file__).parents[1]
# The test book, unpacked; the fixture primer packs it as an EPUB.
PRIMER = ROOT / "data" / "fixtures" / "epub" / "pocket-primer"
# The primer as PyMuPDF lays it out: its pages, and the page with the diagrams of its first two
# exercises.
PRIMER_PAGES = 14
EXERCISES_PAGE = 12

# After 1.e4 e5 2.Nf3 Nc6 3.Bb5.
RUY = "r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
# White to play Nh7+, forking the king and the queen.
FORK = "5k2/p7/1p3qp1/2p3Np/2PnPP1P/8/P2Q1K2/8 w - - 0 2"
# A placement alone, from which only Black can play Ne2+ Kf1 Nxc1.
BLACK_FORK = "8/6pk/5b1p/p2p4/1p1n2N1/1P6/P4PPP/2B3K1"
ONE_GAME = '[Result "*"]\n\n1. e4 *'


def move_specs(*sans: str) -> list[MoveSpec]:
    """A line of moves given by their SAN alone."""
    return [{"san": san} for san in sans]


def png(width: int, height: int, shade: int) -> bytes:
    """A grey picture as PNG, its shade from 0 for black to 255 for white."""
    picture = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    picture.clear_with(shade)
    return picture.tobytes("png")


def write_epub(destination: Path, files: Mapping[str, str | bytes]) -> Path:
    """Zip an EPUB's files the same way every time, so that its hash never changes.

    The mimetype entry comes first and uncompressed, as the EPUB container requires.
    """
    stamp = (2026, 9, 21, 0, 0, 0)
    with zipfile.ZipFile(destination, "w") as epub:
        epub.writestr(zipfile.ZipInfo("mimetype", stamp), "application/epub+zip")
        for name, data in files.items():
            info = zipfile.ZipInfo(name, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            epub.writestr(info, data)
    return destination


def write_text_epub(destination: Path, body: str) -> Path:
    """An EPUB of one XHTML file with the body given, whose pictures may show board.png, a grey
    square."""
    files = {
        "META-INF/container.xml": (PRIMER / "META-INF" / "container.xml").read_bytes(),
        "OEBPS/content.opf": (
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
            'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>Text</dc:title><dc:identifier id="id">pgnify-test</dc:identifier>'
            "<dc:language>en</dc:language></metadata><manifest>"
            '<item id="text" href="text.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="board" href="board.png" media-type="image/png"/></manifest>'
            '<spine><itemref idref="text"/></spine></package>'
        ),
        "OEBPS/text.xhtml": (
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head></head>'
            f"<body>{body}</body></html>"
        ),
        "OEBPS/board.png": png(160, 160, 200),
    }
    return write_epub(destination, files)


class Person:
    """A person at a client that can ask. It answers by the question's subject (a book to read, a
    folder to write in, a file to replace) with "yes", "unticked", "decline" or "cancel", and
    records each question and form."""

    def __init__(self, book: str = "yes", folder: str = "yes", replace: str = "yes"):
        self.answers = {"read": book, "write": folder, "replace": replace}
        self.asked: list[str] = []
        self.forms: list[Any] = []

    async def __call__(
        self, context: ClientRequestContext, params: ElicitRequestParams
    ) -> ElicitResult:
        self.asked.append(params.message)
        self.forms.append(getattr(params, "requested_schema", None))
        if "read this book" in params.message:
            action = self.answers["read"]
        elif "replace this file" in params.message:
            action = self.answers["replace"]
        else:
            action = self.answers["write"]
        if action == "decline" or action == "cancel":
            return ElicitResult(action=action)
        return ElicitResult(action="accept", content={"allow": action == "yes"})


# The two eras of the protocol that a server answers in: a question asked as a request to the
# client in the middle of a call (up to 2025-11-25), and as a result that asks for input and a
# retry of the call (from 2026-07-28).
ERAS = ["legacy", "2026-07-28"]


def in_session[T](
    books: Path | None,
    out: Path | None,
    act: Callable[[Client], Awaitable[T]],
    spanned: bool = False,
    *,
    state: Path | None = None,
    person: Person | None = None,
    era: str = "auto",
) -> T:
    """Run act with the SDK's in-process client, connected to a server for the books at books and
    the output folder out, and return its result. The server keeps its records in state when out
    is None. With a person the client can ask questions, and speaks the protocol of the era."""

    async def run() -> T:
        server = build_server(books, out, spanned=spanned, state=state)
        async with Client(server, mode=era, elicitation_callback=person) as client:
            return await act(client)

    return asyncio.run(run())


def call_tools(
    books: Path | None,
    out: Path | None,
    calls: list[tuple[str, dict[str, Any]]],
    spanned: bool = False,
    *,
    state: Path | None = None,
    person: Person | None = None,
    era: str = "auto",
) -> list[CallToolResult]:
    """Call the tools in turn, in one session."""

    async def call(client: Client) -> list[CallToolResult]:
        return [await client.call_tool(name, arguments) for name, arguments in calls]

    return in_session(books, out, call, spanned, state=state, person=person, era=era)


# The four kinds of PDF pgnify reads, made small: a scan whose text ClearScan replaced, a scan with
# an invisible OCR layer, a scan of pictures alone, and a typeset book with a cover.
SENTENCE = "The isolated pawn gives White open lines for an attack on the king."


def clearscan(folder: Path, texts: list[str] | None = None) -> Path:
    """A scan whose text Acrobat's ClearScan replaced: visible text, a page for each of the
    texts, no picture of the page, and a producer that names the tool."""
    doc = pymupdf.open()
    for text in texts or [SENTENCE] * 3:
        doc.new_page(width=400, height=600).insert_textbox(
            pymupdf.Rect(40, 60, 360, 560), text, fontsize=11
        )
    doc.set_metadata({"producer": "Adobe Acrobat Pro 11.0.12 Paper Capture Plug-in with ClearScan"})
    path = folder / "clearscan.pdf"
    doc.save(path)
    return path


def ocr_scan(folder: Path) -> Path:
    """A scan with an invisible OCR layer over each page's picture, and no producer named."""
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page(width=400, height=600)
        page.insert_image(page.rect, stream=png(40, 60, 230))
        page.insert_text((40, 80), SENTENCE, fontsize=11, render_mode=3)
    path = folder / "ocr-scan.pdf"
    doc.save(path)
    return path


def image_only(folder: Path) -> Path:
    """A scan of pictures alone, with no text layer."""
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page(width=400, height=600)
        page.insert_image(page.rect, stream=png(40, 60, 230))
    doc.set_metadata({"producer": "Adobe Acrobat 8.12 Image Conversion Plug-in"})
    path = folder / "image-only.pdf"
    doc.save(path)
    return path


def born_digital(folder: Path, texts: list[str] | None = None) -> Path:
    """A typeset PDF whose first page is a cover, a picture of the whole page, and a page for
    each of the texts after it."""
    doc = pymupdf.open()
    cover = doc.new_page(width=400, height=600)
    cover.insert_image(cover.rect, stream=png(40, 60, 90))
    cover.insert_text((40, 80), "The Isolani", fontsize=24)
    for text in texts or [SENTENCE] * 3:
        doc.new_page(width=400, height=600).insert_textbox(
            pymupdf.Rect(40, 60, 360, 560), text, fontsize=11
        )
    doc.set_metadata({"producer": "LuaTeX-1.17.0", "creator": "LaTeX with hyperref"})
    path = folder / "born-digital.pdf"
    doc.save(path)
    return path


# A page of two exercises, as a book of exercises prints them: each board with its number and
# whose move it is to its left, its players under it, and prose under the boards.
SKEWER = "6k1/5ppp/8/8/8/8/1Q3PPP/6K1 w - - 0 1"
EXERCISES = (
    (pymupdf.Rect(70, 80, 220, 230), FORK, ("1", "W"), "Fork Example"),
    (pymupdf.Rect(270, 80, 420, 230), SKEWER, ("2", "B"), "Skewer Example"),
)
PROSE = "Both exercises show a piece that attacks two targets at once."


def exercises(
    folder: Path, scanned: bool = False, rotated: bool = False, boxed: bool = False
) -> Path:
    """A PDF page of the two EXERCISES and PROSE under them, set as text; or, scanned, the page
    drawn at 200 dpi and kept as a picture alone, with no text layer; or turned a quarter; or
    with a frame drawn round both exercises, as a test grid's is."""
    doc = pymupdf.open()
    page = doc.new_page(width=460, height=600)
    page.insert_text((40, 50), "Exercises", fontsize=18)
    for number, (box, fen, (label, side), players) in enumerate(EXERCISES, start=1):
        board = folder / f"board-{number}.png"
        render_board(fen, board)
        page.insert_image(box, filename=str(board))
        page.insert_text((box.x0 - 30, box.y0 + 14), label, fontsize=12)
        page.insert_text((box.x0 - 30, box.y0 + 30), side, fontsize=12)
        page.insert_text((box.x0 + 10, box.y1 + 18), players, fontsize=11)
    page.insert_text((40, 300), PROSE, fontsize=12)
    if boxed:
        page.draw_rect(pymupdf.Rect(30, 70, 430, 262), color=(0, 0, 0), width=1.5)
    if scanned:
        doc, page = _scanned(page)
    if rotated:
        page.set_rotation(90)
    kinds = (("-scanned", scanned), ("-rotated", rotated), ("-boxed", boxed))
    path = folder / f"exercises{''.join(name for name, on in kinds if on)}.pdf"
    doc.save(path)
    return path


def scanned_text(folder: Path, columns: list[list[str]], hidden: str | None = None) -> Path:
    """A scanned page of text, a picture alone: the columns, each a list of lines, side by side;
    and with hidden, an invisible text layer over it that says something else."""
    doc = pymupdf.open()
    page = doc.new_page(width=460, height=600)
    width = (460 - 80) / len(columns)
    for index, lines in enumerate(columns):
        for row, line in enumerate(lines):
            page.insert_text((40 + index * width, 60 + 18 * row), line, fontsize=12)
    doc, page = _scanned(page)
    if hidden is not None:
        page.insert_text((40, 60), hidden, fontsize=12, render_mode=3)
    path = folder / f"scanned-text-{len(columns)}{'-hidden' if hidden else ''}.pdf"
    doc.save(path)
    return path


def _scanned(page: pymupdf.Page) -> tuple[pymupdf.Document, pymupdf.Page]:
    """A new PDF of one page, the page drawn at 200 dpi and kept as a picture alone."""
    picture = page.get_pixmap(dpi=200)
    doc = pymupdf.open()
    scan = doc.new_page(width=page.rect.width, height=page.rect.height)
    scan.insert_image(scan.rect, stream=picture.tobytes("png"))
    return doc, scan


def stored_sideways(folder: Path, source: Path) -> Path:
    """The first page of source stored turned a quarter, with a rotation that shows it upright,
    as some scanners save a page."""
    doc = pymupdf.open()
    with pymupdf.open(source) as printed:
        upright = printed[0].rect
        page = doc.new_page(width=upright.height, height=upright.width)
        page.show_pdf_page(page.rect, printed, 0, rotate=90)
    page.set_rotation(90)
    path = folder / f"{source.stem}-stored-sideways.pdf"
    doc.save(path)
    return path


needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="Tesseract is not installed"
)
