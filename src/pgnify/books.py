"""The books a server may read: one book, or every PDF and EPUB in a folder and its subfolders.

The model names a book by its path within the folder, and the server looks that name up among
the books it found, so it never opens a path the model made up. Hidden files and folders are
skipped, such as the .pgnify folder of an output folder kept among the books, and a symbolic
link counts as a book only when it leads to a PDF or EPUB in the folder that is not hidden."""

import functools
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath

SUFFIXES = (".pdf", ".epub")
# The most books an error message or the server's instructions list by name.
MAX_LISTED = 50
# Curly quotes and dashes, matched as the straight quotes and hyphens a name may be typed with.
_TYPED = str.maketrans("\u2018\u2019\u201c\u201d\u2013\u2014", "''\"\"--")


@dataclass
class Book:
    """A book the server may read: its name, as the model gives it, and its file."""

    name: str
    path: Path

    @functools.cached_property
    def key(self) -> str:
        """The name of the book's folder in the output folder: its stem in lower-case letters,
        digits and dashes, then the first 12 hex digits of the SHA-256 of its name and contents.
        Two copies of a book, or a link and its target, get folders of their own, and a book
        whose contents change gets a new one."""
        with self.path.open("rb") as file:
            digest = hashlib.file_digest(file, lambda: hashlib.sha256(self.name.encode() + b"\0"))
        plain = re.sub(r"[^a-z0-9]+", "-", _folded(Path(self.name).stem)).strip("-")[:40]
        return f"{plain or 'book'}-{digest.hexdigest()[:12]}"


class BooksLocation:
    """The books at the location `pgnify serve --books` names: a book, or a folder of books."""

    def __init__(self, location: Path):
        if not location.exists():
            raise FileNotFoundError(f"there is nothing at {location}")
        resolved = location.resolve()
        if resolved.is_file() and resolved.suffix.lower() not in SUFFIXES:
            raise ValueError(f"{location} is not a PDF or an EPUB")
        self.single = resolved if resolved.is_file() else None
        self.root = resolved.parent if self.single else resolved
        self.books = self._found()
        if not self.books:
            raise ValueError(f"there is no PDF or EPUB at {location}")

    def get(self, name: str | None) -> Book:
        """The book named, or the only book when none is named. A name matches a book's name
        exactly, or else the one book whose name differs from it only in case, curly quotes or
        dashes. A book added to the folder since the server started is found too."""
        if name is None:
            if len(self.books) == 1:
                return next(iter(self.books.values()))
            raise ValueError(f"name the book: {self.listing()}")
        if name.startswith(("/", "\\", "~")) or PureWindowsPath(name).drive:
            raise ValueError(f"name the book as it is listed, not by a path: {self.listing()}")
        book = self._match(name)
        if book is None and not self.single:
            self.books = self._found()
            book = self._match(name)
        if book is None:
            raise ValueError(f"there is no book {name!r}: {self.listing()}")
        return book

    def listing(self) -> str:
        names = sorted(self.books)
        shown = ", ".join(repr(name) for name in names[:MAX_LISTED])
        more = f", and {len(names) - MAX_LISTED} more" if len(names) > MAX_LISTED else ""
        return f"the books are {shown}{more}"

    def _match(self, name: str) -> Book | None:
        if name in self.books:
            return self.books[name]
        alike = [book for listed, book in self.books.items() if _folded(listed) == _folded(name)]
        return alike[0] if len(alike) == 1 else None

    def _found(self) -> dict[str, Book]:
        if self.single:
            return {self.single.name: Book(self.single.name, self.single)}
        found = {}
        for path in sorted(self.root.rglob("*")):
            relative = path.relative_to(self.root)
            if not _is_book(relative):
                continue
            try:
                target = path.resolve(strict=True)
            except OSError:  # a link whose target is gone, or a loop
                continue
            if (
                target.is_file()
                and target.is_relative_to(self.root)
                and _is_book(target.relative_to(self.root))
            ):
                found[relative.as_posix()] = Book(relative.as_posix(), target)
        return found


def _is_book(relative: PurePath) -> bool:
    """Whether a path within the books location names a PDF or EPUB that is not hidden."""
    hidden = any(part.startswith(".") for part in relative.parts)
    return relative.suffix.lower() in SUFFIXES and not hidden


def _folded(text: str) -> str:
    """The text in one case, with straight quotes and hyphens for curly quotes and dashes."""
    return unicodedata.normalize("NFC", text).casefold().translate(_TYPED)
