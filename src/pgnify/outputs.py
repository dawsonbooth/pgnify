"""Where the server writes: the output folder, or the folders the person allows.

With --out, the output folder is the one place the server writes. The PGN files sit under the file
names the model gives, each with the model's notes beside it when it gave any. A name may include
folders, which pgnify creates, so that a person registers the server once and the model chooses
each book's place in the output folder. pgnify keeps its records in .pgnify/ there. Without
--out, the model gives each file's full path, and the person is asked the first time for each
folder (permission.py); a yes covers the folder and the folders inside it, by their real paths.
pgnify keeps its records in its own folder, ~/.local/state/pgnify/, with the same layout:

- books/<key>/, a folder for each book, holding the games saved from it, the pictures extracted
  from it and the pages shown from it, and the pieces its figurines were read as, so that two
  books never share them;
- outputs.json, the book each PGN file was written from, and from which saved games, by the
  file's path in the output folder, or its full real path in pgnify's own folder;
- log.jsonl, every tool call, for `pgnify report`.

Two sessions may share a folder, so a record and a log line are written under a lock on the
folder that every process takes, on every platform.

A PGN file is replaced only when this server wrote it in this session from the same book, or
when the model asks to replace it. Names are compared ignoring case, as macOS and Windows compare
them, so that two names cannot reach one file. With --out nothing is written through a symbolic
link. Without it, a folder reached through a link must be allowed by its real path. The PGN and
notes files are never links."""

import errno
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pgnify.books import Book
from pgnify.permission import Allowances, folded, full_path, shown, shown_with_real

# A name the model may give a folder or a PGN file: letters, digits, spaces and . _ - ( ) ',
# starting with a letter or digit, at most 96 characters, with the ".pgn" that target() adds to
# a file's unless it is there.
_NAME = re.compile(r"[^\W_][\w .()'-]{0,95}")
# The longest path of a PGN file in the output folder, in characters.
_PATH_LENGTH = 255
# A book's name cut to this length, as its PGN file's default name, leaves room for ".pgn".
_DEFAULT_LENGTH = 90
NOTES_HEADING = "# The model's own account"
# How many times replace_json tries to replace a file another process has open.
_REPLACES = 5
# The working folder's entries, each refused if it is a symbolic link.
_KEPT = ("books", "log.jsonl", "outputs.json", "lock")


def own_folder() -> Path:
    """pgnify's own folder, where it keeps its records when --out is not given."""
    return Path.home() / ".local" / "state" / "pgnify"


@dataclass(frozen=True)
class BookFolder:
    """A book's folder in .pgnify/books/. Without --out, the games saved in a session are kept
    in a folder of the session's own, since sessions share the book's folder."""

    folder: Path
    session: str | None = None

    @property
    def games(self) -> Path:
        return self.folder / "games" / self.session if self.session else self.folder / "games"

    @property
    def images(self) -> Path:
        return self.folder / "images"

    @property
    def figurines(self) -> Path:
        """The file of the pieces the book's figurines were read as, game by game."""
        return self.folder / "pictures.json"


class Outputs:
    def __init__(self, folder: Path | None, state: Path | None = None):
        """folder is the --out folder, or None, when state is pgnify's own folder. self.folder is
        where paths in results start from."""
        self.fenced = folder is not None
        self.allowances = Allowances("write PGN files in", "--out", nested=True)
        # The files the person allowed replacing, though this session did not write them.
        self.replaces = Allowances("replace", "--out")
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            self.folder = folder.resolve()
            self.work = _unlinked(self.folder / ".pgnify")
            self.work.mkdir(exist_ok=True)
        else:
            assert state is not None, "state is required without an output folder"
            state.mkdir(parents=True, exist_ok=True)
            self.folder = self.work = state.resolve()
        for kept in _KEPT:
            _unlinked(self.work / kept)
        # The key and the real path of the book each PGN file written in this session came from.
        # With --out, by the file's key casefolded, so that two names cannot reach one file.
        # Without it, by the file's identity, which it has whatever the file system does with
        # case: folder names are taken as they are, and names that differ only in case may be
        # two files.
        self.written: dict[str, tuple[str, Path]] = {}
        self.written_ids: dict[tuple[int, int], tuple[str, Path]] = {}
        # Without --out, every session without it shares the book's folders: the games a session
        # saves are its own.
        self.session = None if self.fenced else uuid.uuid4().hex[:12]

    def book_folder(self, book: Book) -> BookFolder:
        """The book's folder, or ValueError when anything in it is a symbolic link."""
        kept = BookFolder(self.work / "books" / book.key, self.session)
        for path in (
            self.work / "books",
            kept.folder,
            kept.folder / "games",
            kept.games,
            kept.images,
            kept.figurines,
        ):
            _unlinked(path)
        for folder in (kept.games, kept.images):
            if folder.is_dir():
                for path in folder.iterdir():
                    _unlinked(path)
        return kept

    def picture(self, path: str) -> Path:
        """The picture at path, which must be exactly the path doc_images or doc_page returned;
        ValueError for any other path, a link out of the folder included."""
        resolved = (self.folder / path).resolve()
        try:
            given = resolved.relative_to(self.folder).as_posix()
            within = resolved.relative_to(self.work / "books").parts
        except ValueError:
            given, within = None, ()
        if path != given or len(within) < 3 or within[1] != "images" or not resolved.is_file():
            raise ValueError(
                "image_path must be a picture doc_images extracted or doc_page showed, by the "
                "path it gave"
            )
        return resolved

    def question(self, file: str | None) -> str | None:
        """What to ask the person before writing the PGN file at file, or None when there is
        nothing to ask: an output folder, a path that target() refuses, or a folder the person
        has already answered for."""
        if self.fenced:
            return None
        try:
            given, folder, _ = self._place(file)
        except ValueError:
            return None
        if not self.allowances.asks_about(folder):
            return None
        return (
            "Allow pgnify to write PGN files in this folder, and in folders inside it, until "
            f"this session ends?\n\n{shown_with_real(given, folder)}\n\n"
            "pgnify will create the folder if it is missing."
        )

    def settle(self, file: str | None, answer: Any) -> str | None:
        """Record the person's answer about the folder of file, and return why nothing may be
        written there, or None. Without an output folder only; target() says why a path is
        refused otherwise."""
        if self.fenced:
            return None
        try:
            _, folder, _ = self._place(file)
        except ValueError:
            return None
        return self.allowances.settle(folder, answer)

    def replace_question(
        self, file: str | None, book: Path | None, replace: bool, folder_allowed: bool
    ) -> str | None:
        """What to ask before replacing the file at file with games from book (its real path): only
        without --out, when replace is true, the file exists, this session did not write it from
        book, and its folder is allowed, before this call or by folder_allowed. None otherwise."""
        path = self._replaced(file, book, replace)
        if path is None or not self.replaces.asks_about(path):
            return None
        if not (folder_allowed or self.allowances.covers(path.parent)):
            return None
        return f"Allow pgnify to replace this file?\n\n{shown(path)}"

    def settle_replace(
        self, file: str | None, book: Path | None, replace: bool, answer: Any
    ) -> str | None:
        """Record the person's answer about replacing the file, and return why it may not be
        replaced, or None."""
        path = self._replaced(file, book, replace)
        return None if path is None else self.replaces.settle(path, answer)

    def _replaced(self, file: str | None, book: Path | None, replace: bool) -> Path | None:
        """The file that would be replaced, without an output folder, though this session did not
        write it from book; None for any other case, which target() answers."""
        if self.fenced or not replace or book is None:
            return None
        try:
            _, folder, name = self._place(file)
            path = folder / (_as_kept(folder, name) if folder.is_dir() else name)
            there = path.is_file() and not path.is_symlink()
        except (OSError, ValueError):
            return None
        writer = self._writer(path)
        if not there or (writer and writer[1] == book):
            return None
        return path

    def _writer(self, path: Path) -> tuple[str, Path] | None:
        """The key and the real path of the book that this session wrote the file at path from;
        None if it did not write it."""
        if self.fenced:
            return self.written.get(self._key(path).casefold())
        identity = _identity(path)
        return self.written_ids.get(identity) if identity else None

    def target(self, file: str | None, book: Book, replace: bool) -> Path:
        """The PGN file to write for book. With an output folder, file is a file name or a path
        of folders and a file name in it, or None for the book's name; without one, it is the
        full path of the file, and its folder must be one the person allowed. ValueError, with
        nothing written and no folder made, when the name is not allowed, a folder on its path
        is a link or a file, or the file exists and this session may not replace it."""
        if not self.fenced:
            _, folder, name = self._place(file)
            if not self.allowances.covers(folder):
                raise ValueError(
                    f"the person has not allowed pgnify to write PGN files in {shown(folder)}"
                )
            path = folder / (_as_kept(folder, name) if folder.is_dir() else name)
        else:
            path = self._in_folder(file, book)
        name = self._key(path)
        notes = notes_path(path)
        for kept in (path, notes):
            if kept.is_symlink() or (kept.exists() and not kept.is_file()):
                raise ValueError(f"{self.named(kept)} is not a plain file, so nothing was written")
        if notes.exists() and not _notes_text(notes).startswith(NOTES_HEADING):
            raise ValueError(
                f"{self.named(notes)} is not pgnify's notes, so nothing was written: give "
                "another file name"
            )
        mine = self._writer(path)
        writer = mine[0] if mine else None
        if (
            not self.fenced
            and replace
            and path.exists()
            and not (mine and mine[1] == book.path)
            and not self.replaces.covers(path)
        ):
            raise ValueError(
                f"the person has not allowed pgnify to replace {self.named(path)}, so nothing "
                "was written"
            )
        if path.exists() and not replace and writer != book.key:
            whose = (
                "was written in this session from another book"
                if writer
                else "was there before this session"
            )
            raise ValueError(
                f"{self.named(path)} {whose}, so nothing was written: give another file name, "
                "or set replace to true"
            )
        return path

    def _in_folder(self, file: str | None, book: Book) -> Path:
        """The path in the output folder that file names, the book's name when file is None."""
        name = file if file is not None else _default_name(Path(book.name).stem)
        name = (name[:-4] if name.lower().endswith(".pgn") else name) + ".pgn"
        parts = name.split("/")
        if len(name) > _PATH_LENGTH or not all(_NAME.fullmatch(part) for part in parts):
            raise ValueError(
                f"{name!r} is not a plain file name or a path of folders and a file name: each "
                "part separated by /, of letters, digits, spaces and . _ - ( ) ', starting with "
                f"a letter or digit, at most 96 characters, and at most {_PATH_LENGTH} "
                "characters in all"
            )
        # A folder or file already there under a name in another case keeps its name, and a
        # folder on the way must be a real one. Where one is missing, the rest are new.
        path = self.folder
        for number, part in enumerate(parts):
            path = path / (_as_kept(path, part) if path.is_dir() else part)
            if number < len(parts) - 1:
                _unlinked(path)
                if path.exists() and not path.is_dir():
                    raise ValueError(f"{self.named(path)} is not a folder, so nothing was written")
        return path

    def _place(self, file: str | None) -> tuple[Path, Path, str]:
        """For a full path without an output folder: the folder as given, its real path and the
        file's name with .pgn. ValueError for a path that is not full, names a folder, has a file
        name that is not allowed, lies in what is not a folder or in pgnify's own folder, or
        cannot be read."""
        if file is None:
            raise ValueError(
                "give file, the full path of the PGN file, absolute or starting with ~"
            )
        path = full_path(file, "file")
        if re.split(r"[\\/]", file.rstrip())[-1] in ("", ".", ".."):
            raise ValueError(
                "file is the PGN file's full path, not a folder's: give the path with its file name"
            )
        name = path.name
        name = (name[:-4] if name.lower().endswith(".pgn") else name) + ".pgn"
        if not _NAME.fullmatch(name):
            raise ValueError(
                f"{name!r} is not a plain file name: letters, digits, spaces and . _ - ( ) ', "
                "starting with a letter or digit, at most 96 characters"
            )
        try:
            folder = path.parent.resolve()
            # Where a folder is missing, the rest are new, and the nearest that is there must be
            # one. A dangling link among the missing folders is refused, since no folder can be made
            # through it.
            below = []
            for parent in (folder, *folder.parents):
                if parent.exists():
                    break
                below.append(parent)
            there = parent
            if not there.is_dir():
                raise ValueError(
                    f"{shown(there)} is not a folder, so nothing was written: give another folder"
                )
            if any(missing.is_symlink() for missing in below):
                raise ValueError(
                    f"{shown(path.parent)} leads to a link that cannot be followed: give "
                    "another folder"
                )
        except OSError as error:  # a path the system will not read, such as another user's
            raise ValueError(
                f"{shown(path.parent)} cannot be read as a folder: give another folder"
            ) from error
        except RuntimeError as error:  # a link loop, in older versions of Python
            raise ValueError(
                f"{shown(path.parent)} leads to a link loop: give another folder"
            ) from error
        if folded(folder).is_relative_to(folded(self.work)):
            raise ValueError(f"{shown(self.work)} is pgnify's own folder: give a folder outside it")
        return path.parent, folder, name

    def wrote(self, path: Path, book: Book, games: list[str] | int, notes: str) -> None:
        """Record that path was written from book's games, their saved names or how many were
        given as text. Keep the notes beside it, or, given none, delete the notes kept with what
        the file held before."""
        name = self._key(path)
        if self.fenced:
            self.written[name.casefold()] = (book.key, book.path)
        elif identity := _identity(path):
            self.written_ids[identity] = (book.key, book.path)
        notes_file = notes_path(path)
        if notes:
            notes_file.write_text(
                f"{NOTES_HEADING}\n\n"
                f"What the AI assistant said of this conversion when it wrote "
                f"{self.named(path)} from {book.name}. pgnify has not checked it: `pgnify "
                "report` says what it could check, and neither these notes nor their absence "
                f"shows that nothing is missing.\n\n{notes}\n",
                encoding="utf-8",
            )
        elif notes_file.exists() and _notes_text(notes_file).startswith(NOTES_HEADING):
            notes_file.unlink()
        with locked(self.work / "lock"):
            records = _records(self.work / "outputs.json")
            records[name] = {
                "book": book.name,
                "book_path": str(book.path),
                "book_key": book.key,
                "games": games,
                "notes": bool(notes),
                "written": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            replace_json(self.work / "outputs.json", records)

    def _key(self, path: Path) -> str:
        """path's key in outputs.json: its place in the output folder, as a POSIX path, or its
        full real path."""
        return path.relative_to(self.folder).as_posix() if self.fenced else str(path)

    def named(self, path: Path) -> str:
        """path as a message names it."""
        return path.relative_to(self.folder).as_posix() if self.fenced else shown(path)

    def _file(self, key: str) -> Path:
        return self.folder / key if self.fenced else Path(key)

    def unwritten(self, book: Book) -> list[str]:
        """The names of book's saved games that no PGN file written from it holds, sorted. A file
        holds a game when it holds the game's saved text as a whole game, a blank line on each
        side, compared without the whitespace around the text and its lines."""
        held = [
            _whole(file.read_text(encoding="utf-8", errors="replace"))
            for name, record in read_records(self.work).items()
            if record.get("book_key") == book.key and (file := self._file(name)).is_file()
        ]
        saved = {
            path.stem: _whole(path.read_text(encoding="utf-8"))
            for path in self.book_folder(book).games.glob("*.pgn")
        }
        return sorted(
            name for name, game in saved.items() if not any(game in text for text in held)
        )

    def log(self, entry: dict[str, Any]) -> None:
        log = _unlinked(self.work / "log.jsonl")
        with locked(self.work / "lock"), log.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _identity(path: Path) -> tuple[int, int] | None:
    """The device and inode numbers of the file at path, which identify it whatever its name; None
    if it is missing."""
    try:
        found = path.stat()
    except OSError:
        return None
    return found.st_dev, found.st_ino


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
    """outputs.json in the folder at work: the record of each PGN file written. It is read under
    the folder's lock, as on Windows a file cannot be replaced while another process reads it."""
    file = work / "outputs.json"
    if not file.exists():
        return {}
    try:
        with locked(work / "lock"):
            return _records(file)
    except PermissionError:  # a folder that is read and not written
        return _records(file)


def _records(file: Path) -> dict[str, Any]:
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}


def replace_json(path: Path, value: Any) -> None:
    """Write a JSON file whole, through a temporary file, so that it is never found half
    written. Where another process has the file open, as on Windows, it tries a few times."""
    written = path.with_name(f"{path.name}.{os.getpid()}.new")
    # write_text would follow a link planted at the temporary name.
    written.unlink(missing_ok=True)
    text = json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    written.write_text(text, encoding="utf-8")
    for attempt in range(_REPLACES):
        try:
            os.replace(written, path)
            return
        except PermissionError:
            if attempt == _REPLACES - 1:
                raise
            time.sleep(0.05)


@contextmanager
def locked(path: Path) -> Generator[None]:
    """Hold an exclusive lock on the file at path, made if it is not there, until the block ends.
    Every process that takes it waits its turn, so that two sessions in one folder lose no record
    and no log line. The operating system frees it if a process dies."""
    with _unlinked(path).open("ab") as file:
        if sys.platform == "win32":
            import msvcrt

            # The first byte, which need not be there. LK_LOCK gives up after ten tries, a second
            # apart, with EDEADLOCK: another process holds the lock, so wait again. Any other
            # failure is raised.
            while True:
                file.seek(0)
                try:
                    msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EDEADLOCK, errno.EDEADLK):
                        raise
            try:
                yield
            finally:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(file, fcntl.LOCK_EX)
            yield  # closing the file frees the lock


def _whole(text: str) -> str:
    """PGN text with the whitespace around it and its lines removed, a blank line on each side,
    so that a game found in it is a whole game."""
    return "\n\n" + re.sub(r"[ \t]*\n[ \t]*", "\n", text.strip()) + "\n\n"


def _notes_text(notes: Path) -> str:
    return notes.read_text(encoding="utf-8", errors="replace")


def _default_name(stem: str) -> str:
    """A book's stem as a PGN file name, other characters replaced by dashes."""
    plain = re.sub(r"[^\w .()'-]+", "-", stem).strip(" ._-'()")
    return plain[:_DEFAULT_LENGTH] or "book"


def _as_kept(folder: Path, name: str) -> str:
    """The entry of folder whose name is name ignoring case: name itself if present, else the
    first match in sorted order, else name."""
    try:
        kept = sorted(
            other.name for other in folder.iterdir() if other.name.casefold() == name.casefold()
        )
    except OSError as error:  # a folder the system will not list, such as another user's
        raise ValueError(f"{shown(folder)} cannot be read, so nothing was written") from error
    return name if name in kept or not kept else kept[0]


def _unlinked(path: Path) -> Path:
    """path, or ValueError when it is a symbolic link, which pgnify never writes through."""
    if path.is_symlink():
        raise ValueError(f"{path.name} in pgnify's output folder is a link, so nothing was written")
    return path
