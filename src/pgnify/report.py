"""Checks of a conversion's output, without a model. A verified problem is a mechanical fact that
needs fixing, such as an illegal move or text a PGN reader would drop. A possible issue is an
observation for a person to judge, such as an uncertain square or a diagram never read. The
model's own account, the notes it gave write_pgn, is quoted separately and never verified."""

import json
import shutil
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import chess
import chess.pgn

from pgnify.chess_tools import check_pgn, nodes, ranges, read_games, render_board
from pgnify.outputs import read_notes, read_records
from pgnify.prose import BookProse, bare, book_prose, passages, sentences, shortened

CAVEAT = (
    "A report without verified problems does not mean the conversion is complete or faithful. "
    "Without a reference PGN to compare with, pgnify cannot tell whether every game, exercise, "
    "variation or passage of the book was found; it reports only what it can check."
)
# A passage repeated in one game's comments is reported when it has at least this many words.
MIN_REPEATED_WORDS = 5
# A picture smaller than this on either side, in pixels, is taken for a figurine, not a diagram.
MIN_DIAGRAM_SIDE = 64
# A picture that covers at least this share of its page is taken for a scanned page.
SCANNED_PAGE = 0.9
# The words of a finding's quotation, and the characters of a tool's error, that report.md
# shows.
QUOTED_WORDS = 14
ERROR_SHOWN = 300
INITIAL = "the initial position"


@dataclass
class Finding:
    """A verified problem or a possible issue, of one game or of the whole file."""

    kind: Literal["problem", "issue"]
    what: str
    game: int | None = None


def check(text: str, book: BookProse | None = None) -> dict[str, Any]:
    """Check PGN text of one or more games and, given book, the book's prose from book_prose,
    compare their comments with it. Returns a summary of each game, and the findings."""
    checked = check_pgn(text)
    games = read_games(text)
    findings: list[Finding] = []
    if not games:
        findings.append(Finding("problem", "no game could be read from the file"))
    for index, counted in enumerate(checked["games"], start=1):
        findings += [Finding("problem", error, index) for error in counted["errors"]]
    findings += [
        Finding("problem", f"text a PGN reader drops, at {dropped}")
        for dropped in checked["dropped"]
    ]
    for index, game in enumerate(games, start=1):
        if uncertain := game.headers.get("Uncertain", "").split():
            findings.append(
                Finding(
                    "issue",
                    f"its position marks {' '.join(uncertain)} as uncertain: compare with the "
                    "book's diagram",
                    index,
                )
            )
        if game.headers.get("FEN") and not game.variations:
            findings.append(
                Finding(
                    "issue",
                    "it is a position without moves: if the book gives moves, they may be in "
                    "its comments",
                    index,
                )
            )
        findings += _comment_issues(game, index, book)
    summaries = [
        _summary(game, counted, index)
        for index, (game, counted) in enumerate(zip(games, checked["games"], strict=True), 1)
    ]
    return {"games": summaries, "findings": [asdict(finding) for finding in findings]}


def check_file(pgn: Path, book: Path | None = None) -> dict[str, Any]:
    """check() on a PGN file, against its book when one is given."""
    text = pgn.read_text(encoding="utf-8", errors="replace")
    result = check(text, book_prose(book) if book else None)
    return {"file": str(pgn), "book": str(book) if book else None, **result, "caveat": CAVEAT}


def report(folder: Path, images: bool = False) -> dict[str, Any]:
    """Report on an output folder of `pgnify serve`: check each PGN file against its book, then,
    for each book, what the server's log shows across every session and every PGN file written
    from it in the folder, and quote the model's account separately. Writes report.md and
    report.json into the folder, and with images saves each doubtful game's diagram beside a
    drawing of its position in report-images/."""
    folder = folder.resolve()
    work = folder / ".pgnify"
    records = read_records(work)
    log = _log(work / "log.jsonl")
    prose_of: dict[Path, BookProse] = {}
    outputs = []
    # By book key: the book's name, and the PGN files written from it with their games.
    names: dict[str, str] = {}
    written: dict[str, list[tuple[str, Sequence[chess.pgn.Game], list[dict[str, Any]]]]] = {}
    for pgn in sorted(path for path in folder.iterdir() if path.suffix.lower() == ".pgn"):
        record = records.get(pgn.name) or next(
            (kept for name, kept in records.items() if name.casefold() == pgn.name.casefold()), {}
        )
        book = Path(record["book_path"]) if record.get("book_path") else None
        book = book if book and book.is_file() else None
        if book and book not in prose_of:
            prose_of[book] = book_prose(book)
        text = pgn.read_text(encoding="utf-8", errors="replace")
        result = check(text, prose_of[book] if book else None)
        key = record.get("book_key")
        entries = [entry for entry in log if _of_book(entry, key)] if key else []
        result["findings"] += [asdict(f) for f in _doubtful_starts(entries, result["games"])]
        if key:
            names[key] = record.get("book") or key
            written.setdefault(key, []).append((pgn.name, read_games(text), result["games"]))
        outputs.append(
            {
                "file": pgn.name,
                "book": record.get("book"),
                "book_found": book is not None,
                **result,
                "images": _images(folder, pgn, entries, result["games"]) if images else [],
                "model_account": read_notes(pgn),
            }
        )
    books = []
    for key, files in written.items():
        entries = [entry for entry in log if _of_book(entry, key)]
        games = [game for _, read, _ in files for game in read]
        summaries = [summary for _, _, counted in files for summary in counted]
        books.append(
            {
                "book": names[key],
                "files": [name for name, _, _ in files],
                "read": _read_summary(entries),
                "findings": [
                    asdict(finding)
                    for finding in _log_issues(entries, summaries, _positions(games))
                ],
            }
        )
    elsewhere = [
        entry for entry in log if "error" in entry and not any(_of_book(entry, k) for k in written)
    ]
    reported = {
        "folder": str(folder),
        "caveat": CAVEAT,
        "outputs": outputs,
        "books": books,
        "findings": [asdict(finding) for finding in _errors(elsewhere)],
    }
    text = json.dumps(reported, indent=1, ensure_ascii=False) + "\n"
    (folder / "report.json").write_text(text, encoding="utf-8")
    (folder / "report.md").write_text(markdown(reported), encoding="utf-8")
    return reported


def markdown(reported: dict[str, Any]) -> str:
    lines = [f"# pgnify report: {reported['folder']}", "", reported["caveat"], ""]
    if not reported["outputs"]:
        lines += ["No PGN file in the folder.", ""]
    for output in reported["outputs"]:
        lines += [f"## {_inline(output['file'])}", ""]
        if output["book"]:
            moved = "" if output["book_found"] else ", which is no longer where it was"
            lines += [
                f"From the book {_inline(output['book'])}{moved}. What the server's log shows "
                "for the book is under its own heading.",
                "",
            ]
        lines += ["| Game | Label | Start | Plies | Variations | Comments | Uncertain |"]
        lines += ["|---|---|---|---|---|---|---|"]
        for game in output["games"]:
            lines.append(
                f"| {game['game']} | {_cell(game['label'])} | {_cell(game['start'])} | "
                f"{game['plies']} | {game['variations']} | {game['comments']} | "
                f"{_cell(' '.join(game['uncertain']))} |"
            )
        lines.append("")
        for kind, heading in (
            ("problem", "Verified problems"),
            ("issue", "Possible issues, for a person to judge"),
        ):
            found = [finding for finding in output["findings"] if finding["kind"] == kind]
            lines += [f"### {heading}", ""]
            lines += [_line(finding) for finding in found] or ["None found."]
            lines.append("")
        if output["images"]:
            lines += ["### Diagrams beside their positions", ""]
            lines += ["| Game | The book's diagram | The game's position |", "|---|---|---|"]
            for shown in output["images"]:
                diagram = f"![]({shown['book']})" if shown["book"] else "not found"
                lines.append(f"| {shown['game']} | {diagram} | ![]({shown['position']}) |")
            lines.append("")
        lines += ["### The model's own account, not verified", ""]
        if output["model_account"]:
            lines += [f"> {line}" if line else ">" for line in output["model_account"].splitlines()]
        else:
            lines.append(
                "The model gave no notes. That says nothing either way about whether the "
                "conversion is complete."
            )
        lines.append("")
    for book in reported["books"]:
        files = ", ".join(_inline(name) for name in book["files"])
        lines += [
            f"## {_inline(book['book'])}: what the server's log shows",
            "",
            "From every session's calls on this book in this folder. The diagrams read from it "
            f"are matched against the games of all its PGN files here, {files}: a diagram that "
            "a game reaches shows that one of these files has its position, not that any one "
            "conversion is complete.",
            "",
        ]
        lines += [f"- {name}: {_inline(value)}" for name, value in book["read"].items()] or [
            "The server's log holds no call on this book."
        ]
        lines += ["", "### Possible issues in the log, for a person to judge", ""]
        lines += [_line(finding) for finding in book["findings"]] or ["None found."]
        lines.append("")
    if reported["findings"]:
        lines += ["## Tool errors outside these PGN files", ""]
        lines += [_line(finding) for finding in reported["findings"]]
        lines.append("")
    return "\n".join(lines)


def as_text(checked: dict[str, Any]) -> str:
    """check_file's result as plain text, for `pgnify check`."""
    lines = [f"{checked['file']}: {counted(len(checked['games']), 'game')}"]
    for kind, heading in (("problem", "Verified problems"), ("issue", "Possible issues")):
        found = [finding for finding in checked["findings"] if finding["kind"] == kind]
        lines += ["", f"{heading}:", *([_line(finding) for finding in found] or ["- none found"])]
    return "\n".join([*lines, "", checked["caveat"]])


def counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _line(finding: dict[str, Any]) -> str:
    where = f"Game {finding['game']}: " if finding["game"] else ""
    return f"- {where}{_inline(finding['what'])}"


def _inline(text: object) -> str:
    """Text collapsed onto one line, so that text from a PGN file, the log or a file name cannot
    start a heading or a quotation in report.md."""
    return " ".join(str(text).split())


def _cell(text: object) -> str:
    return _inline(text).replace("|", "\\|")


def _summary(game: chess.pgn.Game, counts: dict[str, Any], index: int) -> dict[str, Any]:
    headers = game.headers
    label = " ".join(
        part for part in (headers.get("Event", "?"), headers.get("Round", "?")) if part != "?"
    )
    return {
        "game": index,
        "label": label or "?",
        "start": _start(headers),
        "plies": counts["plies"],
        "variations": counts["variations"],
        "comments": counts["comments"],
        "uncertain": headers.get("Uncertain", "").split(),
    }


def _start(headers: chess.pgn.Headers) -> str:
    """The placement a game starts from, or "?" for a blank FEN tag."""
    if "FEN" not in headers:
        return INITIAL
    fen = headers["FEN"].split()
    return fen[0] if fen else "?"


def _readable(placement: str) -> bool:
    """Whether python-chess can set up a board from the placement."""
    try:
        chess.BaseBoard(placement)
    except ValueError:
        return False
    return True


def _positions(games: Sequence[chess.pgn.Game]) -> set[str]:
    """The placement of every position the games start from or reach, variations included. A
    game whose FEN tag is no position, a verified problem already, reaches none."""
    positions = set()
    for game in games:
        try:
            game.board()
        except ValueError:
            continue
        positions |= {node.board().board_fen() for node in nodes(game)}
    return positions


def _comment_issues(game: chess.pgn.Game, index: int, book: BookProse | None) -> list[Finding]:
    """Comment text that no reading of the book's prose holds word for word, and passages a game
    gives more often than the book does."""
    findings = []
    seen: Counter[str] = Counter()
    for node in nodes(game):
        for comment in (node.starting_comment, node.comment):
            for passage in passages(comment):
                for sentence in sentences(passage):
                    plain = bare(sentence)
                    if len(plain.split()) < 2:  # a label such as "HINT" is no passage
                        continue
                    seen[plain] += 1
                    if book is not None and not book.holds(plain):
                        quoted = shortened(sentence, QUOTED_WORDS)
                        findings.append(
                            Finding(
                                "issue",
                                f"comment text not word for word in the book: «{quoted}»",
                                index,
                            )
                        )
    for plain, count in seen.items():
        repeated = count > 1 and len(plain.split()) >= MIN_REPEATED_WORDS
        if repeated and (book is None or book.count(plain) < count):
            quoted = shortened(plain, QUOTED_WORDS)
            findings.append(
                Finding(
                    "issue", f"a passage given {count} times in its comments: «{quoted}»", index
                )
            )
    return findings


def _log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    # Lines end only at "\n": the log keeps other line separators as they are, inside its JSON.
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _doubtful_starts(entries: list[dict[str, Any]], games: list[dict[str, Any]]) -> list[Finding]:
    """The diagrams read from the book with doubts or problems that one of games, one PGN
    file's, starts from, each given with its game."""
    findings = []
    for entry in _readings(entries):
        result = entry["result"]
        said = _said(result) if result["ok"] else ""
        if said and (game := _starting_from(result, games)):
            where = _picture(entry["arguments"]["image_path"])
            findings.append(Finding("issue", f"{where} was read with {said}", game))
    return findings


def _log_issues(
    entries: list[dict[str, Any]], games: list[dict[str, Any]], positions: set[str]
) -> list[Finding]:
    """What the server's log shows a person should look at, for one book. games are the games
    of every PGN file written from it, and positions the placements they start from or reach,
    in their moves or their variations."""
    findings = []
    readings = _readings(entries)
    read = {_picture(entry["arguments"]["image_path"]) for entry in readings}
    for entry in readings:
        result, where = entry["result"], _picture(entry["arguments"]["image_path"])
        if not result["ok"]:
            findings.append(
                Finding("issue", f"the diagram reader read no board in {where}: {result['reason']}")
            )
            continue
        if said := _said(result):
            if _starting_from(result, games) is None:
                findings.append(
                    Finding(
                        "issue",
                        f"{where} was read with {said}; no game in this book's PGN files starts "
                        "from its reading",
                    )
                )
        elif result["placement"] not in positions:
            findings.append(
                Finding(
                    "issue",
                    f"{where} was read as {result['placement']}, a placement no game in this "
                    "book's PGN files starts from or reaches: a game may be missing, or a game "
                    "corrects the reading",
                )
            )
    for entry in entries:
        if entry["tool"] != "doc_images" or "result" not in entry:
            continue
        result = entry["result"]
        page_width, page_height = result["page_size"]
        for image in result["images"]:
            if _picture(image["path"]) in read:
                continue
            if min(image["width"], image["height"]) < MIN_DIAGRAM_SIDE:
                continue
            x0, y0, x1, y1 = image["box"]
            if (x1 - x0) * (y1 - y0) >= SCANNED_PAGE * page_width * page_height:
                continue
            findings.append(
                Finding(
                    "issue",
                    f"page {result['page']}'s picture {_picture(image['path'])}, {image['width']} "
                    f"by {image['height']} pixels, was extracted but never read: a diagram left "
                    "out, or not a diagram",
                )
            )
    saved: dict[str, bool] = {}
    for entry in entries:
        if entry["tool"] == "build_pgn" and entry["arguments"]["name"]:
            name = entry["arguments"]["name"]
            saved[name] = saved.get(name, False) or bool(entry.get("result", {}).get("saved"))
    for name, was_saved in saved.items():
        if not was_saved:
            findings.append(
                Finding(
                    "issue",
                    f"a game was built as {name!r} and never saved, as each build had errors",
                )
            )
    return findings + _errors(entries)


def _readings(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The log's entries of diagrams the reader answered for, a board or a reason why none."""
    return [entry for entry in entries if entry["tool"] == "read_diagram" and "result" in entry]


def _said(result: dict[str, Any]) -> str:
    """A reading's doubts and problems, in words, or "" when it has neither."""
    said = []
    if result["doubts"]:
        said.append("doubts on " + ", ".join(_doubt(doubt) for doubt in result["doubts"]))
    if result["problems"]:
        said.append("problems: " + ", ".join(result["problems"]))
    return "; ".join(said)


def _errors(entries: list[dict[str, Any]]) -> list[Finding]:
    """The tools' errors among the log's entries, each once, with how many times it came."""
    errors = Counter(f"{entry['tool']}: {entry['error']}" for entry in entries if "error" in entry)
    return [
        Finding(
            "issue", f"tool error{f', {count} times' if count > 1 else ''}: {error[:ERROR_SHOWN]}"
        )
        for error, count in errors.items()
    ]


def _doubt(doubt: dict[str, Any]) -> str:
    piece, second = doubt["piece"] or "empty", doubt["second"] or "empty"
    return f"{doubt['square']} ({piece} at {doubt['confidence']}, else {second})"


def _picture(path: str) -> str:
    """A picture's path from books/ onward, so that the paths doc_images returned and the paths
    read_diagram was given compare equal."""
    return path[path.index("books/") :] if "books/" in path else path


def _starting_from(reading: dict[str, Any], games: list[dict[str, Any]]) -> int | None:
    """The game whose position comes from a diagram's reading, if any."""
    fitting = [game["game"] for game in games if game["start"] == reading["placement"]]
    fitting += [game["game"] for game in games if _fits(game, reading)]
    return fitting[0] if fitting else None


def _fits(game: dict[str, Any], reading: dict[str, Any]) -> bool:
    """Whether a game starts from the reading's placement but for squares the reading doubts or
    the game marks uncertain, as the conversion instructions leave a doubtful square empty."""
    if game["start"] == INITIAL or not _readable(game["start"]):
        return False
    allowed = set(game["uncertain"]) | {doubt["square"] for doubt in reading["doubts"]}
    start, read = chess.BaseBoard(game["start"]), chess.BaseBoard(reading["placement"])
    differing = {
        chess.square_name(square)
        for square in chess.SQUARES
        if start.piece_at(square) != read.piece_at(square)
    }
    return differing <= allowed


def _read_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        return {}
    answered = [entry for entry in entries if "result" in entry]
    pages = sorted(
        {
            page
            for entry in answered
            if entry["tool"] == "doc_text"
            for page in entry["result"]["pages"]
        }
    )
    return {
        "pages read": _pages(pages) or "none",
        "searches": sum(entry["tool"] == "doc_search" for entry in answered),
        "diagrams read": sum(entry["tool"] == "read_diagram" for entry in answered),
        "games saved": len(
            {
                entry["arguments"]["name"]
                for entry in answered
                if entry["tool"] == "build_pgn" and entry["result"].get("saved")
            }
        ),
        "tool calls": len(entries),
    }


def _pages(pages: list[int]) -> str:
    return ", ".join(
        str(first) if first == last else f"{first}-{last}" for first, last in ranges(pages)
    )


def _images(
    folder: Path, pgn: Path, entries: list[dict[str, Any]], games: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """For each game whose position is marked uncertain or was read with doubts, the picture its
    position was read from, beside a drawing of the position, saved in report-images/."""
    readings = [
        entry["result"] | {"image_path": entry["arguments"]["image_path"]}
        for entry in entries
        if entry["tool"] == "read_diagram" and entry.get("result", {}).get("ok")
    ]
    shown = []
    target = folder / "report-images" / pgn.stem
    for game in games:
        if game["start"] == INITIAL or not _readable(game["start"]):
            continue
        source = next(
            (reading for reading in readings if reading["placement"] == game["start"]), None
        ) or next((reading for reading in readings if _fits(game, reading)), None)
        doubtful = source is not None and bool(source["doubts"] or source["problems"])
        if not (game["uncertain"] or doubtful):
            continue
        target.mkdir(parents=True, exist_ok=True)
        position = target / f"game-{game['game']}-position.png"
        render_board(game["start"], position)
        drawn: dict[str, Any] = {
            "game": game["game"],
            "position": position.relative_to(folder).as_posix(),
            "book": None,
        }
        picture = (folder / source["image_path"]) if source else None
        if picture and picture.is_file():
            copied = target / f"game-{game['game']}-book{picture.suffix}"
            shutil.copyfile(picture, copied)
            drawn["book"] = copied.relative_to(folder).as_posix()
        shown.append(drawn)
    return shown


def _of_book(entry: dict[str, Any], key: str) -> bool:
    """Whether a log entry concerns the book whose folder is key: by the book it was logged
    with, or, for read_diagram, by the book folder its picture is in."""
    if entry["tool"] == "read_diagram":
        return f"/books/{key}/" in f"/{entry['arguments']['image_path']}"
    return entry.get("book", {}).get("key") == key
