"""The output folder: the one place the server writes.

The PGN files sit at the top of the folder, under the plain file names the model gives, each
with the model's notes beside it when it gave any. pgnify keeps its records in .pgnify/:

- books/<key>/, a folder for each book, holding the games saved from it, the pictures extracted
  from it and the pieces its figurines were read as, so that two books never share them;
- outputs.json, the book each PGN file was written from, and from which saved games;
- log.jsonl, every tool call, for `pgnify report`.

A PGN file is replaced only when this server wrote it in this session from the same book, or
when the model asks to replace it. Names are compared ignoring case, as macOS and Windows compare
them, so that two names cannot reach one file. Nothing is written through a symbolic link."""

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pgnify.books import Book

# A PGN file name the model may give: letters, digits, spaces and . _ - ( ) ', starting with a
# letter or digit, at most 96 characters with the ".pgn" that target() adds unless it is there.
_NAME = re.compile(r"[^\W_][\w .()'-]{0,95}")
# A book's name cut to this length, as its PGN file's default name, leaves room for ".pgn".
_DEFAULT_LENGTH = 90
NOTES_HEADING = "# The model's own account"


@dataclass(frozen=True)
class BookFolder:
    """A book's folder in .pgnify/books/."""

    folder: Path

    @property
    def games(self) -> Path:
        return self.folder / "games"

    @property
    def images(self) -> Path:
        return self.folder / "images"

    @property
    def figurines(self) -> Path:
        """The file of the pieces the book's figurines were read as, game by game."""
        return self.folder / "pictures.json"


class Outputs:
    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder.resolve()
        self.work = _unlinked(self.folder / ".pgnify")
        self.work.mkdir(exist_ok=True)
        for kept in ("books", "log.jsonl", "outputs.json"):
            _unlinked(self.work / kept)
        # The key of the book each PGN file written in this session came from, by its name
        # casefolded.
        self.written: dict[str, str] = {}

    def book_folder(self, book: Book) -> BookFolder:
        """The book's folder, or ValueError when anything in it is a symbolic link."""
        kept = BookFolder(self.work / "books" / book.key)
        for path in (self.work / "books", kept.folder, kept.games, kept.images, kept.figurines):
            _unlinked(path)
        for folder in (kept.games, kept.images):
            if folder.is_dir():
                for path in folder.iterdir():
                    _unlinked(path)
        return kept

    def picture(self, path: str) -> Path:
        """The extracted picture at path, which must be exactly the path doc_images returned;
        ValueError for any other path, a link out of the folder included."""
        resolved = (self.folder / path).resolve()
        try:
            given = resolved.relative_to(self.folder).as_posix()
            within = resolved.relative_to(self.work / "books").parts
        except ValueError:
            given, within = None, ()
        if path != given or len(within) < 3 or within[1] != "images" or not resolved.is_file():
            raise ValueError(
                "image_path must be a picture doc_images extracted, by the path it gave"
            )
        return resolved

    def target(self, file: str | None, book: Book, replace: bool) -> Path:
        """The PGN file to write for book: file, or the book's name when file is None.
        ValueError, with nothing written, when the name is not a plain file name or when the
        file exists and this session may not replace it."""
        name = file if file is not None else _default_name(Path(book.name).stem)
        name = (name[:-4] if name.lower().endswith(".pgn") else name) + ".pgn"
        if not _NAME.fullmatch(name):
            raise ValueError(
                f"{name!r} is not a plain file name: letters, digits, spaces and . _ - ( ) ', "
                "starting with a letter or digit, at most 96 characters, without a folder"
            )
        # A file already there under the name in another case keeps its name.
        name = next(
            (
                other.name
                for other in self.folder.iterdir()
                if other.name.casefold() == name.casefold()
            ),
            name,
        )
        path = self.folder / name
        notes = notes_path(path)
        for kept in (path, notes):
            if kept.is_symlink() or (kept.exists() and not kept.is_file()):
                raise ValueError(f"{kept.name} is not a plain file, so nothing was written")
        if notes.exists() and not _notes_text(notes).startswith(NOTES_HEADING):
            raise ValueError(
                f"{notes.name} is not pgnify's notes, so nothing was written: give another file "
                "name"
            )
        writer = self.written.get(name.casefold())
        if path.exists() and not replace and writer != book.key:
            whose = (
                "was written in this session from another book"
                if writer
                else "was there before this session"
            )
            raise ValueError(
                f"{name} {whose}, so nothing was written: give another file name, or set "
                "replace to true"
            )
        return path

    def wrote(self, path: Path, book: Book, games: list[str] | int, notes: str) -> None:
        """Record that path was written from book's games, their saved names or how many were
        given as text. Keep the notes beside it, or, given none, delete the notes kept with what
        the file held before."""
        self.written[path.name.casefold()] = book.key
        notes_file = notes_path(path)
        if notes:
            notes_file.write_text(
                f"{NOTES_HEADING}\n\n"
                f"What the AI assistant said of this conversion when it wrote {path.name} from "
                f"{book.name}. pgnify has not checked it: `pgnify report` says what it could "
                "check, and neither these notes nor their absence shows that nothing is "
                f"missing.\n\n{notes}\n",
                encoding="utf-8",
            )
        elif notes_file.exists() and _notes_text(notes_file).startswith(NOTES_HEADING):
            notes_file.unlink()
        records = read_records(self.work)
        records[path.name] = {
            "book": book.name,
            "book_path": str(book.path),
            "book_key": book.key,
            "games": games,
            "notes": bool(notes),
            "written": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        replace_json(self.work / "outputs.json", records)

    def log(self, entry: dict[str, Any]) -> None:
        with _unlinked(self.work / "log.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def notes_path(pgn: Path) -> Path:
    return pgn.with_name(pgn.stem + ".notes.md")


def read_notes(pgn: Path) -> str | None:
    """The notes the model gave with the PGN file, without the heading and preamble wrote()
    puts before them; None when there are none, or the notes file is not pgnify's."""
    notes = notes_path(pgn)
    text = _notes_text(notes) if notes.is_file() else ""
    if not text.startswith(NOTES_HEADING):
        return None
    return text.split("\n\n", 2)[-1].strip()


def read_records(work: Path) -> dict[str, Any]:
    """outputs.json in the folder .pgnify/ at work: the record of each PGN file written."""
    file = work / "outputs.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}


def replace_json(path: Path, value: Any) -> None:
    """Write a JSON file whole, through a temporary file, so that it is never found half
    written."""
    written = path.with_name(path.name + ".new")
    # write_text would follow a link planted at the temporary name.
    written.unlink(missing_ok=True)
    text = json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    written.write_text(text, encoding="utf-8")
    os.replace(written, path)


def _notes_text(notes: Path) -> str:
    return notes.read_text(encoding="utf-8", errors="replace")


def _default_name(stem: str) -> str:
    """A book's stem as a PGN file name, other characters replaced by dashes."""
    plain = re.sub(r"[^\w .()'-]+", "-", stem).strip(" ._-'()")
    return plain[:_DEFAULT_LENGTH] or "book"


def _unlinked(path: Path) -> Path:
    """path, or ValueError when it is a symbolic link, which pgnify never writes through."""
    if path.is_symlink():
        raise ValueError(f"{path.name} in pgnify's output folder is a link, so nothing was written")
    return path
