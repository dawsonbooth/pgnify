"""Score a whole-book run against its slice's inventory (experiments/slices.toml), from the run's
PGN alone, and list what a review should look at. The inventory is built by hand, apart from any
session, and lists the book's boards (each numbered diagram and test, with the diagram reader's
placement, checked by eye), each test's solution, and its named games, those it gives whole
marked so.

- Boards: each is found once, found more than once, or missing: a game of the run's PGN files
  starts from its placement, or reaches it on any line. A test is solved when its game has moves
  and holds the solution's first move, where the inventory gives one. A board without kings,
  which no legal FEN can hold, is listed with how the run kept it.
- Games: each named game is found when a game's White and Black give its players; one the book
  gives whole should start from the initial position, or the odds position it gives.
- The run's games that start from no board of the inventory and name no game of it, and the
  games that share a start and a main line, listed for review.
- Commentary, page by page: the share of each page's sentences of five words or more that the
  run's comments hold word for word. A mechanical match: it shows where words are missing, not
  that the words found are at their place.
- Overwritten or lost work, from the session's logs: names that two builds saved different
  games under, files written again without games written to them before, and games built but
  written nowhere (the run's unwritten.pgn).
- Running: the session's account, as score_slice.py gives it, each agent's and the advisor's
  share apart, and each agent's context request by request.
- A sample for the review by hand: in each chapter, a test with its solution, a board's fragment
  and a whole game where there is one.

None of this shows that a game's moves, variations or words are right: only the review does, and
only for the games it looks at. Writes book-score.json and book-review.md into the run's folder
and prints a summary.

    uv run python experiments/score_book.py SLICE RUN_DIR
"""

import argparse
import itertools
import json
import random
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import chess
import chess.pgn
import pymupdf
import score_slice
import session

from pgnify.chess_tools import nodes, read_games
from pgnify.doc_tools import page_text
from pgnify.prose import prose, sentences

ROOT = Path(__file__).resolve().parents[1]
INITIAL = chess.STARTING_BOARD_FEN
# The fewest words a book's sentence has for the commentary check, and the share of a page's
# sentences under which the page is listed for review.
SENTENCE_WORDS = 5
# A picture's mark and its caption, "DIAGRAM 5. Black to play" or "TEST 3.", which the book's
# text sets inside a sentence where a diagram interrupts it.
CAPTIONS = re.compile(r"(?m)^\[picture \d+: [0-9a-f]{6}\]\n|^(?:DIAGRAM|TEST) \d+\.[^\n]*\n")
LOW_SHARE = 0.5
SAMPLE_SEED = 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("slice")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    spec = session.slices()["slices"][args.slice]
    inventory = json.loads((ROOT / spec["inventory"]).read_text())
    book = session.book(spec["source"])
    run = args.run_dir.resolve()
    score, review = score_book(run, inventory, book)
    (run / "book-score.json").write_text(json.dumps(score, indent=1, ensure_ascii=False))
    (run / "book-review.md").write_text(review)
    print(
        json.dumps({key: value for key, value in score.items() if key not in ("running", "pages")})
    )
    print(f"Wrote {run / 'book-score.json'} and {run / 'book-review.md'}")


def score_book(run: Path, inventory: dict[str, Any], book: Path) -> tuple[dict, str]:
    """The run's score against the inventory, and the page for its review."""
    games = _games(run / "work")
    unwritten = _read(run / "unwritten.pgn") if (run / "unwritten.pgn").exists() else []
    lines: list[str] = [f"# Book review: {run.name}", ""]
    counts: Counter[str] = Counter()
    boards = _boards(inventory, games, counts, lines)
    named = _named(inventory, games, counts, lines)
    _prose_lines(inventory, games, counts, lines)
    _unmatched(inventory, games, boards, named, counts, lines)
    coverage = _coverage(book, games, counts, lines)
    _overwrites(run, games, unwritten, counts, lines)
    running = score_slice._session(run)
    answer = running.pop("final_answer")
    lines += ["## Running", "", *score_slice.running_lines(running), ""]
    _contexts(run, lines)
    _sample(inventory, games, boards, named, lines)
    report = _report(run)
    lines += ["## pgnify report", "", "```", report, "```", "", "## Final answer", ""]
    lines += [answer or "(none)", ""]
    score = {**dict(counts), "pages": coverage, "running": running}
    return score, "\n".join(lines)


class Found:
    """A game of the run: its file, its number in the file, and the game."""

    def __init__(self, file: str, number: int, game: chess.pgn.Game):
        self.file, self.number, self.game = file, number, game
        self.start = game.board().board_fen()
        self.placements = {node.board().board_fen() for node in nodes(game)}
        self.main = [move.uci() for move in game.mainline_moves()]

    def __str__(self) -> str:
        headers = self.game.headers
        label = " ".join(
            value
            for value in (headers.get("Round"), headers.get("White"), headers.get("Black"))
            if value and value != "?"
        )
        return f"{self.file} #{self.number}" + (f" ({label})" if label else "")


def _read(path: Path) -> list[chess.pgn.Game]:
    return list(read_games(path.read_text(encoding="utf-8")))


def _games(work: Path) -> list[Found]:
    return [
        Found(pgn.name, number, game)
        for pgn in sorted(work.glob("*.pgn"))
        for number, game in enumerate(_read(pgn), start=1)
    ]


def _boards(inventory, games: list[Found], counts, lines) -> dict[str, list[Found]]:
    """Each board of the inventory with the games that start from it, or else reach it."""
    found: dict[str, list[Found]] = {}
    lines += ["## Boards", ""]
    for chapter in inventory["chapters"]:
        boards = [b for b in inventory["boards"] if b["chapter"] == chapter["title"]]
        if not boards:
            continue
        lines += [f"### {chapter['title']}", ""]
        for board in boards:
            starting = [g for g in games if g.start == board["placement"]]
            reaching = [
                g for g in games if board["placement"] in g.placements and g not in starting
            ]
            matched = starting or reaching
            found[board["id"]] = matched
            counts["boards"] += 1
            counts[f"{board['kind']}s"] += 1
            if not matched:
                counts["boards_missing"] += 1
                lines.append(f"- {board['id']} p{board['page']}: MISSING")
                continue
            counts["boards_found"] += 1
            if len(starting) > 1:
                counts["boards_started_more_than_once"] += 1
            how = "starts" if starting else "reached in"
            notes = _board_notes(board, starting)
            if notes:
                counts["boards_with_notes"] += 1
            shown = ", ".join(str(g) for g in matched[:3]) + (" …" if len(matched) > 3 else "")
            lines.append(
                f"- {board['id']} p{board['page']}: {how} {shown}"
                + (f"; {'; '.join(notes)}" if notes else "")
            )
            if board["kind"] == "test" and starting:
                counts["tests_found"] += 1
                counts["tests_solved"] += not any("solution" in note for note in notes)
        lines.append("")
    for board in (b for b in inventory["boards"] if b["kingless"]):
        kept = _kingless(board, games)
        counts["kingless"] += 1
        lines.append(f"- Board without kings, {board['id']} p{board['page']}: {kept}")
    lines.append("")
    return found


def _board_notes(board: dict[str, Any], starting: list[Found]) -> list[str]:
    notes = []
    if len(starting) > 1:
        notes.append(f"{len(starting)} games start from it")
    side = {"w": chess.WHITE, "b": chess.BLACK}.get(board.get("to_play") or "")
    for game in starting:
        if side is not None and game.game.board().turn != side and game.main:
            notes.append(f"{game} has the other side to move")
    if board["kind"] == "test" and starting:
        firsts = board.get("solution_first_moves") or []
        if not any(g.main for g in starting):
            notes.append("solution: no moves")
        elif firsts and not any(_begins(g, firsts) for g in starting):
            notes.append(f"solution: no line begins with {' or '.join(firsts)}")
    return notes


def _begins(found: Found, firsts: list[str]) -> bool:
    """Whether a line of the game, main or variation, begins with one of the moves."""
    board = found.game.board()
    moves = set()
    for san in firsts:
        try:
            moves.add(board.parse_san(san))
        except ValueError:
            continue
    return any(child.move in moves for child in found.game.variations)


def _kingless(board: dict[str, Any], games: list[Found]) -> str:
    """How the run kept a board without kings: as a game's FEN, in a comment, or not at all."""
    as_start = [g for g in games if g.start == board["placement"]]
    if as_start:
        return f"as the FEN of {', '.join(str(g) for g in as_start)}"
    texts = [g for g in games if any(board["placement"] in node.comment for node in nodes(g.game))]
    if texts:
        return f"its placement in a comment of {', '.join(str(g) for g in texts)}"
    return "not found as a FEN or a placement in a comment (it may be described in words)"


def _named(inventory, games: list[Found], counts, lines) -> dict[str, list[Found]]:
    """Each named game with the games whose players match it and that hold one of its boards,
    or its start when the book gives it from the first move, so that two games of the same
    players stay apart."""
    found: dict[str, list[Found]] = {}
    placements = {board["id"]: board["placement"] for board in inventory["boards"]}
    lines += ["## Named games", ""]
    for named in inventory["games"]:
        players = [g for g in games if _same_players(g.game.headers, named)]
        # Its boards tell two games of the same players apart; its start does only for a game
        # without boards, since every game from the first move shares it.
        wanted = {placements[board] for board in named["boards"]}
        if named["from_move_one"] and not wanted:
            wanted.add(named["start"])
        matched = [g for g in players if wanted & g.placements] if wanted else players
        found[named["id"]] = matched
        counts["games"] += 1
        counts["games_from_move_one"] += named["from_move_one"]
        label = f"{named['id']} {named['white']} - {named['black']} p{named['page']}"
        if not matched:
            counts["games_missing"] += 1
            note = (
                f"; its players' games hold none of its boards: {', '.join(map(str, players[:3]))}"
                if players
                else ""
            )
            lines.append(f"- {label}: MISSING{note}")
            continue
        counts["games_found"] += 1
        notes = []
        if named["from_move_one"]:
            whole = [g for g in matched if g.start == named["start"]]
            counts["games_from_move_one_found"] += bool(whole)
            if not whole:
                notes.append("the book gives it from the first move, but its game starts later")
        shown = ", ".join(str(g) for g in matched[:4]) + (" …" if len(matched) > 4 else "")
        lines.append(f"- {label}: {shown}" + (f"; {'; '.join(notes)}" if notes else ""))
    lines.append("")
    return found


def _prose_lines(inventory, games: list[Found], counts, lines) -> None:
    """Each line the book's prose gives from the initial position: whether a game reaches the
    position it ends on. A line kept in a comment alone is not found this way."""
    lines += ["## Lines in the prose", ""]
    for line in inventory.get("lines", []):
        counts["lines"] += 1
        reaching = [g for g in games if line["placement"] in g.placements]
        counts["lines_reached"] += bool(reaching)
        shown = ", ".join(str(g) for g in reaching[:3]) if reaching else "no game reaches its end"
        lines.append(f"- {line['id']} p{line['page']} {line['moves'][:60]}: {shown}")
    lines.append("")


def _same_players(headers: chess.pgn.Headers, named: dict[str, Any]) -> bool:
    return _surname(named["white"]) in _plain(headers.get("White", "")) and _surname(
        named["black"]
    ) in _plain(headers.get("Black", ""))


def _surname(name: str) -> str:
    return _plain(name).split()[-1] if _plain(name) else ""


def _plain(text: str) -> str:
    """Lower case, without accents or punctuation."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]+", " ", text.lower()).strip()


def _unmatched(inventory, games, boards, named, counts, lines) -> None:
    """The run's games that no board, named game or prose line of the inventory matches, and
    the games that repeat another's start and main line."""
    matched = {id(g) for found in (*boards.values(), *named.values()) for g in found}
    matched |= {
        id(g)
        for line in inventory.get("lines", [])
        for g in games
        if line["placement"] in g.placements and g.start == INITIAL
    }
    others = [g for g in games if id(g) not in matched]
    counts["run_games"] = len(games)
    counts["run_games_unmatched"] = len(others)
    lines += ["## The run's other games", ""]
    for game in others:
        moves = " ".join(node.san() for node in list(game.game.mainline())[:6])
        words = " ".join(prose(game.game.comment).split()[:10])
        lines.append(f"- {game}: {game.start}, {moves or 'no moves'}; «{words}»")
    repeated = defaultdict(list)
    for game in games:
        if game.main:
            repeated[(game.start, tuple(game.main))].append(game)
    twins = [found for found in repeated.values() if len(found) > 1]
    counts["games_repeated"] = sum(len(found) - 1 for found in twins)
    for found in twins:
        lines.append(f"- Same start and main line: {', '.join(str(g) for g in found)}")
    lines.append("")


def _coverage(book: Path, games: list[Found], counts, lines) -> list[dict[str, Any]]:
    """Page by page, the share of the book's sentences of SENTENCE_WORDS words or more that the
    run's comments hold word for word, each sentence counted on the page it starts on. The
    book's text is read as one, without its pictures' marks and captions, which may cut a
    sentence, and with a word its lines or pages break at a hyphen joined again ("e6-" and
    "pawn" as "e6-pawn")."""
    comments = (
        " "
        + " ".join(
            prose(text)
            for g in games
            for node in nodes(g.game)
            for text in (node.comment, getattr(node, "starting_comment", ""))
            if text
        )
        + " "
    )
    with pymupdf.open(book) as doc:
        texts = [CAPTIONS.sub("", page_text(page)) for page in doc]
    whole = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "-", "\n".join(texts))
    plain = [prose(text) for text in texts]
    pages = [{"page": number, "sentences": 0, "held": 0} for number in range(1, len(texts) + 1)]
    at = 0
    for sentence in sentences(prose(whole)):
        if len(sentence.split()) < SENTENCE_WORDS:
            continue
        # The page its opening words are on: the last sentence's page or one of the next two,
        # since a sentence the book's breaks cut may be found on neither.
        opening = " ".join(sentence.split()[:3])
        found = next((i for i in range(at, min(at + 3, len(plain))) if opening in plain[i]), at)
        at = found
        pages[found]["sentences"] += 1
        pages[found]["held"] += (
            f" {sentence} " in comments or f" {sentence.rstrip('.')}" in comments
        )
    counts["book_sentences"] = sum(p["sentences"] for p in pages)
    counts["book_sentences_held"] = sum(p["held"] for p in pages)
    lines += ["## Commentary, page by page", ""]
    low = [p for p in pages if p["sentences"] >= 3 and p["held"] < LOW_SHARE * p["sentences"]]
    lines.append(
        f"{counts['book_sentences_held']} of {counts['book_sentences']} sentences held word for "
        f"word. Pages under {LOW_SHARE:.0%}, of three sentences or more:"
    )
    lines += ["", *(f"- p{p['page']}: {p['held']} of {p['sentences']}" for p in low), ""]
    return pages


def _overwrites(run: Path, games, unwritten, counts, lines) -> None:
    """From the session's logs: names saved again with another game, files written again
    without games written to them before, and games built but written nowhere."""
    builds, writes = _calls(run)
    lines += ["## Overwritten or lost work", ""]
    by_name = defaultdict(list)
    for build in builds:
        if build["name"]:
            by_name[build["name"]].append(build)
    for name, found in by_name.items():
        different = {(b["start"], b["round"], b["players"]) for b in found}
        agents = {b["agent"] for b in found}
        if len(found) > 1 and (len(different) > 1 or len(agents) > 1):
            counts["names_saved_again_differently"] += 1
            shown = "; ".join(
                f"{b['agent']} at {b['time']}: Round {b['round']}, {b['players']}, {b['start']}"
                for b in found
            )
            lines.append(f"- Name {name!r} saved {len(found)} times: {shown}")
    written: dict[str, list[dict]] = defaultdict(list)
    for write in writes:
        written[write["file"]].append(write)
    final = {name for write in writes for name in write["names"]}
    for file, found in written.items():
        for before, after in itertools.pairwise(found):
            dropped = [n for n in before["names"] if n not in after["names"]]
            lost = [
                n for n in dropped if not any(n in w["names"] for w in writes if w is not before)
            ]
            if dropped:
                counts["files_written_again_without_earlier_games"] += 1
                lines.append(
                    f"- {file} written again by {after['agent']} at {after['time']} without "
                    f"{len(dropped)} games {before['agent']} wrote to it"
                    + (f", {len(lost)} of them in no other write: {lost[:8]}" if lost else "")
                )
    counts["unwritten_games"] = len(unwritten)
    if unwritten:
        lines.append(f"- {len(unwritten)} games saved and written nowhere: see unwritten.pgn")
    counts["names_written"] = len(final)
    lines.append("")


def _calls(run: Path) -> tuple[list[dict], list[dict]]:
    """Every build_pgn and write_pgn call of every agent, from the session's logs, with its
    agent, time and what it saved or wrote."""
    projects = run / "claude-config" / "projects"
    builds, writes = [], []
    seen: set[str] = set()
    for log in sorted(projects.glob("**/*.jsonl"), key=lambda p: ("subagents" in p.parts, p)):
        agent = log.stem.removeprefix("agent-") if "subagents" in log.parts else "main"
        for line in log.read_text(errors="replace").split("\n"):
            if "mcp__pgnify__" not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message") or {}
            if entry.get("type") != "assistant":
                continue
            for block in message.get("content") or []:
                if block.get("type") != "tool_use" or block["id"] in seen:
                    continue
                seen.add(block["id"])
                name = block["name"].removeprefix(score_slice.TOOL_PREFIX)
                given = block.get("input") or {}
                stamp = (entry.get("timestamp") or "")[11:19]
                if name == "build_pgn":
                    spec: dict[str, Any] = (
                        given["spec"] if isinstance(given.get("spec"), dict) else {}
                    )
                    headers = spec.get("headers") or {}
                    builds.append(
                        {
                            "agent": agent,
                            "time": stamp,
                            "name": given.get("name"),
                            "start": (spec.get("start_fen") or "initial").split(" ")[0],
                            "round": headers.get("Round"),
                            "players": f"{headers.get('White')} - {headers.get('Black')}",
                        }
                    )
                elif name == "write_pgn":
                    writes.append(
                        {
                            "agent": agent,
                            "time": stamp,
                            "file": given.get("file") or "(the book's name)",
                            "names": list(given.get("names") or []),
                        }
                    )
    return builds, writes


def _contexts(run: Path, lines) -> None:
    """Each agent's context, request by request: its first, largest and last, and a dozen
    requests between, evenly spaced."""
    lines += ["## Context by request", ""]
    for account in score_slice.agents(run):
        sizes = account["contexts"]
        if not sizes:
            continue
        step = max(1, len(sizes) // 12)
        shown = ", ".join(f"{size // 1000}K" for size in sizes[::step])
        lines.append(
            f"- {account['agent']} ({account['type'] or 'main'}): {len(sizes)} requests, first "
            f"{sizes[0] // 1000}K, peak {max(sizes) // 1000}K, last {sizes[-1] // 1000}K, "
            f"{account['compactions']} compactions; {shown}"
        )
    lines.append("")


def _sample(inventory, games, boards, named, lines) -> None:
    """In each chapter, a test with its solution, a board's fragment and a whole game, where the
    chapter has them and the run has a game for them, chosen by a fixed seed."""
    chooser = random.Random(SAMPLE_SEED)
    lines += ["## Sample for the review by hand", ""]
    for chapter in inventory["chapters"]:
        picks = []
        tests = [
            b
            for b in inventory["boards"]
            if b["chapter"] == chapter["title"] and b["kind"] == "test" and boards.get(b["id"])
        ]
        fragments = [
            b
            for b in inventory["boards"]
            if b["chapter"] == chapter["title"]
            and b["kind"] == "diagram"
            and b.get("role") == "start"
            and boards.get(b["id"])
        ]
        whole = [
            g
            for g in inventory["games"]
            if g["chapter"] == chapter["title"] and g["from_move_one"] and named.get(g["id"])
        ]
        for pool, found in ((tests, boards), (fragments, boards), (whole, named)):
            if pool:
                item = chooser.choice(pool)
                picks.append(f"{item['id']} p{item['page']}: {found[item['id']][0]}")
        if picks:
            lines.append(f"- {chapter['title']}: " + "; ".join(picks))
    lines.append("")


def _report(run: Path) -> str:
    """What `pgnify report` says of the run's output folder, as a user would run it."""
    done = subprocess.run(
        ["pgnify", "report", str(run / "work")], capture_output=True, text=True, check=False
    )
    return (done.stdout + done.stderr).strip()


if __name__ == "__main__":
    main()
