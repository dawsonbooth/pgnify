"""The prose of comments and of a book's text, in a form that compares: the words between move
notation, with typography and spacing made uniform, cut into sentences. `pgnify check` and
`pgnify report` look for each sentence of a PGN file's comments in the book's text, and the
evaluation's scorer compares a run's comments with its truth's the same way. Move notation is
left out of both, so that a book's figurines and a comment's piece letters compare equal."""

import re
from pathlib import Path

import pymupdf

from pgnify.doc_tools import page_text

# Move notation as books and comments print it, with its number, marks and punctuation.
MOVE = re.compile(
    r"(?P<number>\d+\.{0,3})?\.{0,3}"
    r"(?P<body>[KQRBN][a-h1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8]|[a-h][1-8]|O-O(-O)?|0-0(-0)?)"
    r"(?P<promotion>=[QRBN])?[+#]?[!?]*[,.;:]*"
)
# A move number, or a list mark such as "2a)", alone.
NUMBER = re.compile(r"\d+(\.{1,3}|[a-z])?[,.;:]*")
RESULT = re.compile(r"(1-0|0-1|1/2-1/2|\u00bd-\u00bd)[,.;:]*")
# A soft hyphen only marks where a word may break: it is removed, with the line break after it.
SOFT = re.compile(r"\u00ad\s*")
# Curly quotes, dashes and the ellipsis as typed on a keyboard; a bullet as a space; and
# parentheses removed, since a run may give a book's parenthesis as a comment of its own.
TYPOGRAPHY = str.maketrans(
    {
        "\u2019": "'",
        "\u2018": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u2022": " ",
        "(": "",
        ")": "",
    }
)
COMMAND = re.compile(r"\[%[^\]]*\]")
PICTURE = re.compile(r"\[picture \d+: [0-9a-f]{6}\]")
ALPHANUMERIC = re.compile(r"[^\W_]")
# A word that ends a sentence, and one that can begin the next.
_ENDS_SENTENCE = re.compile(r"[.!?][\"']*$")
_BEGINS_SENTENCE = re.compile(r"[\"']?[A-Z]")


def bare(text: str) -> str:
    """The text's words without punctuation at their ends."""
    return " ".join(word for word in (w.strip(".,;:!?'\"") for w in text.split()) if word)


def prose(text: str) -> str:
    """The text's passages of words between move notation, joined by spaces."""
    return " ".join(passages(text))


def passages(text: str) -> list[str]:
    """The text's passages of words between move notation, with typography and spacing made
    uniform; PGN's commands, a source's picture markers and parentheses removed; and tokens that
    hold no letter or digit, such as a stray "..", dropped."""
    return [passage for passage, _, _ in notated_passages(text)]


def notated_passages(text: str) -> list[tuple[str, int, bool]]:
    """Each passage of words between move notation, with how many moves come before it and
    whether a move follows it, ignoring a move number between them. A bare square, such as "e5",
    divides passages but counts as a move only after a move number."""
    text = SOFT.sub("", PICTURE.sub(" ", COMMAND.sub(" ", text))).translate(TYPOGRAPHY)
    found: list[tuple[str, int, bool]] = []
    words: list[str] = []
    moves = 0
    waiting = None  # a passage ended by a move number, until the next token says what follows
    numbered = False
    for token in text.split():
        if match := MOVE.fullmatch(token):
            move = numbered or _is_move(token, match)
            if words:
                found.append((" ".join(words), moves, move))
                words = []
            elif waiting is not None:
                found[waiting] = (*found[waiting][:2], move)
            moves += move
            waiting, numbered = None, False
        elif (number := NUMBER.fullmatch(token)) or RESULT.fullmatch(token):
            if words:
                found.append((" ".join(words), moves, False))
                words = []
                waiting = len(found) - 1
            numbered = bool(number)
        elif ALPHANUMERIC.search(token):
            words.append(token)
            waiting, numbered = None, False
    if words:
        found.append((" ".join(words), moves, False))
    return found


def _is_move(token: str, match: re.Match[str]) -> bool:
    """Whether notation is a move rather than a bare square: it has a move number or dots, a
    piece, a capture, castling or a promotion."""
    body = match["body"]
    return (
        bool(match["number"])
        or token.startswith(".")
        or body[0] in "KQRBNO0"
        or "x" in body
        or bool(match["promotion"])
    )


def sentences(passage: str) -> list[str]:
    """A passage's sentences: cut after a word ending in a full stop, an exclamation mark or a
    question mark, where the next word begins with a capital."""
    words = passage.split()
    found, current = [], []
    for index, word in enumerate(words):
        current.append(word)
        following = words[index + 1] if index + 1 < len(words) else None
        if _ENDS_SENTENCE.search(word) and (following is None or _BEGINS_SENTENCE.match(following)):
            found.append(" ".join(current))
            current = []
    if current:
        found.append(" ".join(current))
    return found


def shortened(text: str, words: int) -> str:
    """The text's first words, with an ellipsis when there are more."""
    parts = text.split()
    return " ".join(parts[:words]) + (" …" if len(parts) > words else "")


def book_prose(path: Path | str) -> str:
    """The prose of a book's pages, as comments' prose is compared with it."""
    with pymupdf.open(path) as doc:
        return " ".join(prose(page_text(page)) for page in doc)
