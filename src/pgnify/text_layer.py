"""Where a book's text layer comes from, as the book's file shows it: whether the layer appears to
be a machine reading of scanned pages, judged by properties of the file alone, the capture or OCR
tool its metadata names and pictures that cover whole pages, and never by its words. That is
evidence of where the text comes from, not that any of it is wrong: `pgnify report` labels the
comparisons that rest on such a layer as of lower confidence, and keeps every one."""

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Capture and OCR tools, as a PDF's metadata names them in its producer or creator.
CAPTURE_TOOL = re.compile(
    r"paper capture|clearscan|abbyy|finereader|tesseract|ocrmypdf|omnipage|readiris|\bocr\b",
    re.IGNORECASE,
)
# A picture that covers at least this share of its page is taken for a picture of the page.
SCANNED_PAGE = 0.9


@dataclass(frozen=True)
class TextLayer:
    """What the file shows: the producer or creator that names a capture or OCR tool, as the
    file gives it, or None; the pages that have text; those of them whose text lies over a
    picture of the whole page; and how many pages the book has."""

    tool: str | None
    with_text: tuple[int, ...]
    over_scans: tuple[int, ...]
    pages: int = 0

    @property
    def machine_read(self) -> bool:
        """Whether the layer appears to be a machine reading: the book has text, and its file
        names a capture or OCR tool, or most of its pages with text lie over a picture of the
        whole page, as a scan's text layer does."""
        return bool(self.with_text) and (
            self.tool is not None or 2 * len(self.over_scans) > len(self.with_text)
        )

    def machine_read_page(self, number: int) -> bool:
        """Whether the page's text appears to be a machine reading: the layer does, and the page
        has text, named by the tool or over a picture of the page."""
        return (
            self.machine_read
            and number in self.with_text
            and (self.tool is not None or number in self.over_scans)
        )

    def evidence(self) -> str:
        """What the file shows, in words, for a report: the tool it names, and how many of its
        pages with text lie over a picture of the whole page, where most do."""
        found = []
        if self.tool is not None:
            found.append(f"its file names a capture or OCR tool, «{self.tool}»")
        if 2 * len(self.over_scans) > len(self.with_text):
            verb = "lies" if len(self.over_scans) == 1 else "lie"
            found.append(
                f"{len(self.over_scans)} of its {len(self.with_text)} pages with text {verb} over "
                "a picture of the whole page"
            )
        return " and ".join(found)


def text_layer(path: Path | str) -> TextLayer:
    """What the book's file shows of where its text layer comes from."""
    with pymupdf.open(path) as doc:
        named = (doc.metadata.get(key) or "" for key in ("producer", "creator"))
        tool = next((value for value in named if CAPTURE_TOOL.search(value)), None)
        with_text, over_scans = [], []
        for page in doc:
            if not page.get_text("text").strip():
                continue
            with_text.append(page.number + 1)
            # Pictures are placed on the page unturned, whatever its rotation.
            bounds = page.rect * page.derotation_matrix
            covered = (abs(pymupdf.Rect(info["bbox"]) & bounds) for info in page.get_image_info())
            if any(area >= SCANNED_PAGE * abs(bounds) for area in covered):
                over_scans.append(page.number + 1)
        pages = doc.page_count
    return TextLayer(tool, tuple(with_text), tuple(over_scans), pages)
