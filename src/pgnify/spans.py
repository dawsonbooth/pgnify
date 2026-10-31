"""Comments copied from the book, a prototype: a comment may name spans of a page's text, as
doc_text numbers its lines, and build_pgn copies each span exactly instead of the model writing
the book's words again. The model chooses what to copy and where; the tool copies the span
named, or fails and copies nothing. SPANS says how a span is given and copied.

Served only by `pgnify serve --spans`, an experimental option, off by default, which numbers
doc_text's lines and serves this build_pgn under its name."""

import re
from typing import Any, Required, TypedDict, cast, get_type_hints

from pgnify import chess_tools, doc_tools

SOFT_HYPHEN = "\u00ad"
# A copied span longer than twice this many words is shown by its first and last words.
_SHOWN = 5


class Span(TypedDict, total=False):
    """A span of the book's text to copy, as SPANS describes it."""

    page: Required[int]
    line: Required[int]
    first: Required[str]
    last_line: int
    last: Required[str]


_FIELDS = get_type_hints(Span)
# A part is described to the model as text or any object, and a span's fields are checked by
# _span. Typed as a Span, a span missing a field drew pydantic's errors for every branch of the
# union, including "should be a string", which a model took for the fix.
Comment = str | list[str | dict[str, Any]]


class SpannedMoveSpec(TypedDict, total=False):
    """A move in a line: its SAN, the comment after it, which may name spans of the book, its
    NAGs, and the variations that replace it: a list of lines, each a list of moves from the
    position before it."""

    san: Required[str]
    comment: Comment
    nags: list[int]
    variations: list[list["SpannedMoveSpec"]]


class SpannedGameSpec(TypedDict, total=False):
    """A game: its headers, the FEN it starts from if not the initial position, a comment
    before the first move, which may name spans of the book, its main line, and its result."""

    headers: dict[str, str]
    start_fen: str
    comment: Comment
    moves: list[SpannedMoveSpec]
    result: str


SPANS = """A comment may also be a list of parts, joined with spaces, each either text or a
span of the book to copy exactly, so that the book's words need not be written again: {page,
line, first, last_line, last}, the page, the line the span begins on as doc_text numbers it and
the words it begins with there, and the line it ends on, if another, and the words it ends with
there. Each line must hold its words once, whole and with their punctuation, as doc_text shows
them; otherwise the span is an error and nothing is copied. Lines are joined with a space, or
with nothing after a hyphen, and soft hyphens are removed. A span stays on its page: a note that
runs on to the next page is two spans. Where the book's text is wrong, as a scan's text layer
can be, give the right words as text, alone or between spans. Copying the same span twice is
reported, since the book's words belong in one place."""

COPIED = """With spans, the result also has copied, each span copied, by the path of its
comment, cut to its first and last five words, to check where it begins and ends; and repeated,
any span copied more than once."""

NUMBERED = """Each line of text is given with its number, as "12| text", which build_pgn's
spans name; the empty lines before an EPUB's paragraphs have none."""


def build_pgn_description() -> str:
    """build_pgn's description, as served with spans."""
    return f"{(chess_tools.build_pgn.__doc__ or '').rstrip()}\n\n{SPANS}\n\n{COPIED}"


def doc_text_description() -> str:
    """doc_text's description, as served with spans."""
    return f"{(doc_tools.doc_text.__doc__ or '').rstrip()}\n\n{NUMBERED}"


def build_pgn(
    spec: SpannedGameSpec | str,
    name: str | None = None,
    store: str | None = None,
    figurines: str | None = None,
    book: str | None = None,
) -> dict[str, Any]:
    """build_pgn, copying the spans the spec's comments name from the book at the path book."""
    copier = _Copier(book)
    known = chess_tools.read_figurines(figurines, besides=name)
    built = chess_tools.build_game(spec, known, copier.text)
    built = {
        **built,
        "ok": built["ok"] and not copier.errors,
        "errors": copier.errors + built["errors"],
    }
    if name is None:
        result = built
    else:
        assert store is not None, "a game saved by name needs a store"
        result = chess_tools.save_built(built, name, store, figurines)
    if copier.copied:
        result["copied"] = copier.copied
    if copier.repeated:
        result["repeated"] = copier.repeated
    return result


def copy_span(path: str, span: Span) -> str:
    """The text of span in the book at path, as SPANS describes it, or ValueError saying why it
    cannot be copied."""
    lines = doc_tools.page_lines(path, span["page"])
    first, last = span["line"], span.get("last_line", span["line"])
    for number in (first, last):
        if not 1 <= number <= len(lines):
            raise ValueError(
                f"page {span['page']} has no line {number}: its lines run from 1 to {len(lines)}"
            )
    if last < first:
        raise ValueError(f"the span ends on line {last}, before line {first}, where it begins")
    begin = _place(lines[first - 1], span["first"], first, "begins")
    end = _place(lines[last - 1], span["last"], last, "ends")
    if last == first:
        if end[1] <= begin[0]:
            raise ValueError(
                f"on line {first}, the words it ends with come before those it begins with"
            )
        return _joined([lines[first - 1][begin[0] : end[1]]])
    return _joined(
        [lines[first - 1][begin[0] :], *lines[first : last - 1], lines[last - 1][: end[1]]]
    )


class _Copier:
    """Turns a comment given as parts into text, keeping what it copied and what went wrong."""

    def __init__(self, book: str | None):
        self.book = book
        self.errors: list[dict[str, Any]] = []
        self.copied: list[dict[str, str]] = []
        self.repeated: list[dict[str, str]] = []
        self.seen: dict[tuple[Any, ...], str] = {}

    def text(self, comment: Comment, path: str) -> str:
        if isinstance(comment, str):
            return comment
        parts = []
        for index, part in enumerate(comment, start=1):
            if isinstance(part, str):
                parts.append(part)
                continue
            if self.book is None:
                self.errors.append({"path": path, "message": "name the book to copy spans from"})
                continue
            try:
                copied = copy_span(self.book, _span(part))
            except ValueError as error:
                self.errors.append({"path": path, "message": f"comment part {index}: {error}"})
                continue
            key = tuple(part.get(field) for field in _FIELDS)
            if key in self.seen:
                self.repeated.append(
                    {"path": path, "also_at": self.seen[key], "text": _ends(copied)}
                )
            self.seen.setdefault(key, path)
            self.copied.append({"path": path, "text": _ends(copied)})
            parts.append(copied)
        return " ".join(part.strip() for part in parts if part.strip())


def _span(part: dict[str, Any]) -> Span:
    """The part as a span, or ValueError saying what it lacks or has wrong."""
    said = []
    if missing := [field for field in _FIELDS if field in Span.__required_keys__ - set(part)]:
        said.append(f"it has no {', '.join(missing)}")
    if unknown := sorted(set(part) - set(_FIELDS)):
        said.append(f"not a field of a span: {', '.join(unknown)}")
    said += [
        f"{field} should be {'a number' if kind is int else 'text'}"
        for field, kind in _FIELDS.items()
        if field in part and not isinstance(part[field], kind)
    ]
    if said:
        raise ValueError(
            "a span is {page, line, first, last_line, last}, last_line optional: " + "; ".join(said)
        )
    return cast(Span, part)


def _place(line: str, words: str, number: int, which: str) -> tuple[int, int]:
    """Where the words stand in the line, whole, or ValueError when they do not stand there
    exactly once."""
    parts = words.split()
    if not parts:
        raise ValueError(f"no words given for where the span {which}")
    pattern = r"(?<!\S)" + r"\s+".join(re.escape(part) for part in parts) + r"(?!\S)"
    found = [match.span() for match in re.finditer(pattern, line)]
    if len(found) == 1:
        return found[0]
    shown = " ".join(line.split())
    if not found:
        raise ValueError(
            f"line {number} does not hold {words!r} as whole words, where the span {which}: "
            f"the line is {shown!r}"
        )
    raise ValueError(
        f"line {number} holds {words!r} {len(found)} times, where the span {which}: give more of "
        f"its words; the line is {shown!r}"
    )


def _joined(pieces: list[str]) -> str:
    """The pieces of a span's lines as one text: joined by a space, but directly after a hyphen
    or a soft hyphen, and the soft hyphens removed."""
    text = ""
    for piece in pieces:
        piece = piece.strip()
        if not text:
            text = piece
        elif text.endswith(SOFT_HYPHEN):
            text = text[:-1] + piece
        elif text.endswith("-"):
            text += piece
        elif piece:
            text += " " + piece
    return " ".join(text.replace(SOFT_HYPHEN, "").split())


def _ends(text: str) -> str:
    words = text.split()
    if len(words) <= 2 * _SHOWN:
        return text
    return " ".join(words[:_SHOWN]) + " … " + " ".join(words[-_SHOWN:])
