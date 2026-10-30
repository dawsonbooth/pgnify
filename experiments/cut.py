"""Cut a clipped source from a book, as experiments/slices.toml names it: some of the book's files
or pages, unchanged, so that a run on a hosted model can be sent nothing else of the book.

A PDF keeps the pages named, and the outline entries on those pages or with such entries under
them. An EPUB keeps the spine documents that hold the pages named, as PyMuPDF lays the book out,
with the pictures, stylesheets and fonts they use; its package is rewritten to list only what is
kept, and its NCX table of contents to point only at kept documents: an entry whose document is
cut but which has kept entries under it points at the first of them, so that the sections the
tools report keep their titles. The book's documents and pictures are copied byte for byte.
Every page of the cut is then checked to give the same text, with its picture markers, as the
book's page it comes from, and a cut whose book has a table of contents over those pages must
have one too.

Writes data/local/cuts/<cut>.epub or .pdf and prints its path, its SHA-256, and its page count
as PyMuPDF lays it out.

    uv run python experiments/cut.py CUT...
"""

import argparse
import hashlib
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree

import pymupdf
import session

from pgnify.doc_tools import page_text

NCX = "http://www.daisy.org/z3986/2005/ncx/"
# Files whose references to other files are followed: documents, stylesheets, vector pictures.
REFERRING = (".html", ".xhtml", ".htm", ".css", ".svg", ".xml")
DOCUMENTS = (".html", ".xhtml", ".htm")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("cuts", nargs="+", metavar="cut")
    names = parser.parse_args().cuts
    cuts = session.slices()["cuts"]
    for name in names:
        spec = cuts[name]
        book = session.book(spec["source"])
        ranges = [tuple(pages) for pages in spec["pages"]]
        out = cut(book, ranges, session.CUTS / f"{name}{book.suffix}")
        with pymupdf.open(out) as doc:
            count = doc.page_count
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        print(f"{out}\n  sha256 {digest}\n  {count} pages")


def cut(book: Path, ranges: list[tuple[int, int]], out: Path) -> Path:
    """The book cut to the pages in ranges, each (first, last) counting from 1, written to out
    and checked."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if book.suffix.lower() == ".pdf":
        pages = [page for first, last in ranges for page in range(first, last + 1)]
        with pymupdf.open(book) as doc:
            outline = _kept_toc(doc.get_toc(), {page: new for new, page in enumerate(pages, 1)})
            doc.select([page - 1 for page in pages])
            doc.set_toc(outline)
            doc.save(out, garbage=4, deflate=True, no_new_id=True)
    else:
        documents, pages = _documents(book, ranges)
        _cut_epub(book, documents, out)
    _check(book, pages, out)
    return out


def _documents(book: Path, ranges: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
    """The spine documents, by their place in the spine, that hold any page in ranges, and the
    book's pages they hold."""
    kept, pages, start = [], [], 1
    with pymupdf.open(book) as doc:
        for chapter in range(doc.chapter_count):
            end = start + doc.chapter_page_count(chapter) - 1
            if any(first <= end and start <= last for first, last in ranges):
                kept.append(chapter)
                pages.extend(range(start, end + 1))
            start = end + 1
    return kept, pages


def _cut_epub(book: Path, documents: list[int], out: Path) -> None:
    with zipfile.ZipFile(book) as epub:
        names = set(epub.namelist())
        container = epub.read("META-INF/container.xml").decode()
        opf_name = _found(r"""full-path\s*=\s*["']([^"']+)""", container)
        opf = epub.read(opf_name).decode()
        # The package as MuPDF reads it: without comments, and a spine of manifest items only.
        package = re.sub(r"<!--.*?-->", "", opf, flags=re.S)
        base = posixpath.dirname(opf_name)
        items = re.findall(r"<item\b[^>]*?(?:/>|>.*?</item\s*>)", package, flags=re.S)
        manifest = {_attribute(tag, "id"): (tag, _attribute(tag, "href")) for tag in items}
        spine = [
            idref
            for idref in re.findall(r"""<itemref\b[^>]*?idref\s*=\s*["']([^"']+)""", package)
            if idref in manifest
        ]
        kept_files = {_path(base, manifest[spine[index]][1]) for index in documents}
        # What the kept documents use, and what that uses in turn: pictures, stylesheets, fonts.
        waiting = sorted(kept_files)
        while waiting:
            for used in _used(epub, waiting.pop(), names) - kept_files:
                kept_files.add(used)
                waiting.append(used)
        toc_id = re.search(r"""<spine\b[^>]*?toc\s*=\s*["']([^"']+)""", package)
        toc_name = _path(base, manifest[toc_id[1]][1]) if toc_id else None
        dropped = {
            id_
            for id_, (_, href) in manifest.items()
            if _path(base, href) not in kept_files and _path(base, href) != toc_name
        }
        for id_ in dropped:
            opf = opf.replace(manifest[id_][0], "")
        opf = re.sub(
            r"""<itemref\b[^>]*?idref\s*=\s*["']([^"']+)["'][^>]*>""",
            lambda match: "" if match[1] in dropped else match[0],
            opf,
        )
        opf = re.sub(
            r"""<reference\b[^>]*?href\s*=\s*["']([^"'#]+)[^>]*>""",
            lambda match: match[0] if _path(base, match[1]) in kept_files else "",
            opf,
        )
        with zipfile.ZipFile(out, "w") as out_zip:
            # The mimetype entry first and uncompressed, as the container requires.
            out_zip.writestr(zipfile.ZipInfo("mimetype"), epub.read("mimetype"))
            for info in epub.infolist():
                name = info.filename
                if name == "mimetype":
                    continue
                if name == opf_name:
                    data = opf.encode()
                elif name == toc_name:
                    data = _cut_toc(epub.read(name), name, kept_files)
                elif name == "META-INF/container.xml" or name in kept_files:
                    data = epub.read(name)
                else:
                    continue
                entry = zipfile.ZipInfo(name, date_time=info.date_time)
                out_zip.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED)


def _used(epub: zipfile.ZipFile, name: str, names: set[str]) -> set[str]:
    """The files of the EPUB that a document or stylesheet uses: pictures, stylesheets and
    fonts, not the other documents its links lead to."""
    if not name.lower().endswith(REFERRING):
        return set()
    text = epub.read(name).decode(errors="replace")
    references = re.findall(r"""(?:src|href|xlink:href)\s*=\s*["']([^"'#]+)""", text)
    references += re.findall(r"""url\(\s*['"]?([^'")]+)""", text)
    references += re.findall(r"""@import\s+["']([^"']+)""", text)
    folder = posixpath.dirname(name)
    return {
        path
        for reference in references
        if (path := _path(folder, reference)) in names and not path.lower().endswith(DOCUMENTS)
    }


def _cut_toc(ncx: bytes, toc_name: str, kept_files: set[str]) -> bytes:
    """The NCX table of contents with only entries that are, or hold, kept documents."""
    ElementTree.register_namespace("", NCX)
    root = ElementTree.fromstring(ncx)
    folder = posixpath.dirname(toc_name)

    def kept(point: ElementTree.Element) -> str | None:
        """Prune point's children; return the source point should keep, or None to drop it."""
        sources = []
        for child in point.findall(f"{{{NCX}}}navPoint"):
            source = kept(child)
            if source is None:
                point.remove(child)
            else:
                sources.append(source)
        content = point.find(f"{{{NCX}}}content")
        own = content.get("src", "") if content is not None else ""
        if _path(folder, own.split("#")[0]) in kept_files:
            return own
        if sources and content is not None:
            content.set("src", sources[0])
            return sources[0]
        return None

    nav_map = root.find(f"{{{NCX}}}navMap")
    if nav_map is None:
        raise ValueError(f"{toc_name} has no navMap")
    for point in nav_map.findall(f"{{{NCX}}}navPoint"):
        if kept(point) is None:
            nav_map.remove(point)
    for order, point in enumerate(nav_map.iter(f"{{{NCX}}}navPoint"), start=1):
        point.set("playOrder", str(order))
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _kept_toc(toc: list[list[Any]], new_pages: dict[int, int]) -> list[list[Any]]:
    """The entries of a PDF's outline that are on kept pages, at their new pages, and those that
    hold such entries, at the first one's page."""
    kept = []
    for index, (level, title, page, *_) in enumerate(toc):
        target = new_pages.get(page)
        if target is None:
            for inner_level, _, inner_page, *_ in toc[index + 1 :]:
                if inner_level <= level:
                    break
                if inner_page in new_pages:
                    target = new_pages[inner_page]
                    break
        if target is not None:
            kept.append([level, title, target])
    return kept


def _check(book: Path, pages: list[int], out: Path) -> None:
    """Every page of the cut gives the text of the book's page it comes from, and the cut has a
    table of contents if the book has one over those pages."""
    with pymupdf.open(book) as original, pymupdf.open(out) as cut:
        if cut.page_count != len(pages):
            raise ValueError(f"{out} has {cut.page_count} pages, not the book's {len(pages)}")
        for new, page in enumerate(pages, 1):
            if page_text(original[page - 1]) != page_text(cut[new - 1]):
                raise ValueError(f"{out} page {new} is not the book's page {page}")
        kept = set(pages)
        covered = any(entry[2] in kept for entry in original.get_toc())
        if covered and not cut.get_toc():
            raise ValueError(f"{out} lost the table of contents over its pages")


def _attribute(tag: str, name: str) -> str:
    return _found(rf"""\b{name}\s*=\s*["']([^"']*)""", tag)


def _found(pattern: str, text: str) -> str:
    """The pattern's first group in text, which the EPUB must have."""
    match = re.search(pattern, text)
    if match is None:
        raise ValueError(f"the EPUB lacks {pattern!r}")
    return match[1]


def _path(folder: str, reference: str) -> str:
    """A reference, relative to folder, as a name in the EPUB's zip."""
    return posixpath.normpath(posixpath.join(folder, unquote(reference)))


if __name__ == "__main__":
    main()
