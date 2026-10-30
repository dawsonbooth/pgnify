"""Chess tools for the model: plain functions on python-chess, each returning a JSON-serialisable
dict. python-chess decides legality and reads and writes PGN; these functions parse the model's
movetext, read figurines from legality, build games from specs and find text python-chess
drops."""

import difflib
import io
import itertools
import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Required, TypedDict

import chess
import chess.pgn
import chess.svg
import pymupdf

from pgnify.outputs import replace_json

_MOVE_NUMBER = re.compile(r"^\d+(\.*)$")
_GLUED_MOVE_NUMBER = re.compile(r"^\d+(\.+)(\S+)$")
_MOVE_NUMBER_PREFIX = re.compile(r"^\d*(\.+|…)")
# The side a move number's periods mark: one for White, three for Black.
_PERIODS = {".": chess.WHITE, "...": chess.BLACK}
_SUFFIX = re.compile(r"(!!|\?\?|!\?|\?!|!|\?)$")
_SUFFIX_NAGS = {"!": 1, "?": 2, "!!": 3, "??": 4, "!?": 5, "?!": 6}
_SIDES = {chess.WHITE: "White", chess.BLACK: "Black"}
_RESULTS = ("1-0", "0-1", "1/2-1/2", "*")
_RESULTS_TEXT = "1-0, 0-1, 1/2-1/2 or *"
_NAG = re.compile(r"^(\$\d+|[!?]{1,2})$")
_TOKEN = re.compile(r"[{}();]|[^\s{}();]+")
_GAME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
# A figurine as the model gives it: by its picture's id, [0ebed3], or as doc_text prints it,
# [picture 3: 0ebed3].
_PICTURE = re.compile(r"\[(?:picture \d+: )?([0-9a-f]{6})\]")
# A figurine separated from the rest of its move by whitespace, as doc_text lays out
# "2 [0ebed3] c3".
_SPACED = re.compile(r"(?<![^\s.(])(\[[0-9a-f]{6}\])\s+(?=x?[a-h]?[1-8]?x?[a-h][1-8])")
_PIECES = "KQRBN"
_FEN_FIELDS = (
    "placement",
    "side_to_move",
    "castling",
    "en_passant",
    "halfmove_clock",
    "fullmove_number",
)
_NEITHER_SIDE = (
    "the position was given as a placement alone and neither side to move plays this line, "
    "so White was assumed"
)


def validate_moves(
    start: str, moves: list[str] | str, figurines: str | None = None
) -> dict[str, Any]:
    """Replay moves in SAN from a position and report where they stop being legal.

    start: "start" for the initial position, or a FEN. Given a placement alone, the side to
    move is the one the line's first move number marks (1. for White, 1... for Black); without
    one, it is deduced as the only side that can play the whole line, or White is assumed when
    both sides can or neither can.
    moves: a line of PGN movetext, as one string or a list: SAN moves with move numbers (1., 1...
    or 1), suffix annotations (! ? !! ?? !? ?!), NAGs ($1) and a final result, as PGN allows.
    A move number marking the other side's move is an error, and so is a capture, check or mate
    mark that the move contradicts. Give a piece the book prints as a picture by the picture's
    id, as build_pgn takes it: "2 [0ebed3]c3".

    Returns ok; start_fen, the position replayed from, in full; side_to_move, "w" or "b", and
    side_to_move_basis: "given" by the FEN, "notation", "deduced" or "assumed"; plies, each with
    its SAN and the FEN after it; final_fen, the position reached; error, null when every move
    replays, else the index of the first move that does not, the move, why, and the legal moves
    there in SAN; and pictures, when the line gives any, each picture read with its piece, as
    far as the line is legal.
    """
    text = moves if isinstance(moves, str) else " ".join(moves)
    line = _movetext(_given_pictures(text).split())
    sans = [san for san, _ in line]
    known = read_figurines(figurines)
    board, basis = _start(start, sans, line[0][1] if line else None, known)
    labels = [f"index {index}" for index in range(len(sans))]
    readings, reaches = _readings([(board, sans)], known)
    reading = _Reading(readings, [(sans, labels)], reaches)
    start_fen = board.fen()
    found = reading.given(sans)
    result: dict[str, Any] = {
        "ok": True,
        "start_fen": start_fen,
        "side_to_move": _side(board),
        "side_to_move_basis": basis,
        "plies": [],
        "final_fen": start_fen,
        "error": None,
        **({"pictures": found} if found else {}),
    }
    for index, (san, marked) in enumerate(line):
        try:
            if marked is not None and marked != board.turn:
                raise ValueError(
                    f"the move number before {san!r} marks {_SIDES[marked]}'s move, "
                    f"but {_SIDES[board.turn]} is to move"
                )
            move = _parse(board, reading.move(san, labels[index]))
        except ValueError as error:
            unplayable = _unplayable(board, san, error)
            if basis == "assumed" and not any(
                _plays(chess.Board(start_fen), side, sans, known)
                for side in (chess.WHITE, chess.BLACK)
            ):
                unplayable["message"] += f" ({_NEITHER_SIDE})"
            result |= {"ok": False, "error": {"index": index, **unplayable}}
            break
        played = board.san(move)
        board.push(move)
        result["plies"].append({"san": played, "fen": board.fen()})
    result["final_fen"] = board.fen()
    return result


def _movetext(tokens: list[str]) -> list[tuple[str, chess.Color | None]]:
    """A line of PGN movetext as its moves, each with the side its move number marks: White by
    one period, Black by three, else None. Move numbers, NAGs, suffix annotations and a final
    result are PGN syntax, not moves; any other token is passed on as a move, for python-chess
    to accept or refuse."""
    line: list[tuple[str, chess.Color | None]] = []
    marked = None
    for index, token in enumerate(tokens):
        if _NAG.match(token) or (token in _RESULTS and index == len(tokens) - 1):
            continue
        if number := _MOVE_NUMBER.match(token):
            marked = _PERIODS.get(number.group(1))
            continue
        if glued := _GLUED_MOVE_NUMBER.match(token):
            marked, token = _PERIODS.get(glued.group(1)), glued.group(2)
        line.append((_SUFFIX.sub("", token), marked))
        marked = None
    return line


def _start(
    start: str, sans: list[str], marked: chess.Color | None, known: dict[str, str]
) -> tuple[chess.Board, str]:
    """The position to replay sans from, and the basis of its side to move: given by a full FEN;
    from a placement alone, the side the line's first move number marks (notation), else the
    only side that can play the whole line in a position that can occur (deduced), else White
    (assumed), when both sides can or neither can."""
    if start == "start":
        return chess.Board(), "given"
    board = chess.Board(start)
    if len(start.split()) > 1:
        return board, "given"
    if marked is not None:
        board.turn = marked
        return board, "notation"
    sides = [side for side in (chess.WHITE, chess.BLACK) if _plays(board, side, sans, known)]
    if len(sides) == 1:
        board.turn = sides[0]
        return board, "deduced"
    return board, "assumed"


def _plays(board: chess.Board, side: chess.Color, sans: list[str], known: dict[str, str]) -> bool:
    board = board.copy()
    board.turn = side
    if not board.is_valid():
        return False
    return any(played == len(sans) for played, _ in _leaves(board, sans, known))


def _side(board: chess.Board) -> str:
    return "w" if board.turn == chess.WHITE else "b"


def read_figurines(figurines: str | None, besides: str | None = None) -> dict[str, str]:
    """The piece each figurine was read as in the saved games, from the file figurines, leaving
    out the game named besides and any figurine two games read as different pieces."""
    if not (figurines and Path(figurines).exists()):
        return {}
    read: dict[str, set[str]] = {}
    for name, pieces in json.loads(Path(figurines).read_text(encoding="utf-8")).items():
        if name == besides:
            continue
        for picture, piece in pieces.items():
            read.setdefault(picture, set()).add(piece)
    return {picture: next(iter(pieces)) for picture, pieces in read.items() if len(pieces) == 1}


def _record_figurines(figurines: str, name: str, pieces: dict[str, str]) -> None:
    """Record the piece each figurine was read as in the game saved as name, replacing what that
    game read before."""
    file = Path(figurines)
    games = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
    if not pieces and name not in games:
        return
    games[name] = pieces
    replace_json(file, games)


def _given_pictures(text: str) -> str:
    """The text with each picture marked as [id], and one that begins a move joined to the rest
    of the move."""
    return _SPACED.sub(r"\1", _PICTURE.sub(r"[\1]", text))


def _pictured(text: str, pieces: dict[str, str]) -> str:
    """The text with each picture that pieces reads written as the piece's letter."""
    return _PICTURE.sub(lambda picture: pieces.get(picture[1], picture[0]), text)


def _readings(
    lines: list[tuple[chess.Board, list[str]]], known: dict[str, str]
) -> tuple[list[dict[str, str]], list[int | None]]:
    """The ways to read the pictures in the lines as pieces, extending known, one piece to a
    picture: the lines in turn, each up to its last picture, played legally as far as any way
    left can play it; and, for each line that stops being legal under every way left, where it
    stops. The moves after a line's last picture decide nothing, since a mistake there could
    settle a picture wrongly. "12 [K]h2 f5 f4", written for 12.Kh2 f5 13.Kg3 f4, read on to f4
    as 13...f4 would take the king for a knight that leaves f3."""
    readings: list[dict[str, str]] = [known]
    reaches: list[int | None] = []
    for board, sans in lines:
        pictured = [index for index, san in enumerate(sans) if _PICTURE.search(san)]
        if not pictured:
            reaches.append(None)
            continue
        found = [
            leaf
            for reading in readings
            for leaf in _leaves(board, sans[: pictured[-1] + 1], reading)
        ]
        furthest = max(played for played, _ in found)
        readings = [reading for played, reading in found if played == furthest]
        reach = max(played for reading in readings for played, _ in _leaves(board, sans, reading))
        reaches.append(reach if reach < len(sans) else None)
    return readings, reaches


def _leaves(
    board: chess.Board, sans: list[str], reading: dict[str, str]
) -> Iterator[tuple[int, dict[str, str]]]:
    """Each way to read the pictures in sans that extends reading, with how many of the moves it
    plays legally from board."""
    stack = [(board.copy(stack=False), 0, reading)]
    while stack:
        board, played, reading = stack.pop()
        while played < len(sans):
            san = sans[played]
            new = [
                picture
                for picture in dict.fromkeys(_PICTURE.findall(san))
                if picture not in reading
            ]
            moves = []
            for pieces in itertools.product(_PIECES, repeat=len(new)):
                option = {**reading, **dict(zip(new, pieces, strict=True))}
                try:
                    moves.append((_parse(board, _pictured(san, option), marks=False), option))
                except ValueError:
                    continue
            if not moves:
                break
            for move, option in moves[1:]:
                branch = board.copy(stack=False)
                branch.push(move)
                stack.append((branch, played + 1, option))
            move, reading = moves[0]
            board.push(move)
            played += 1
        yield played, reading


class _Reading:
    """The pieces the pictures in a call's moves are read as. A picture is read when every
    remaining way of reading the pictures agrees on its piece. lines, each line's moves and
    their labels, and reaches, how far each line plays, tell where a line stops being legal."""

    def __init__(
        self,
        readings: list[dict[str, str]],
        lines: list[tuple[list[str], list[str]]],
        reaches: list[int | None],
    ) -> None:
        first = readings[0]
        self.pieces = {
            picture: piece
            for picture, piece in first.items()
            if all(reading[picture] == piece for reading in readings)
        }
        self.candidates = {
            picture: sorted({reading[picture] for reading in readings}) for picture in first
        }
        self.stops = [
            (sans, labels, reach)
            for (sans, labels), reach in zip(lines, reaches, strict=True)
            if reach is not None
        ]

    def move(self, san: str, label: str) -> str:
        """san, labelled label in errors, with its pictures written as their pieces, or
        ValueError for a picture not read."""
        read = _pictured(san, self.pieces)
        if unread := _PICTURE.search(read):
            picture, pieces = unread[1], self.candidates.get(unread[1], [])
            if len(pieces) > 1:
                raise ValueError(
                    f"the picture {picture} could be {_either(pieces)} here, and the moves do "
                    f"not settle which; give the piece's letter{self._stop(label)}"
                )
            raise ValueError(f"no piece in place of the picture {picture} makes this move legal")
        return read

    def _stop(self, label: str) -> str:
        """Where a line holding the move labelled label stops being legal, whatever the
        pieces."""
        for sans, labels, reach in self.stops:
            if label in labels[:reach]:
                return (
                    f"; under each, the line stops being legal at {labels[reach]}, "
                    f"{sans[reach]!r}, which may be the move to fix"
                )
        return ""

    def text(self, comment: str) -> str:
        """The comment with each picture that was read written as its piece's letter."""
        return _pictured(_given_pictures(comment), self.pieces)

    def unread(self, comment: str) -> str | None:
        """Why the comment's pictures are not all read, or None when they are."""
        why = [
            f"{picture}, which could be {_either(self.candidates[picture])}"
            if len(self.candidates.get(picture, [])) > 1
            else f"{picture}, which no move reads as a piece"
            for picture in dict.fromkeys(_PICTURE.findall(comment))
            if picture not in self.pieces
        ]
        if not why:
            return None
        return (
            f"the comment gives the picture {'; and '.join(why)}: give each such piece by its "
            "letter, or leave out a picture that is not a piece"
        )

    def given(self, sans: list[str]) -> dict[str, str]:
        """The pictures in sans that were read, with their pieces."""
        return {
            picture: self.pieces[picture]
            for san in sans
            for picture in _PICTURE.findall(san)
            if picture in self.pieces
        }


def _either(pieces: list[str]) -> str:
    return ", ".join(pieces[:-1]) + " or " + pieces[-1]


def _parse(board: chess.Board, san: str, marks: bool = True) -> chess.Move:
    """The move san names, or ValueError. python-chess reads a SAN without its capture, check
    and mate marks and writes its own; a mark the move contradicts is refused here, so that the
    book's moves are not silently corrected. Without marks, as when pictures are read and the
    side to move deduced, the marks are left to the move's own check, so that a wrong mark
    decides neither the piece nor the side to move, and is reported at its move."""
    move = board.parse_san(san)
    if not move:
        raise ValueError(f"{san!r} is a null move")
    if not marks:
        return move
    if "x" in san and not board.is_capture(move):
        raise ValueError(f"{san!r} marks a capture, but the move captures nothing")
    if san.endswith("+") and not board.gives_check(move):
        raise ValueError(f"{san!r} marks check, but the move gives no check")
    if san.endswith("#"):
        after = board.copy(stack=False)
        after.push(move)
        if not after.is_checkmate():
            raise ValueError(f"{san!r} marks mate, but the move does not mate")
    return move


def _unplayable(board: chess.Board, san: str, error: ValueError) -> dict[str, Any]:
    legal = sorted(board.san(move) for move in board.legal_moves)
    return {"move": san, "message": str(error), "legal_moves": legal}


def check_fen(fen: str) -> dict[str, Any]:
    """Check a FEN, or a placement alone, and complete the fields it leaves out.

    Returns ok; fen, normalised and complete; side_to_move, "w" or "b"; status: invalid, normal,
    check, or how the game has ended, such as checkmate or stalemate; problems, why the position
    cannot occur, empty when it can; and assumed, the fields filled with defaults (side_to_move
    w, castling -, en_passant -, halfmove_clock 0, fullmove_number 1).
    """
    assumed = list(_FEN_FIELDS[len(fen.split()) :])
    try:
        board = chess.Board(fen)
    except ValueError as error:
        return {
            "ok": False,
            "fen": None,
            "side_to_move": None,
            "status": "invalid",
            "problems": [str(error)],
            "assumed": assumed,
        }
    problems = status_names(board.status())
    return {
        "ok": not problems,
        "fen": board.fen(),
        "side_to_move": _side(board),
        "status": "invalid" if problems else _status(board),
        "problems": problems,
        "assumed": assumed,
    }


def status_names(status: chess.Status) -> list[str]:
    """The faults python-chess's status flags name, such as "too_many_kings"."""
    return [flag.name.lower() for flag in chess.Status if flag in status]


def _status(board: chess.Board) -> str:
    outcome = board.outcome()
    if outcome is not None:
        return outcome.termination.name.lower()
    return "check" if board.is_check() else "normal"


def render_board(fen: str, png: str | Path) -> None:
    """Draw a position, from a FEN or a placement alone, as a PNG 400 pixels square, White at
    the bottom."""
    svg = chess.svg.board(chess.Board(fen), size=400)
    Path(png).parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open("svg", svg.encode()) as doc:
        doc[0].get_pixmap().save(str(png))


class MoveSpec(TypedDict, total=False):
    """A move in a line: its SAN, the comment after it, its NAGs, and the variations that
    replace it: a list of lines, each a list of moves from the position before it."""

    san: Required[str]
    comment: str
    nags: list[int]
    variations: list[list["MoveSpec"]]


class GameSpec(TypedDict, total=False):
    """A game: its headers, the FEN it starts from if not the initial position, a comment
    before the first move, its main line, and its result."""

    headers: dict[str, str]
    start_fen: str
    comment: str
    moves: list[MoveSpec]
    result: str


def build_pgn(
    spec: GameSpec | str,
    name: str | None = None,
    store: str | None = None,
    figurines: str | None = None,
) -> dict[str, Any]:
    """Build one PGN game from a structured spec, checking every move.

    spec: headers (Event, Round, Annotator, ...); start_fen, which adds SetUp and FEN, a placement
    alone taking its side to move as validate_moves deduces it from the main line; comment,
    before the first move, alone for a text-only game; moves, the main line, each move with its
    san (a suffix annotation such as "d5!" is read as its NAG; a capture, check or mate mark that
    the move contradicts is an error) and optionally comment, the book's commentary after that
    move, nags (1 !, 2 ?, 3 !!, 4 ??, 5 !?, 6 ?!) and variations, the lines that replace this
    move, a list of lines each a list of moves in the same form, whose moves may have variations
    of their own: a line's first move is made instead of this move, by the same side, from the
    position before it, and both sides then move in turn; and result, one of 1-0, 0-1, 1/2-1/2
    and *, else the headers' Result, else *. For example, after 1.e4 e5, the move 2.Nf3 with a
    comment and two variations, 2.f4 exf4 and 2.Bc4: {"san": "Nf3", "comment": "...",
    "variations": [[{"san": "f4"}, {"san": "exf4"}], [{"san": "Bc4"}]]}. Within the first,
    Black's other reply 2...d5 is a variation of 2...exf4: [{"san": "f4"}, {"san": "exf4",
    "variations": [[{"san": "d5"}]]}]. The commentary on one move may give a line for the next:
    an alternative to White's 8th move, given in the commentary on Black's 7th, replaces White's
    8th move, so it goes on that move, not on the move whose commentary gives it. A line that
    would begin after a move the game does not have, or after its last move, replaces no move,
    so it cannot be a variation.

    A piece the book prints as a picture is given by the picture's id as doc_text marks it, in
    place of the piece's letter, in a san or a comment: "[0ebed3]c3", not by a letter worked out
    for it. The book's text does not say which piece a picture is; the tool reads it from the
    moves. A picture stands for one piece throughout the book: the piece under which the moves
    are legal, the main line first, then each variation, each line up to its last picture and as
    far as it is legal. Pieces read in a saved game carry over to calls for other games. A move
    whose picture could be more than one piece, or none, is an error that says so, and so is a
    comment's picture that no move reads; give that piece by its letter.

    name, optional: save the game under this name, for write_pgn to write from its names, instead
    of returning its PGN, so that its PGN need not be repeated. Saving under a name again
    replaces the game saved before; a game with errors is not saved. A name is up to 40 letters,
    digits, dots, dashes and underscores, starting with a letter or digit.

    Returns ok; pgn, the game as built, up to any illegal move; errors, each with the path of the
    move that is not legal, or whose comment gives a picture no move reads ("comment" for the
    game's comment), the move, why, and the legal moves there, and for a variation that is not
    legal, any neighbouring move in place of which it would be legal; and pictures, when the
    moves give any, each picture read with its piece, as far as the moves are legal. A path names
    moves as the game numbers them: from the initial position "1b" is Black's first move and
    "3w/v1/3b" the second move of the first variation on White's third; from a FEN at move 30
    with White to move, the first move is "30w". With a name, instead of pgn: saved, the name, or
    null if the game was not saved; replaced, whether it replaced a game saved before; outline,
    the game's moves with each comment cut to its first five words, to check where each comment
    sits; and plies, variations and comments, as check_pgn counts them.
    """
    built = build_game(spec, read_figurines(figurines, besides=name))
    if name is None:
        return built
    assert store is not None, "a game saved by name needs a store"
    return save_built(built, name, store, figurines)


def save_built(
    built: dict[str, Any], name: str, store: str, figurines: str | None = None
) -> dict[str, Any]:
    """Save a built game under name in the folder store, for write_pgn to write, and say what
    was saved. The pieces its pictures were read as are recorded in the file figurines, for
    calls for other games."""
    if not _GAME_NAME.fullmatch(name):
        raise ValueError(
            f"{name!r} is not a game's name: up to 40 letters, digits, dots, dashes and "
            "underscores, starting with a letter or digit"
        )
    file = Path(store) / f"{name}.pgn"
    replaced = built["ok"] and file.exists()
    if built["ok"]:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(built["pgn"].strip("\n") + "\n", encoding="utf-8")
        if figurines:
            _record_figurines(figurines, name, built.get("pictures", {}))
    counts = check_pgn(built["pgn"])["games"]
    return {
        "ok": built["ok"],
        "saved": name if built["ok"] else None,
        "replaced": replaced,
        "outline": _outline(built["pgn"]),
        **({key: counts[0][key] for key in ("plies", "variations", "comments")} if counts else {}),
        **({"pictures": built["pictures"]} if "pictures" in built else {}),
        "errors": built["errors"],
    }


def _outline(pgn: str) -> str:
    (game,) = read_games(pgn)
    for node in nodes(game):
        node.comment = _first_words(node.comment)
        node.starting_comment = _first_words(node.starting_comment)
    return " ".join(game.accept(_exporter(headers=False)).split())


def _first_words(comment: str) -> str:
    words = comment.split()
    return " ".join(words[:5]) + ("…" if len(words) > 5 else "")


def read_spec(spec: Any) -> Any:
    """A spec given as JSON text, read as the object it holds, or ValueError saying where the text
    breaks; anything else as it is. A model's spec reaches the tool as text when its JSON is
    broken, so the error calls the JSON malformed rather than the type wrong: "spec is text that
    is not JSON" was taken for a wrong type, and the same broken JSON sent again."""
    if not isinstance(spec, str):
        return spec
    try:
        read = json.loads(spec)
    except json.JSONDecodeError as error:
        before, after = spec[max(0, error.pos - 30) : error.pos], spec[error.pos :][:30]
        raise ValueError(
            f"the spec's JSON is malformed: {error}: ...{before} <<here>> {after}..."
        ) from error
    if not isinstance(read, dict):
        raise ValueError("the spec's JSON is not an object")
    return read


def _as_given(comment: Any, path: str) -> str:
    return comment


def build_game(
    spec: Any, known: dict[str, str], comments: Callable[[Any, str], str] = _as_given
) -> dict[str, Any]:
    """Build the game a spec gives, its pictures read as pieces extending known. comments
    turns each comment, as the spec gives it, into text, given the path of its move ("comment"
    for the game's own)."""
    spec = read_spec(spec)
    game = chess.pgn.Game()
    for name, value in spec.get("headers", {}).items():
        game.headers[name] = value
    moves = spec.get("moves", [])
    if spec.get("start_fen"):
        main = [_split_suffix(move["san"])[0] for move in moves]
        board, _ = _start(spec["start_fen"], main, None, known)
        game.setup(board)
    lines = list(_spec_lines(moves, game.board()))
    readings, reaches = _readings([(game.board(), sans) for sans, _ in lines], known)
    reading = _Reading(readings, lines, reaches)
    comment = comments(spec.get("comment", ""), "comment")
    game.comment = reading.text(comment)
    result = spec.get("result") or spec.get("headers", {}).get("Result") or "*"
    if result not in _RESULTS:
        raise ValueError(f"result {result!r} is not one of {_RESULTS_TEXT}")
    game.headers["Result"] = result
    errors: list[dict[str, Any]] = []
    if why := reading.unread(comment):
        errors.append({"path": "comment", "message": why})
    _add_line(game, moves, "", errors, reading, comments=comments)
    pictures = reading.given([san for sans, _ in lines for san in sans])
    return {
        "ok": not errors,
        "pgn": game.accept(_exporter(headers=True)),
        "errors": errors,
        **({"pictures": pictures} if pictures else {}),
    }


def _exporter(headers: bool) -> chess.pgn.StringExporter:
    return chess.pgn.StringExporter(headers=headers, variations=True, comments=True)


def _spec_lines(
    line: list[MoveSpec],
    start: chess.Board,
    before: tuple[tuple[str, ...], tuple[str, ...]] = ((), ()),
    prefix: str = "",
    ply: int = 0,
) -> Iterator[tuple[list[str], list[str]]]:
    """The moves of each line in the spec from the game's start, with the path of each: this line
    first, then the variations on its moves in turn, each before the variations within it."""
    sans = [_split_suffix(move["san"])[0] for move in line]
    paths = [prefix + _move_path(start, ply + index) for index in range(len(line))]
    yield [*before[0], *sans], [*before[1], *paths]
    for index, move in enumerate(line):
        for number, variation in enumerate(move.get("variations", []), start=1):
            prefixes = ((*before[0], *sans[:index]), (*before[1], *paths[:index]))
            yield from _spec_lines(
                variation, start, prefixes, f"{paths[index]}/v{number}/", ply + index
            )


def _move_path(start: chess.Board, ply: int) -> str:
    """The number and side of the move ply plies after start, such as "3w"."""
    half = ply + (start.turn == chess.BLACK)
    return f"{start.fullmove_number + half // 2}{'b' if half % 2 else 'w'}"


def _split_suffix(san: str) -> tuple[str, str | None]:
    """The move with its pictures marked as [id], and its suffix annotation (! ? !! ?? !? ?!),
    if it has one."""
    san = _given_pictures(san)
    suffix = _SUFFIX.search(san)
    return (san[: suffix.start()], suffix[1]) if suffix else (san, None)


def _add_line(
    parent: chess.pgn.GameNode,
    line: list[MoveSpec],
    prefix: str,
    errors: list[dict[str, Any]],
    reading: _Reading,
    nearby: list[tuple[chess.Board, str, str]] | None = None,
    comments: Callable[[Any, str], str] = _as_given,
) -> None:
    """Add the moves of line after parent, each move's variations after it, recording each move
    that is not legal. nearby, for a variation: the moves beside the one it replaces, each as the
    position before it, its path and its san, to name in the error if a move of the variation is
    not legal but the whole variation would be legal in place of one of them."""
    before: tuple[chess.Board, str, str] | None = None
    for index, move_spec in enumerate(line):
        board = parent.board()
        path = f"{prefix}{_move_path(board, 0)}"
        san, suffix = _split_suffix(move_spec["san"])
        nags = list(move_spec.get("nags", []))
        if suffix and _SUFFIX_NAGS[suffix] not in nags:
            nags.append(_SUFFIX_NAGS[suffix])
        try:
            move = _parse(board, reading.move(san, path))
        except ValueError as error:
            unplayable = _unplayable(board, move_spec["san"], error)
            if nearby:
                unplayable["message"] += _elsewhere(line, nearby, reading)
            errors.append({"path": path, **unplayable})
            return
        comment = comments(move_spec.get("comment", ""), path)
        child = parent.add_variation(move, comment=reading.text(comment), nags=nags)
        if why := reading.unread(comment):
            errors.append({"path": path, "move": move_spec["san"], "message": why})
        beside = [before] if before else []
        if index + 1 < len(line):
            after = child.board()
            beside.append((after, f"{prefix}{_move_path(after, 0)}", line[index + 1]["san"]))
        for number, variation in enumerate(move_spec.get("variations", []), start=1):
            _add_line(parent, variation, f"{path}/v{number}/", errors, reading, beside, comments)
        before = (board, path, move_spec["san"])
        parent = child


def _elsewhere(
    line: list[MoveSpec], nearby: list[tuple[chess.Board, str, str]], reading: _Reading
) -> str:
    """What to add to the error of a variation that is not legal: PGN's branching, and each move
    beside the one it replaces in place of which it would be legal throughout. Legality is all
    the tool can check, not whether the book means the line there."""
    fits = [f"{path} {san}" for board, path, san in nearby if _plays_through(board, line, reading)]
    if not fits:
        return ""
    return (
        ". A variation replaces the move it is given on: its first move is made instead of that "
        "move, by the same side, and both sides then move in turn. Given on "
        f"{' or on '.join(fits)} instead, this line would be legal, though legality alone does "
        "not say the book means it there"
    )


def _plays_through(board: chess.Board, line: list[MoveSpec], reading: _Reading) -> bool:
    board = board.copy(stack=False)
    try:
        for move_spec in line:
            san = _split_suffix(move_spec["san"])[0]
            board.push(_parse(board, _pictured(san, reading.pieces)))
    except ValueError:
        return False
    return True


def check_pgn(pgn: str) -> dict[str, Any]:
    """Check PGN text of one or more games: what python-chess reads of it, and what it drops.

    python-chess's reader silently skips what it cannot read: commentary outside braces,
    comments after a semicolon, malformed tags, stray brackets; and a { never closed makes the
    rest of the text a comment.

    Returns ok, true when there is at least one game, no game has errors and nothing was
    dropped; dropped, each piece of text python-chess did not read, with its line; and games, in
    order, each with plies (on the main line), variations, comments, and errors: a FEN tag that
    is not a complete, possible FEN; a missing result, a Result tag that differs from it, or a
    result the final position contradicts, such as 1-0 after Black mates; parse errors and
    illegal moves. After an illegal move python-chess skips the rest of its line, so later
    errors and dropped text may follow from the first.
    """
    games, read = [], []
    for game in read_games(pgn):
        try:
            exported = game.accept(_exporter(headers=True))
        except ValueError:  # a FEN tag no board can be set up from: python-chess read its tags
            exported = "\n".join(f'[{name} "{value}"]' for name, value in game.headers.items())
        read.append(exported)
        every = list(nodes(game))
        games.append(
            {
                "plies": len(list(game.mainline_moves())),
                "variations": sum(len(node.variations) - 1 for node in every if node.variations),
                "comments": sum(bool(node.comment) + bool(node.starting_comment) for node in every),
                "errors": _fen_tag_errors(game)
                + _result_errors(game)
                + [str(error) for error in game.errors],
            }
        )
    dropped = _dropped(pgn, "\n\n".join(read))
    ok = bool(games) and not dropped and not any(game["errors"] for game in games)
    return {"ok": ok, "dropped": dropped, "games": games}


def write_pgn(
    *,
    path: str,
    games: list[str] | None = None,
    names: list[str] | None = None,
    store: str | None = None,
) -> dict[str, Any]:
    """Write games to the PGN file at path, a blank line between them, and check it as check_pgn
    does: either games, one PGN game per item, or names, games saved with build_pgn in the
    folder store, in order. An empty list, an empty game or a name not saved is refused with
    ValueError, and the file is left as it was.

    Returns path; saved, how many games the file now holds; replaced, how many it held before;
    and check_pgn's ok, dropped and games.
    """
    if games and names:
        raise ValueError("give games or names, not both")
    if names:
        assert store is not None, "games given by name need their store"
        where = Path(store)
        for name in names:
            if not (_GAME_NAME.fullmatch(name) and (where / f"{name}.pgn").is_file()):
                saved = ", ".join(sorted(file.stem for file in where.glob("*.pgn"))) or "none"
                raise ValueError(f"no game saved as {name!r}; the games saved are: {saved}")
        games = [(where / f"{name}.pgn").read_text(encoding="utf-8") for name in names]
    games = games or []
    out = Path(path).resolve()
    empty = next((number for number, game in enumerate(games, 1) if not game.strip()), None)
    if not games or empty is not None:
        problem = f"game {empty} is empty" if empty else "no games given"
        raise ValueError(f"{problem}, so nothing was written; {_still_held(out)}")
    before = _held(out)
    text = "\n\n".join(game.strip("\n") for game in games) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    check = check_pgn(text)
    return {"path": str(out), "saved": len(check["games"]), "replaced": before, **check}


def _held(out: Path) -> int:
    """How many games the PGN file holds, as python-chess reads it; none if it does not exist."""
    return len(read_games(out.read_text(encoding="utf-8"))) if out.exists() else 0


def _still_held(out: Path) -> str:
    before = _held(out)
    if before:
        return f"{out.name} still holds the {_games(before)} saved before"
    return "no games have been saved yet"


def _games(count: int) -> str:
    return f"{count} game" if count == 1 else f"{count} games"


class _ReadGame(chess.pgn.Game):
    """A game as python-chess reads it, with the Result tag as written and the result that ends
    the movetext, which python-chess silently reconciles."""

    result_tag: str | None = None
    result_token: str | None = None


class _Reader(chess.pgn.GameBuilder[_ReadGame]):
    """python-chess's reader, keeping what _ReadGame keeps, and the errors it meets in the
    game's errors without logging them."""

    def __init__(self) -> None:
        super().__init__(Game=_ReadGame)

    def visit_header(self, tagname: str, tagvalue: str) -> None:
        if tagname == "Result":
            self.game.result_tag = tagvalue
        super().visit_header(tagname, tagvalue)

    def visit_result(self, result: str) -> None:
        self.game.result_token = result
        super().visit_result(result)

    def handle_error(self, error: Exception) -> None:
        self.game.errors.append(error)


def read_games(text: str) -> list[_ReadGame]:
    """Every game in PGN text, as python-chess reads them; what it could not read is in each
    game's errors."""
    stream, games = io.StringIO(text), []
    while (game := chess.pgn.read_game(stream, Visitor=_Reader)) is not None:
        games.append(game)
    return games


def nodes(game: chess.pgn.GameNode) -> Iterator[chess.pgn.GameNode]:
    """Every node of a game, the game itself first, variations included."""
    stack: list[chess.pgn.GameNode] = [game]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.variations)


def ranges(numbers: list[int]) -> list[tuple[int, int]]:
    """Ascending numbers as runs of consecutive ones, each as its first and last."""
    found: list[tuple[int, int]] = []
    for number in numbers:
        if found and found[-1][1] == number - 1:
            found[-1] = (found[-1][0], number)
        else:
            found.append((number, number))
    return found


def _result_errors(game: _ReadGame) -> list[str]:
    if game.errors:  # python-chess skipped the rest of the line, the result with it
        return []
    if game.result_token is None:
        return [f"the movetext does not end with a result: {_RESULTS_TEXT}"]
    if game.result_tag not in (None, game.result_token):
        return [f"the movetext ends {game.result_token} but the Result tag says {game.result_tag}"]
    try:
        outcome = game.end().board().outcome()
    except ValueError:  # a FEN tag no board can be set up from, reported as such
        return []
    if outcome is not None and game.result_token not in ("*", outcome.result()):
        ended = outcome.termination.name.lower().replace("_", " ")
        return [
            f"the game ends in {ended}, so its result is {outcome.result()}, not "
            f"{game.result_token}"
        ]
    return []


def _dropped(text: str, read: str) -> list[str]:
    """Each run of words in text that python-chess's re-export of it lacks, with its line, and
    what python-chess reads otherwise than written. Tags, moves, move numbers, NAGs and results
    are left out of the comparison: python-chess reads each of those or reports an error, and
    its export may reorder them."""
    masked, misread = _scan(text)
    written = _words(masked)
    matcher = difflib.SequenceMatcher(
        None,
        [word for word, _ in written],
        [word for word, _ in _words(_scan(read)[0])],
        autojunk=False,
    )
    dropped = []
    for operation, first, last, _, _ in matcher.get_opcodes():
        if operation in ("delete", "replace"):
            start, end = written[first][1].start(), written[last - 1][1].end()
            dropped.append(f"line {text.count('\n', 0, start) + 1}: {text[start:end]}")
    return dropped + misread


def _scan(text: str) -> tuple[str, list[str]]:
    """Follow python-chess's reader over the lines of text: the text with the tags it reads as
    tags blanked out, and what it reads otherwise than written: tag lines inside a game's moves,
    which it skips, and a { never closed, which makes the rest of the text a comment."""
    kept, stray = [], []
    in_moves, opened = False, None
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        if opened is None:
            if not line.strip():
                in_moves = False
            elif chess.pgn.TAG_REGEX.match(line):
                if in_moves:
                    stray.append(number)
                kept.append(re.sub(r"[^\n]", " ", line))
                continue
            elif not line.startswith(("%", ";", "[")):
                in_moves = True
        for character in line:
            if opened is None and character == ";":
                break
            if opened is None and character == "{":
                opened = number
            elif opened is not None and character == "}":
                opened = None
        kept.append(line)
    misread = []
    for first, last in ranges(stray):
        if first == last:
            misread.append(
                f"line {first}: a tag inside a game's moves is not read as a tag; leave a blank "
                "line between games"
            )
        else:
            misread.append(
                f"lines {first} to {last}: tags inside a game's moves are not read as tags; "
                "leave a blank line between games"
            )
    if opened is not None:
        misread.append(f"line {opened}: {{ is never closed, so the rest became a comment")
    return "".join(kept), misread


def _words(text: str) -> list[tuple[str, re.Match[str]]]:
    return [
        (match.group(), match)
        for match in _TOKEN.finditer(text)
        if not _read_as_movetext(match.group())
    ]


def _read_as_movetext(token: str) -> bool:
    """Whether python-chess reads the token as a move, move number, NAG or result."""
    if token in _RESULTS or _NAG.match(token):
        return True
    core = _MOVE_NUMBER_PREFIX.sub("", token).rstrip("!?+#")
    if not core or core.isdigit():
        return True
    match = chess.pgn.MOVETEXT_REGEX.fullmatch(core)
    return bool(match and match.group(1))


def _fen_tag_errors(game: chess.pgn.Game) -> list[str]:
    if "FEN" not in game.headers:
        return []
    checked = check_fen(game.headers["FEN"])
    faults = checked["problems"] + [f"no {field}" for field in checked["assumed"]]
    return [f"FEN tag {game.headers['FEN']!r}: {', '.join(faults)}"] if faults else []
