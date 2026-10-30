"""Helpers and constants shared by the test modules."""

import asyncio
import zipfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import pymupdf
from mcp import Client
from mcp.types import CallToolResult

from pgnify.chess_tools import MoveSpec
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


def in_session[T](
    books: Path, out: Path, act: Callable[[Client], Awaitable[T]], spanned: bool = False
) -> T:
    """Run act with the SDK's in-process client, connected to a server for the books at books and
    the output folder out, and return its result."""

    async def run() -> T:
        async with Client(build_server(books, out, spanned=spanned)) as client:
            return await act(client)

    return asyncio.run(run())


def call_tools(
    books: Path, out: Path, calls: list[tuple[str, dict[str, Any]]], spanned: bool = False
) -> list[CallToolResult]:
    """Call the tools in turn, in one session."""

    async def call(client: Client) -> list[CallToolResult]:
        return [await client.call_tool(name, arguments) for name, arguments in calls]

    return in_session(books, out, call, spanned)
