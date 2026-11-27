"""Comments copied from the book: a comment may name passages of a page's text, and build_pgn
copies each exactly, instead of the model writing the book's words again. The model chooses what
to copy and where; the tool copies the passage named, or fails and copies nothing. COPIES says how
a copy is given and made, and the server serves this build_pgn under its name.

A copy begins where its from words stand on its page, once, and ends where its to words next end,
which on an EPUB may be on the next page. It reads the page's text as doc_text gives it, from the
text layer or the EPUB's own text, never an OCR reading. build_pgn's pieces, which the model
gives, say which letter each figurine of the book is; they are applied to every comment."""

import difflib
import itertools
import re
import unicodedata
from pathlib import Path
from types import TracebackType
from typing import Any, Required, TypedDict, cast, get_type_hints

import pymupdf

from pgnify import chess_tools, doc_tools
from pgnify.paragraphs import paragraph_starts

SOFT_HYPHEN = "\u00ad"
# A copied passage longer than twice this many words is shown by its first and last words.
_SHOWN = 5
# What an error shows of the page's text: the characters before and after the place where the
# words looked for nearly stand, and at most this many places where they stand more than once.
_BEFORE, _AFTER = 40, 80
_PLACES = 5
# A picture's mark in a page's text, or a figurine printed as a character.
_FIGURINE = re.compile(r"\[picture \d+: [0-9a-f]{6}\]|[\u2654-\u265f]")
# A figurine printed as a Unicode character.
_UNICODE_FIGURINE = re.compile("[\u2654-\u265f]")
# The marks of the pictures a line ends with.
_PICTURES_AT_END = re.compile(r"(?:\[picture \d+: [0-9a-f]{6}\] ?)+$")
# The Unicode categories of a character pieces can take for a figurine: other, currency and
# modifier symbols, and private-use codes, as a chess font's are; not a mathematical sign such as
# \u00b1, which the book's marks use.
_SYMBOLS = ("So", "Sc", "Sk", "Co")
# The quotes a copy's words may give for each other: the book's own are copied.
_QUOTES = {
    **dict.fromkeys("'\u2018\u2019", "['\u2018\u2019]"),
    **dict.fromkeys('"\u201c\u201d', '["\u201c\u201d]'),
}
# A picture's id as doc_text prints it.
_IDENTIFIER = re.compile(r"[0-9a-f]{6}")
_LETTERS = "KQRBN"

Copy = TypedDict(
    "Copy",
    {
        "page": Required[int],
        "from": Required[str],
        "to": Required[str],
        "fix": list[list[str]],
    },
    total=False,
)
"""A passage of the book's text to copy, as COPIES describes it."""

_FIELDS = get_type_hints(Copy)
# A part is described to the model as text or any object, and a copy's fields are checked by
# _copy. Typed as a Copy, a copy missing a field would get pydantic's errors for every branch of
# the union, among them "should be a string", which reads as the fix.
Comment = str | list[str | dict[str, Any]]


class CopyingMoveSpec(TypedDict, total=False):
    """A move in a line: its SAN, the comment after it, which may copy from the book, its NAGs,
    and the variations that replace it: a list of lines, each a list of moves from the position
    before it."""

    san: Required[str]
    comment: Comment
    nags: list[int]
    variations: list[list["CopyingMoveSpec"]]


class CopyingGameSpec(TypedDict, total=False):
    """A game: its headers, the FEN it starts from if not the initial position, a comment
    before the first move, which may copy from the book, its main line, and its result."""

    headers: dict[str, str]
    start_fen: str
    comment: Comment
    moves: list[CopyingMoveSpec]
    result: str


COPIES = """A comment may also be a list of parts, each text or a copy of the book's words: {page,
from, to, fix}. from is the words the passage begins with, whole, with punctuation, as doc_text
shows them, standing on the page once; to is the words it ends with, the first place after from that
has them, on that page or, in an EPUB only, the next: in a PDF, or where an EPUB's page ends with a
picture, a copy stays on its page, and the next page's part is a copy of its own.
Spaces and line breaks between words do not matter, and ' and \u2019 match alike in from and to, as
" and \u201c \u201d do; the copy keeps the book's own characters. In an EPUB, a paragraph, heading
or list item begins a line; a PDF has no such marks, so between a PDF's paragraphs put a "\\n" text
part. A copy that cannot be made is an error, and nothing is copied. fix, optional, is a list of
[printed, written]: each printed, once in the passage, within one line, and anywhere, even inside a
word, is replaced by written, which may be empty. Parts are joined with spaces, except at a line
break a text part begins or ends with. The result also has copied (each copy's path, first and last
five words, word count), fixed, and repeated (a copy overlapping an earlier one)."""

PIECES = """pieces, optional: the piece letter of each figurine the book prints, as the book's key
gives them, an object such as {"0ebed3": "N", "♗": "B", "¢": "K"}. A key is a picture's id as
doc_text prints it, or one character the book prints for a piece, a Unicode figurine or a chess
font's code: a symbol or a private-use character, not ASCII, a letter, a digit, punctuation or a
sign such as ±, since prose and the book's marks use those; a value is K, Q, R, B or N. In every
comment, copied or typed, each such picture mark and character becomes its letter; in a move, a
picture in pieces is that piece, and a move not legal with it is an error.
Give every figurine a copy may carry: pgnify has no table of figurines of its own. A comment that
still holds a Unicode figurine is listed in the result's figurines_left, {path, figurines}, and is
no error, since a book's key may quote its figurines.
In a game built in parts, a part's pieces add to those its earlier parts gave, and a key given
again takes the later letter throughout the game; a part whose pieces read a saved move's picture
as another piece is refused, and a build without after keeps none of the pieces earlier parts
gave."""


def build_pgn_description() -> str:
    """build_pgn's description, as served: the tool's, how a comment copies from the book, and
    the figurines' pieces."""
    return f"{(chess_tools.build_pgn.__doc__ or '').rstrip()}\n\n{COPIES}\n\n{PIECES}"


def build_pgn(
    spec: CopyingGameSpec | str,
    name: str | None = None,
    store: str | None = None,
    figurines: str | None = None,
    book: str | None = None,
    pieces: dict[str, str] | None = None,
    after: str | None = None,
) -> dict[str, Any]:
    """build_pgn, copying the passages the spec's comments name from the book at the path book,
    and reading the figurines as PIECES says. With after, the spec is a part of the game saved as
    name, merged into it and built whole, its pieces added to those the game's builds gave.
    ValueError for pieces that are not as PIECES says, and for a part that cannot be added."""
    others = chess_tools.read_figurines(figurines, besides=name)
    whole = chess_tools.read_spec(spec)
    if after is None:
        game_pieces = pieces
        pictures, characters = _lettered(game_pieces)
    else:
        record = chess_tools.saved_game(name, store)
        game_pieces = {**record["pieces"], **(pieces or {})}
        pictures, characters = _lettered(game_pieces)
        _keep_saved_moves(pictures, record["pictures"])
        known = others | record["pictures"] | pictures
        whole = chess_tools.merge_part(record, whole, after, known)
    with _Pages(book) as pages:
        copier = _Copier(pages, characters)
        # What the model gives overrides what an earlier game read.
        built = chess_tools.build_game(whole, others | pictures, copier.text)
    errors = [_with_pieces(error, pictures) for error in built["errors"]]
    built = {
        **built,
        "ok": built["ok"] and not copier.errors,
        "errors": copier.errors + errors,
    }
    if name is None:
        result = built
    else:
        assert store is not None, "a game saved by name needs a store"
        result = chess_tools.save_built(
            built, name, store, figurines, {"spec": whole, "pieces": game_pieces or {}}
        )
    for key in ("copied", "fixed", "repeated", "figurines_left"):
        if found := getattr(copier, key):
            result[key] = found
    return result


def _lettered(pieces: dict[str, str] | None) -> tuple[dict[str, str], dict[str, str]]:
    """The pieces the model gives, as the pictures' ids with their letters and the characters with
    theirs, or ValueError saying what is wrong with them."""
    if pieces is None:
        return {}, {}
    if not isinstance(pieces, dict):
        raise ValueError("pieces is an object of figurines and their letters, as PIECES says")
    said = []
    for key, letter in pieces.items():
        if not (isinstance(key, str) and (_IDENTIFIER.fullmatch(key) or len(key) == 1)):
            said.append(f"{key!r} is neither a picture's id, 6 hex digits, nor one character")
        elif not _IDENTIFIER.fullmatch(key) and (
            key.isascii() or unicodedata.category(key) not in _SYMBOLS
        ):
            # Such a character stands in prose too, and every comment's would become a letter.
            said.append(
                f"no key may be {key!r}, since prose or the book's marks may use it: give that "
                "figurine's letter by a fix"
            )
        if letter not in tuple(_LETTERS):
            said.append(f"the letter for {key!r} is {letter!r}, not one of K, Q, R, B or N")
    if said:
        raise ValueError("pieces: " + "; ".join(said))
    pictures = {key: letter for key, letter in pieces.items() if _IDENTIFIER.fullmatch(key)}
    return pictures, {key: letter for key, letter in pieces.items() if key not in pictures}


def _keep_saved_moves(pictures: dict[str, str], read: dict[str, str]) -> None:
    """ValueError when pictures, the letters the pieces give, read a picture as another piece than
    read, the pieces the saved game's moves read the pictures as."""
    changed = [
        f"pieces gives the picture {picture} as {letter}, where the saved game read it as "
        f"{read[picture]}"
        for picture, letter in pictures.items()
        if picture in read and read[picture] != letter
    ]
    if changed:
        raise ValueError(
            "; ".join(changed) + ": a part cannot change a saved move, so leave each such picture "
            "out of pieces, or build the game again without after"
        )


def _with_pieces(error: dict[str, Any], pictures: dict[str, str]) -> dict[str, Any]:
    """The error with what pieces adds to it: for a move that is not legal, which of its pictures
    pieces gave; for a picture no move reads, that pieces can give its letter."""
    if "legal_moves" not in error:
        return {
            **error,
            "message": error["message"].replace(
                "give each such piece by its letter",
                "give each such piece's letter in pieces or in the comment",
            ),
        }
    given = [
        f"{picture} as {pictures[picture]}"
        for picture in dict.fromkeys(chess_tools._PICTURE.findall(error.get("move", "")))
        if picture in pictures
    ]
    if not given:
        return error
    return {**error, "message": f"{error['message']} (pieces gives the picture {', '.join(given)})"}


# Where a passage begins or ends: a page, and an offset in the text of its lines joined.
Place = tuple[int, int]


def copy_passage(pages: "_Pages", copy: Copy) -> tuple[str, Place, Place, list[tuple[str, str]]]:
    """The passage the copy names in the pages of a book, as COPIES describes it, where it begins
    and ends, and the fixes made in it, or ValueError saying why it cannot be copied."""
    number = copy["page"]
    page = pages.page(number)
    text, extents = _joined([page])
    found = _find(text, copy["from"], "begins")
    if not found:
        raise ValueError(
            f"page {number} does not hold {copy['from']!r} as whole words, where the copy "
            f"begins; the nearest text is: {_window(text, _nearest(text, copy['from']))}"
        )
    if len(found) > 1:
        raise ValueError(
            f"page {number} holds {copy['from']!r} {len(found)} times, where the copy begins: "
            f"give more of its words; they stand at {_around(text, found)}"
        )
    begin, after = found[0]
    ends = [end for _, end in _find(text, copy["to"], "ends") if end >= after]
    following = None
    if not ends and pages.epub:
        try:
            following = pages.page(number + 1)
        except ValueError:
            pass
        else:
            text, extents = _joined([page, following])
            ends = [end for _, end in _find(text, copy["to"], "ends") if end >= after]
    if not ends:
        message = (
            f"{f'pages {number} and {number + 1} hold' if following else f'page {number} holds'} "
            f"no {copy['to']!r} as whole words ending at or after where the copy begins; the "
            f"nearest text is: {_window(text, _nearest(text, copy['to'], begin))}"
        )
        if not pages.epub:
            message += (
                f"{'' if message.endswith('…') else '.'} A copy in a PDF stays on its page: end "
                f"this copy with its last words on page {number}, and copy the rest from page "
                f"{number + 1} as a copy of its own"
            )
        raise ValueError(message)
    end = min(ends)
    if end > extents[0][1] and (pictures := _ending_pictures(page)):
        raise ValueError(
            f"page {number} ends with {pictures}: in an EPUB's layout a picture at the end of a "
            f"page is a diagram or another picture, a figurine whose move goes on at the top of "
            f"page {number + 1}, or a figurine that page {number + 1} prints again there. End this "
            f"copy with its last words on page {number}, and copy the rest from page {number + 1} "
            "as a copy of its own; a fix can leave out a figurine printed twice"
        )
    passage, fixed = _fixed(text[begin:end], copy.get("fix", []))
    last = (number, end) if end <= extents[0][1] else (number + 1, end - extents[1][0])
    return passage, (number, begin), last, fixed


def _ending_pictures(page: list[tuple[str, bool]]) -> str | None:
    """The marks of the pictures the last line of the page's text ends with, if it ends with
    any: alone, several together, or after the line's words."""
    last = next((" ".join(line.split()) for line, _ in reversed(page) if line.strip()), "")
    found = _PICTURES_AT_END.search(last)
    return found[0].strip() if found else None


class _Pages:
    """The pages of one book, each read once while the book is open, since opening a book lays it
    out, which takes longer than a copy. With no path, it holds no book."""

    def __init__(self, path: str | None):
        self.path = path
        self.epub = path is not None and Path(path).suffix.lower() == ".epub"
        self.doc: pymupdf.Document | None = None
        self.read: dict[int, list[tuple[str, bool]]] = {}

    def __enter__(self) -> "_Pages":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.doc:
            self.doc.close()
            self.doc = None

    def page(self, number: int) -> list[tuple[str, bool]]:
        """The lines of a page's text, each with whether it begins a block, a paragraph, a heading
        or an item of a list, as doc_text marks an EPUB's blocks; a PDF has no such marks."""
        assert self.path is not None, "a copy reads a book"
        if number not in self.read:
            if self.doc is None:
                self.doc = pymupdf.open(self.path)
            lines = doc_tools.lines_of(self.doc, number)
            begun = paragraph_starts(self.path).get(number, frozenset()) if self.epub else ()
            self.read[number] = [(line, index in begun) for index, line in enumerate(lines)]
        return self.read[number]


class _Copier:
    """Turns a comment given as parts into text, keeping what it copied and what went wrong,
    writing each of the characters in the pieces given as its letter, and listing the comments
    that still hold a Unicode figurine."""

    def __init__(self, pages: _Pages, characters: dict[str, str]):
        self.pages = pages
        self.characters = str.maketrans(characters)
        self.errors: list[dict[str, Any]] = []
        self.copied: list[dict[str, Any]] = []
        self.fixed: list[dict[str, str]] = []
        self.repeated: list[dict[str, str]] = []
        self.seen: list[tuple[Place, Place, str]] = []
        self.figurines_left: list[dict[str, Any]] = []

    def text(self, comment: Comment, path: str) -> str:
        text = self._text(comment, path)
        if left := list(dict.fromkeys(_UNICODE_FIGURINE.findall(text))):
            self.figurines_left.append({"path": path, "figurines": left})
        return text

    def _text(self, comment: Comment, path: str) -> str:
        if isinstance(comment, str):
            return comment.translate(self.characters)
        parts = []
        for index, part in enumerate(comment, start=1):
            if isinstance(part, str):
                parts.append(part.translate(self.characters))
                continue
            if self.pages.path is None:
                self.errors.append({"path": path, "message": "name the book to copy from"})
                continue
            try:
                passage, begin, end, fixes = copy_passage(self.pages, _copy(part))
            except ValueError as error:
                self.errors.append({"path": path, "message": f"comment part {index}: {error}"})
                continue
            passage = passage.translate(self.characters)
            shown = _ends(passage)
            before = next(
                (where for first, last, where in self.seen if first < end and begin < last), None
            )
            if before is not None:
                self.repeated.append({"path": path, "also_at": before, "text": shown})
            self.seen.append((begin, end, path))
            self.copied.append({"path": path, "text": shown, "words": len(passage.split())})
            self.fixed += [
                {"path": path, "printed": printed, "written": written} for printed, written in fixes
            ]
            parts.append(passage)
        return _assembled(parts)


def _copy(part: dict[str, Any]) -> Copy:
    """The part as a copy, or ValueError saying what it lacks or has wrong."""
    said = []
    if missing := [field for field in _FIELDS if field in Copy.__required_keys__ - set(part)]:
        said.append(f"it has no {', '.join(missing)}")
    if unknown := sorted(set(part) - set(_FIELDS)):
        said.append(f"not a field of a copy: {', '.join(unknown)}")
    said += [
        f"{field} should be {'a number' if kind is int else 'text'}"
        for field, kind in _FIELDS.items()
        if field in part and kind in (int, str) and not _is(part[field], kind)
    ]
    fix = part.get("fix", [])
    if not isinstance(fix, list) or not all(
        isinstance(pair, list) and len(pair) == 2 and all(isinstance(word, str) for word in pair)
        for pair in fix
    ):
        said.append("fix should be a list of [printed, written] pairs of text")
    if said:
        raise ValueError("a copy is {page, from, to, fix}, fix optional: " + "; ".join(said))
    return cast(Copy, part)


def _is(value: Any, kind: type) -> bool:
    return isinstance(value, kind) and not isinstance(value, bool)


def _joined(pages: list[list[tuple[str, bool]]]) -> tuple[str, list[tuple[int, int]]]:
    """The lines of the pages as one text, and where each page's lines begin and end in it. Lines
    are joined by a space, but directly after a hyphen or a soft hyphen, the soft hyphens
    removed, and after an ellipsis before a picture or a figurine, as in "2...[picture 5: f4c1b7]",
    where the book's markup has no space; a line that begins a block begins a line of the text,
    and a page break is joined as a line break is."""
    text, glue = "", ""
    extents = []
    for page in pages:
        begin = None
        for line, begins in page:
            piece = " ".join(line.split())
            clean = piece.replace(SOFT_HYPHEN, "")
            if not clean:
                continue
            if text:
                if begins:
                    text += "\n"
                elif glue == " " and text.endswith(("...", "…")) and _FIGURINE.match(clean):
                    pass
                else:
                    text += glue
            if begin is None:
                begin = len(text)
            text += clean
            glue = "" if piece.endswith(SOFT_HYPHEN) or clean.endswith("-") else " "
        extents.append((len(text) if begin is None else begin, len(text)))
    return text, extents


def _find(text: str, words: str, where: str) -> list[tuple[int, int]]:
    """Where the words stand in the text, whole, each as its start and end, overlapping ones
    included, or ValueError when there are no words. Between words, white space may be of any
    amount, and none after a hyphen, where a line broke; a quote may be of either kind of its
    sort, straight or curved."""
    parts = words.replace(SOFT_HYPHEN, "").split()
    if not parts:
        raise ValueError(f"no words given for where the copy {where}")
    pattern = "".join(
        (r"\s*" if parts[index - 1].endswith("-") else r"\s+" if index else "")
        + "".join(_QUOTES.get(char) or re.escape(char) for char in part)
        for index, part in enumerate(parts)
    )
    return [match.span(1) for match in re.finditer(rf"(?<!\S)(?=({pattern})(?!\S))", text)]


def _fixed(passage: str, fixes: list[list[str]]) -> tuple[str, list[tuple[str, str]]]:
    """The passage with each printed text of the fixes replaced by what is written for it, or
    ValueError when one does not occur in it exactly once, runs over a line break, or two occur
    in the same place. A space beside an empty written text goes with it."""
    places = []
    for printed, written in fixes:
        parts = printed.split()
        if not parts:
            raise ValueError("a fix has no printed text to replace")
        pattern = r"\s+".join(re.escape(part) for part in parts)
        found = [match.span(1) for match in re.finditer(rf"(?=({pattern}))", passage)]
        if not found:
            raise ValueError(
                f"fix {printed!r} does not occur in the passage; the nearest text is: "
                f"{_window(passage, _nearest(passage, printed))}"
            )
        if len(found) > 1:
            raise ValueError(
                f"fix {printed!r} occurs {len(found)} times in the passage: give more of its "
                f"words; they stand at {_around(passage, found)}"
            )
        if "\n" in passage[slice(*found[0])]:
            raise ValueError(
                f"fix {printed!r} runs over a line break of the passage, which it would delete: "
                "fix each line's words in a fix of its own"
            )
        places.append((*found[0], written))
    places.sort()
    for (_, last, _), (first, _, _) in itertools.pairwise(places):
        if first < last:
            raise ValueError("two fixes replace the same words of the passage")
    for first, last, written in reversed(places):
        if not written:
            before, after = passage[max(0, first - 1) : first], passage[last : last + 1]
            if after == " " and before in ("", " ", "\n"):
                last += 1
            elif before == " " and after in ("", "\n"):
                first -= 1
        passage = passage[:first] + written + passage[last:]
    return passage, [(printed, written) for printed, written in fixes]


def _assembled(parts: list[str]) -> str:
    """The parts of a comment as one text: joined by a space, except where a part begins or ends
    with a line break, which stays as written, so that a text part "\\n\\n" between two copies
    sets each on a line of its own."""
    text = ""
    for part in parts:
        part = part.strip(" \t")
        if not part:
            continue
        if text and not text.endswith("\n") and not part.startswith("\n"):
            text += " "
        text += part
    return text.strip()


def _nearest(text: str, words: str, start: int = 0) -> int:
    """Where in the text from start the words come nearest to standing: where they would begin
    if the longest run of characters they share with it were theirs."""
    wanted = " ".join(words.split())
    match = difflib.SequenceMatcher(None, text[start:], wanted, autojunk=False).find_longest_match()
    return start + max(0, match.a - match.b)


def _window(text: str, at: int) -> str:
    """The text around the place at, on one line, a little over a hundred characters, cut short
    with an ellipsis where the text goes on."""
    first = max(0, at - _BEFORE)
    shown = " ".join(text[first : at + _AFTER].split())
    return ("…" if first else "") + shown + ("…" if at + _AFTER < len(text) else "")


def _around(text: str, found: list[tuple[int, int]]) -> str:
    """Each place in the text, up to _PLACES of them, with the words around it."""
    shown = "; ".join(_window(text, first) for first, _ in found[:_PLACES])
    return shown + (f"; and {len(found) - _PLACES} more" if len(found) > _PLACES else "")


def _ends(text: str) -> str:
    words = text.split()
    if len(words) <= 2 * _SHOWN:
        return " ".join(words)
    return " ".join(words[:_SHOWN]) + " … " + " ".join(words[-_SHOWN:])
