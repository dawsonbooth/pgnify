"""Tesseract's reading of a page as printed: for doc_text's ocr option, and for the text beside
the boards read_diagram finds on a page without a text layer. pgnify runs the tesseract program,
which the user installs apart, in a separate process, on the page drawn in grey at 300 dpi, and
reads its words and their boxes. It installs, trains and tunes nothing, and without the program
says how to install it.

A reading is a machine reading, never the book's text: Tesseract reads no figurines, evaluation
marks or other chess signs, which come out as letters or other characters, or not at all."""

import functools
import shutil
import subprocess
from dataclasses import dataclass

import pymupdf

DPI = 300
# Seconds a page's reading may take: about 1.5 on a scanned page of a book.
TIMEOUT = 120
# Tesseract's page segmentation: a page, its columns found and read in order; a block of text
# of one size, which reads the labels beside a board that a page's reading drops.
PAGE, BLOCK = 3, 6
LANGUAGE = "eng"


@dataclass(frozen=True)
class Word:
    """A word Tesseract read: its text, and its box in points on the page as it lies unturned."""

    text: str
    box: pymupdf.Rect


@dataclass(frozen=True)
class Line:
    """A line of words, in Tesseract's order."""

    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def box(self) -> pymupdf.Rect:
        box = pymupdf.Rect(self.words[0].box)
        for word in self.words[1:]:
            box |= word.box
        return box


@functools.cache
def version() -> str:
    """Tesseract's name and version, as it gives them, such as "tesseract 5.5.0"; RuntimeError,
    saying how to install it, where the tesseract command is missing, or why it does not run."""
    if shutil.which("tesseract") is None:
        raise RuntimeError(
            "OCR needs Tesseract, which pgnify runs but does not install: install it, for "
            "example with `brew install tesseract` on macOS, `apt install tesseract-ocr` on "
            "Debian or Ubuntu, or the installer at github.com/UB-Mannheim/tesseract on Windows, "
            "so that the tesseract command runs"
        )
    shown = _run(["--version"])
    # Older versions write their version to stderr.
    said = (shown.stdout or shown.stderr).decode(errors="replace").strip().splitlines()
    if not said:
        raise RuntimeError("Tesseract failed: tesseract --version printed nothing")
    return said[0].strip()


def read(
    page: pymupdf.Page,
    clip: pymupdf.Rect | None = None,
    blank: list[pymupdf.Rect] | tuple[pymupdf.Rect, ...] = (),
    segmentation: int = PAGE,
) -> list[Line]:
    """Tesseract's reading of the page, or of the part clip covers, in points on the page as it
    lies unturned, with each of blank, in the same points, left white."""
    version()
    zoom = pymupdf.Matrix(DPI / 72, DPI / 72)
    part = clip if clip is not None else page.rect * page.derotation_matrix
    # A rotated page is drawn turned, as it is read.
    turned = part * page.rotation_matrix
    picture = page.get_pixmap(matrix=zoom, clip=turned, colorspace=pymupdf.csGRAY, alpha=False)
    for box in blank:
        # A pixmap drawn from part of a page keeps the whole page's pixel coordinates.
        picture.set_rect((box * page.rotation_matrix * zoom).irect, (255,))
    arguments = ["stdin", "stdout", "-l", LANGUAGE, "--psm", str(segmentation), "--dpi", str(DPI)]
    process = _run([*arguments, "tsv"], picture.tobytes("png"))
    # From the picture's pixels to the page's points, unturned.
    back = pymupdf.Matrix(1, 0, 0, 1, picture.x, picture.y) * ~zoom * page.derotation_matrix
    return lines(process.stdout.decode(errors="replace"), back)


def _run(arguments: list[str], picture: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    """The tesseract command run with the arguments, the picture given on stdin; RuntimeError,
    saying why, where it fails or gives no answer within TIMEOUT seconds."""
    try:
        process = subprocess.run(
            ["tesseract", *arguments], input=picture, capture_output=True, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Tesseract gave no answer within {TIMEOUT} seconds") from None
    if process.returncode != 0:
        last = process.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["no message"]
        raise RuntimeError(f"Tesseract failed: {last[0]}")
    return process


def lines(tsv: str, back: pymupdf.Matrix = pymupdf.Identity) -> list[Line]:
    """The lines of words in Tesseract's TSV, in its order, each word's box taken from the
    picture's pixels by back."""
    found: dict[tuple[str, str, str], list[Word]] = {}
    for row in tsv.splitlines()[1:]:
        fields = row.split("\t")
        if len(fields) != 12 or fields[0] != "5" or not fields[11].strip():
            continue
        left, top, width, height = (int(value) for value in fields[6:10])
        box = pymupdf.Rect(left, top, left + width, top + height) * back
        word = Word(fields[11].strip(), box.normalize())
        found.setdefault((fields[2], fields[3], fields[4]), []).append(word)
    return [Line(tuple(words)) for words in found.values()]
