"""Score runs against their slice's truth (experiments/slices.toml), from each run's
output PGN alone, and list every fault, for review against the run's final answer.

- Games: a game that starts where the game before it ends, as an analysis that goes on from a
  diagram at a line's end, is read joined on to that game, in the truth and the run alike, and
  so is a game of text alone that follows another from the same position with the same headers
  (a Round aside), as prose of one section that one game or two may hold. Each truth game is
  paired with the run's game that starts from its placement, squares the truth marks uncertain
  left out (on a scan, squares too dark to read), else with the one whose start differs in the
  fewest squares, at most four, reported as such; of several, the one whose main line agrees
  longest, then the one sharing most comment words. A run's game of text alone starts from the
  initial position, as a truth game that begins its section does, so for a truth game with
  moves it ranks after every run game with moves. The side to move is compared. Run games
  paired with no truth game are listed.
- Moves: the main line, exact or the plies that agree. Each variation of the truth's tree, from
  where it leaves its parent line to its end, is kept, kept in part or lost according to which
  of its moves the run's tree has at the same path from the start, as main line or variation. A
  line is optional from the move whose comment the truth marks [%optional]. Play the truth keeps
  in words after a move, with the moves a run may give for it instead marked on that move as
  [%accept moves SAN...], is required either way: given as moves when the run's tree has them
  there, as the move's continuation or in a variation that repeats the move; given in words when
  a comment of the paired game holds them as notation, in order; lost when it is neither. Off
  the truth's main line, such a play given as its line's own continuation (the run's node for
  the marked move, reached along its path by first matching children, has a child that is the
  play's first move) is counted apart as plays_as_continuation and listed, since the truth
  accepts it in words or in a branch, also when the run gives the branch too; at the main
  line's end the main line's exactness holds it instead, and a play marked
  [%accept continuation SAN...] is exempt. Moves of the run on no path of the truth, and on no
  such play, are unsupported, each listed from its first move. Given as the continuation of the
  truth's main line, such play makes the run's main line longer and so not exact, unless the
  truth marks it on the main line's last move as [%accept continuation SAN...]: play the book
  gives after an analysis line's last move as that line's own sequel, which a run may give as
  the main line's last moves. A variation whose first move the truth marks [%accept main] is a
  line the book gives in place of the truth's main line, which a run may give as its main line
  instead, and so from there: at each move the run's main line takes the truth's own or such a
  variation. The line the truth then accepts is the path so followed, continued by the truth's
  own course to the end of the line it is on, and, where the truth marks that end
  [%accept continuation SAN...], that line followed by exactly that play too. The run's main
  line is exact when it is one of those lines, and the truth's own main line is in the run's
  tree in full, by its paths, its plies agreeing as far as they are, and does not go on there
  (along each first child of the run's tree) but by exactly one of the plays the truth marks
  [%accept continuation SAN...] on its main line's end; a deviation onto an unmarked move, or a
  move more, leaves it not exact. The choices are counted apart, once a game, as structure.
- Commentary: the truth's comments cut into prose units, the words between move notation, with
  typography and spacing made uniform. A unit is verbatim when the paired game's comments hold
  it word for word, in one comment or split between comments; elsewhere when another game holds
  it word for word; near when one holds at least 85% of its words in order, or all but one of
  three or more, or, of a unit of two words, 85% of its characters (the differences listed), the
  paired game's comments first, and then another game's (near, in run game N instead). A unit
  split between comments is attached when each piece is at a place that would hold the whole
  unit (a unit cut at a variation's label, its lines each in a branch of their own). A unit in one
  comment is attached when the comment is at the unit's place: after the same move; within a
  variation in place of that move, or on the line's own move where the truth's comment is in a
  variation, but never in another variation in place of the truth's; within a variation in place of
  the next move; up to as many moves further along the truth's line as the truth's comment gives
  before the unit or right after it; or, for the words before a game's first move, in a game of text
  alone just before it, or at the start of a run game that starts from a position the truth's main
  line reaches; the truth's words at such a later start, after its move there, may also be in a game
  of text alone just before the run game. Moves a run gives for play the truth keeps in words are
  placed as if the truth's tree had them. Units of one truth comment that one run comment holds
  out of the truth's order are listed.
- Headers: each the truth gives that says something of the source, kept or not: a game's own
  (players, place, date, round, result) in the same header of its run game, the round and result
  exactly and the others with all their words in any order; any other, such as the chapter's
  name or a player's rating, in a header or comment of any run game, its words in order.
- Structure, apart from the content, so that no loss counts twice: truth games whose run games
  come out of the book's order; prose given in another number of games of text alone than the
  truth's (a partition, its words scored as above); and units the run's comments give more
  often than the truth's, a passage given twice.
- Words of the run's comments outside the truth's units: those the source has, such as running
  heads and page numbers, are counted; those it lacks are listed as unsupported.
- Characters: each line of the truth's comments of two words or more (a heading such as
  "Solution 3" included), character for character in the paired game's comments, white space
  aside, apart from the words, so that a figurine written as a letter or a straight quote for a
  curly one is a character's fault and not a word's. A line that holds no unit of prose, as
  "Solution 3" or "CHAPTER 2", which the words' scoring does not hold, is checked by its
  characters alone: exact, or missing. A line of notation alone that is not in the comments but
  whose moves the run's tree has, from the position before the truth's numbered move that begins
  the line, is counted apart (lines_notation_in_tree) and listed, its moves being scored in the
  tree. A line that holds play the run gave as moves is left out, its words and its moves being
  scored apart.
- NAGs on the moves the run shares with the truth, other than the plies before a run game that
  starts later on the truth's main line, which are counted apart; a set the truth marks on a
  move as [%accept nag N...] agrees there as well as the truth's own.
- Running: from the session log, wall time, requests, tokens, thinking, compactions, cost at
  list price, and the tool calls, build_pgn's by outcome, over the main conversation and its
  subagents; each agent's share apart, and the advisor's; and the final answer.

Writes score.json, with the scorer's fingerprint, and faults.md into each run's folder and
prints a summary.

    uv run python experiments/score_slice.py SLICE RUN_DIR...
"""

import argparse
import copy
import difflib
import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

import chess
import chess.pgn
import session

import pgnify.chess_tools
import pgnify.prose
from pgnify.chess_tools import nodes, read_games
from pgnify.prose import (
    COMMAND,
    MOVE,
    TYPOGRAPHY,
    BookProse,
    bare,
    book_prose,
    notated_passages,
    passages,
    prose,
    sentences,
    shortened,
)

ROOT = Path(__file__).resolve().parents[1]
OPTIONAL = "[%optional]"
# The truth's marks of a reading a run may take instead of its own: moves for play it keeps in
# words (continuation: play a run may also give as the main line's own last moves), and NAGs.
ACCEPT = re.compile(r"\[%accept (moves|continuation|nag) ([^\]]+)\]")
# The truth's mark on a variation's first move: a line a run may give as its main line instead.
ACCEPT_MAIN = "[%accept main]"
NEAR = 0.85
# The prefix Claude Code gives the names of pgnify's tools.
TOOL_PREFIX = "mcp__pgnify__"
# The most squares in which a run game's start may differ from a truth game's for the two to pair.
MOST_DIFFERING = 4
# How many words a fault quotes of a truth unit, and of the words the source lacks.
QUOTED = 12
QUOTED_UNSUPPORTED = 30
# The headers that name a game itself, compared with the same header of its run game; any other
# the truth gives, such as its chapter's name or a player's rating, is kept wherever the run
# gives it, once.
GAME_HEADERS = ("White", "Black", "Site", "Date", "Round", "Result")
# Headers that say nothing of the source: positions, which are scored apart, and the truth's own
# marks.
NOT_SOURCE_HEADERS = ("FEN", "SetUp", "Uncertain", "Item")
UNKNOWN = ("", "?", "????.??.??", "*")


def fingerprint() -> str:
    """The scorer's fingerprint: its code, the map scorer's, the pgnify modules it reads prose
    and games with, and python-chess's version."""
    digest = hashlib.sha256()
    for path in (
        Path(__file__),
        Path(__file__).with_name("score_map.py"),
        Path(pgnify.prose.__file__),
        Path(pgnify.chess_tools.__file__),
    ):
        digest.update(path.read_bytes())
    digest.update(chess.__version__.encode())
    return digest.hexdigest()[:16]


class Price(NamedTuple):
    """Dollars per million tokens at list price."""

    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float


# As Claude Code's model catalog gives them (2.1.319), which its own cost
# accounting uses: the sessions' model, and those a subagent or the advisor may run on.
PRICES = {
    "claude-sonnet-5-5": Price(2.0, 10.0, 0.10, 2.50, 4.0),
    "claude-opus-5-5": Price(4.0, 20.0, 0.20, 5.0, 8.0),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, 0.125, 0.20),
    "claude-fable-5-1": Price(10.0, 50.0, 0.25, 12.5, 20.0),
}
# A model whose catalog entry prices a long prompt apart: a request whose prompt (its input,
# cache reads and cache writes) passes the threshold, in tokens, costs the long price throughout.
LONG_PROMPT = {
    "claude-haiku-5-5": (100_000, Price(0.50, 2.50, 0.05, 0.625, 1.0)),
}
# The token counts an agent's requests are totalled in.
TOKENS = (
    "input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_write_1h_tokens",
    "output_tokens",
    "thinking_tokens",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("slice")
    parser.add_argument("run_dirs", nargs="+", type=Path, metavar="run_dir")
    args = parser.parse_args()
    spec = session.slices()["slices"][args.slice]
    truth = read_games((ROOT / spec["truth"]).read_text())
    source = book_prose(session.cut_file(spec["cut"]))
    scope = [tuple(pages) for pages in spec.get("scope_pages", [])] or None
    for run_dir in args.run_dirs:
        score, faults = score_run(run_dir, truth, source, scope)
        (run_dir / "score.json").write_text(json.dumps(score, indent=1))
        (run_dir / "faults.md").write_text(faults)
        print(json.dumps(score))


def score_run(
    run: Path,
    truth: Sequence[chess.pgn.Game],
    source: BookProse,
    scope: list[tuple[int, int]] | None = None,
) -> tuple[dict[str, Any], str]:
    """A run's score, and its faults as a page for the review. A review's run is scored on the
    file it wrote, reviewed.pgn, and any other run on every PGN file in its output folder."""
    files = sorted((run / "work").glob("*.pgn"))
    if (run / "work" / "reviewed.pgn").exists():
        files = [run / "work" / "reviewed.pgn"]
    output = [game for pgn in files for game in read_games(pgn.read_text())]
    score, faults = score_output(truth, output, source)
    score["scorer"] = fingerprint()
    running = _session(run)
    answer = running.pop("final_answer")
    score.update(running)
    score["exploration"] = exploration(run, scope)
    lines = [f"# Faults: {run.relative_to(ROOT) if run.is_relative_to(ROOT) else run}", ""]
    lines += faults
    lines += ["", "## Running", ""]
    lines += running_lines(running)
    lines += ["", "## Final answer", "", answer or "(none)", ""]
    return score, "\n".join(lines)


def exploration(run: Path, scope: list[tuple[int, int]] | None) -> dict[str, Any]:
    """How the run read the book, from the server's log: its calls by tool; the pages doc_text
    and doc_page gave, against the distinct pages and those outside the slice's scope; searches;
    diagrams read more than once; the seconds before its first build and the pages read by
    then; and the seconds spent in the tools. For a review, whose output folder is a copy of
    the run it reviews, only the calls made after it started count."""
    log = run / "work" / ".pgnify" / "log.jsonl"
    if not log.exists():
        return {}
    since = None
    if (run / "session.json").exists():
        phases = json.loads((run / "session.json").read_text()).get("phases") or []
        since = phases[0].get("start") if phases else None
    entries = []
    for line in log.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        when = datetime.fromisoformat(entry["time"]).timestamp()
        if since is None or when >= since - 1:  # the log keeps whole seconds
            entries.append((when, entry))
    inside = {page for first, last in scope or [] for page in range(first, last + 1)}
    tools: Counter[str] = Counter(entry["tool"] for _, entry in entries)
    read: list[int] = []
    shown: list[int] = []
    readings: Counter[str] = Counter()
    first_build = None
    before_build: set[int] = set()
    for when, entry in entries:
        tool, arguments, result = (
            entry["tool"],
            entry.get("arguments") or {},
            entry.get("result") or {},
        )
        if tool == "doc_text":
            pages = result.get("pages") or []
            read += pages
            if first_build is None:
                before_build.update(pages)
        elif tool == "doc_page" and arguments.get("page"):
            shown.append(arguments["page"])
            if first_build is None:
                before_build.add(arguments["page"])
        elif tool == "read_diagram":
            readings[json.dumps(arguments, sort_keys=True)] += 1
        elif tool == "build_pgn" and first_build is None:
            first_build = when
    start = entries[0][0] if entries else None
    return {
        "calls": dict(tools),
        "pages_read": len(read),
        "distinct_pages_read": len(set(read)),
        "pages_read_outside_scope": len(set(read) - inside) if scope else None,
        "page_pictures": len(shown),
        "distinct_page_pictures": len(set(shown)),
        "searches": tools.get("doc_search", 0),
        "diagram_readings": sum(readings.values()),
        "diagram_readings_repeated": sum(n - 1 for n in readings.values() if n > 1),
        "seconds_before_first_build": round(first_build - start, 1)
        if first_build and start
        else None,
        "distinct_pages_before_first_build": len(before_build),
        "tool_seconds": round(sum(entry.get("seconds") or 0 for _, entry in entries), 1),
    }


def running_lines(running: dict[str, Any]) -> list[str]:
    """_session's account as list items, each agent's on a line of its own."""
    lines = [f"- {key}: {value}" for key, value in running.items() if key != "agents"]
    for agent in running.get("agents", []):
        shown = {key: value for key, value in agent.items() if key not in ("agent", "report")}
        lines.append(f"- agent {agent['agent']}: {shown}")
    return lines


def score_output(
    truth: Sequence[chess.pgn.Game], output: Sequence[chess.pgn.Game], source: BookProse
) -> tuple[dict[str, Any], list[str]]:
    """Score a run's games against the truth's, and list the faults found."""
    truth, truth_parts = _joined(truth)
    output, run_parts = _joined(output)
    pairs = _pair(truth, output)
    given_anywhere = _anywhere(output)
    faults: list[str] = []
    counts: Counter[str] = Counter()
    every_comment = [
        (index, prose(_unbroken(text)))
        for index, game in enumerate(output)
        for _, text in _comments(game)
    ]
    for t_index, game in enumerate(truth):
        label = _label(game, t_index)
        counts["truth_games"] += 1
        r_index, differing, offset = pairs.get(t_index, (None, [], 0))
        found = output[r_index] if r_index is not None else None
        if found is not None and offset:
            counts["games_found_from_later_position"] += 1
            counts["plies_before_run_start"] += offset
            faults.append(
                f"- {label}: the run's game starts from the position after {offset} plies of the "
                "truth's main line; those plies are not in its tree"
            )
            found = _rerooted(found, game, offset)
        if found is None:
            faults.append(f"- {label}: no game in the output starts from its position")
        else:
            counts["games_found"] += 1
            if differing:
                counts["games_found_start_differs"] += 1
                squares = ", ".join(differing)
                marked = found.headers.get("Uncertain")
                note = f" (the run marks {marked} as uncertain)" if marked else ""
                faults.append(
                    f"- {label}: the run's game starts from another placement: {squares}{note}"
                )
            if found.board().turn != game.board().turn:
                counts["side_to_move_wrong"] += 1
                faults.append(
                    f"- {label}: the run's game has {_side(found)} to move, the truth {_side(game)}"
                )
        _score_moves(label, game, found, offset, counts, faults)
        if offset:
            # The truth's plies before the run game's start are bare copies, not the run's.
            counts["main_line_plies_agreeing"] -= offset
        before = output[r_index - 1] if r_index else None
        text_before = before if before is not None and not before.variations else None
        # Where the run game starts, as a path of the truth's: after its first plies, if it
        # starts from a position the truth's main line reaches.
        start = tuple(move.uci() for move in list(game.mainline_moves())[:offset])
        _score_comments(
            label, game, found, r_index, text_before, start, every_comment, counts, faults
        )
        _score_characters(label, game, found, counts, faults)
        _score_headers(label, game, found, given_anywhere, counts, faults)
    paired = {r_index for r_index, _, _ in pairs.values()}
    for r_index, game in enumerate(output):
        if r_index not in paired:
            counts["games_unpaired"] += 1
            moves = " ".join(node.san() for node in list(game.mainline())[:6])
            words = " ".join(prose(game.comment).split()[:8])
            faults.append(
                f"- Run game {r_index + 1} pairs with no truth game: starts {game.board().fen()}"
                f"{', ' + moves if moves else ''}{', «' + words + '»' if words else ''}"
            )
    _score_extra_words(truth, output, source, counts, faults)
    _score_structure(truth, truth_parts, output, run_parts, pairs, counts, faults)
    _score_duplicates(truth, output, counts, faults)
    return dict(counts), faults


def _score_moves(
    label: str,
    truth: chess.pgn.Game,
    found: chess.pgn.Game | None,
    offset: int,
    counts: Counter[str],
    faults: list[str],
) -> None:
    truth_paths = _paths(truth)
    run_paths = _paths(found) if found is not None else {}
    main = list(truth.mainline())
    counts["truth_plies"] += len(main)
    if found is not None:
        run_main = list(found.mainline_moves())
        instead = _main_instead(truth, run_main)
        if instead is None:
            agree = _agreeing(found, truth)
            exact = run_main in _main_lines(truth)
            if not exact:
                has = _has(found, agree)
                faults.append(
                    f"- {label}: main line differs after {agree} of {len(main)} plies: {has}"
                )
        else:
            # The run's main line is a line the truth accepts as main in place of its own, which
            # is then one of the run's lines, kept by its paths.
            taken, lines = instead
            agree = next((i for i, n in enumerate(main) if _key(n) not in run_paths), len(main))

            def follows_of(line: list[chess.Move]) -> int:
                return next(
                    (i for i, (a, b) in enumerate(zip(run_main, line, strict=False)) if a != b),
                    min(len(run_main), len(line)),
                )

            # Of the lines the truth accepts, the one the run's main line follows longest.
            line = max(lines, key=follows_of)
            follows = follows_of(line)
            exact = agree == len(main) and run_main in lines
            counts["main_lines_accepted_instead"] += 1
            faults.append(
                f"- {label}: main line by "
                + ", ".join(
                    f"{_line_text([v])} in place of {_line_text([v.parent.variations[0]])}"
                    for v in taken
                )
                + ", as the truth accepts (structure)"
            )
            if run_main not in lines:
                faults.append(
                    f"- {label}: main line differs from the line the truth accepts in place of "
                    f"its own after {follows} of {len(line)} plies: {_has(found, follows)}"
                )
            if agree < len(main):
                faults.append(
                    f"- {label}: the truth's main line kept in part, {agree} of {len(main)} "
                    f"plies: the run lacks {_line_text(main[agree : agree + 4])}"
                )
            elif beyond := _beyond_main(truth, found):
                # The truth's own main line, given as a variation, goes on where the truth's
                # ends, other than by a continuation the truth accepts there.
                exact = False
                faults.append(
                    f"- {label}: the truth's main line, given as a variation, goes on past its "
                    f"end: {_line_text(beyond)}"
                )
        counts["main_line_plies_agreeing"] += agree
        counts["main_lines_exact"] += exact
    for line, optional_from in _variations(truth):
        text = _describe(line)
        required = line if optional_from is None else line[:optional_from]
        if required:
            counts["truth_lines_required"] += 1
            have = sum(_key(node) in run_paths for node in required)
            if have == len(required):
                counts["lines_kept"] += 1
            elif _key(required[0]) in run_paths:
                counts["lines_kept_in_part"] += 1
                faults.append(
                    f"- {label}: line kept in part, {have} of {len(required)} moves: {text}"
                )
            else:
                counts["lines_lost"] += 1
                faults.append(f"- {label}: line lost: {text}")
            if optional_from is not None and _key(line[optional_from]) in run_paths:
                counts["optional_ends_given"] += 1
        else:
            counts["truth_lines_optional"] += 1
            kept = sum(_key(node) in run_paths for node in line)
            if kept:
                counts["optional_lines_given"] += 1
                faults.append(
                    f"- {label}: optional line given ({kept} of {len(line)} moves): {text}"
                )
    accepted: set[tuple[str, ...]] = set()
    for node, play in _plays(truth):
        keys = _play_keys(node, play)
        accepted.update(keys)
        text = f"after {_line_text([node])}: {' '.join(play)}"
        counts["truth_plays"] += 1
        given = sum(key in run_paths for key in keys)
        if given == len(keys):
            counts["plays_as_moves"] += 1
        elif found is not None and _in_words(play, found):
            counts["plays_in_words"] += 1
        elif given:
            counts["plays_in_part"] += 1
            faults.append(f"- {label}: play given in part, {given} of {len(keys)} moves, {text}")
        else:
            counts["plays_lost"] += 1
            faults.append(f"- {label}: play lost, neither as moves nor in words, {text}")
    # A play the truth marks moves, accepted in words or in a branch, given as moves that go on
    # from its move as that line's own continuation, off the truth's main line: at its end the
    # main line's exactness holds it, and a play marked continuation is accepted so.
    for node, play in _plays(truth, ("moves",)):
        if found is None or node in main:
            continue
        here = _follow(found, _key(node))
        first = node.board().parse_san(play[0])
        if here is not None and any(child.move == first for child in here.variations):
            counts["plays_as_continuation"] += 1
            faults.append(
                f"- {label}: play given as its line's own continuation, where the reference "
                f"accepts it in words or in a branch: after {_line_text([node])}: {' '.join(play)}"
            )
    supported = truth_paths.keys() | accepted
    for key, node in run_paths.items():
        if key in supported:
            continue
        if key[:-1] in run_paths and key[:-1] not in supported:
            continue  # within an unsupported line already listed from its first move
        counts["unsupported_lines"] += 1
        faults.append(f"- {label}: unsupported moves: {_describe(_rest_of_line(node))}")
    # The plies before a run game that starts later on the truth's main line are counted apart,
    # their marks with them: bare copies, not the run's.
    before_start = {_key(node) for node in main[:offset]}
    for key, node in truth_paths.items():
        counts["truth_nags"] += bool(node.nags)
        if key in before_start:
            counts["nags_before_run_start"] += bool(node.nags)
            continue
        if key not in run_paths:
            continue
        run_nags = run_paths[key].nags
        if run_nags != node.nags and run_nags in _accepted_nags(node):
            counts["nags_accepted"] += 1
            counts["nags_agreeing"] += bool(node.nags)
            continue
        counts["nags_agreeing"] += bool(node.nags) and run_nags == node.nags
        if run_nags != node.nags:
            counts["nags_unsupported"] += bool(run_nags - node.nags)
            counts["nags_missing"] += bool(node.nags - run_nags)
            move = _line_text([run_paths[key]])
            run_shown, truth_shown = sorted(run_nags) or "none", sorted(node.nags) or "none"
            faults.append(
                f"- {label}: NAGs on {move}: the run {run_shown}, the truth {truth_shown}"
            )


def _score_comments(
    label: str,
    truth: chess.pgn.Game,
    found: chess.pgn.Game | None,
    r_index: int | None,
    text_before: chess.pgn.Game | None,
    start: tuple[str, ...],
    every_comment: list[tuple[int, str]],
    counts: Counter[str],
    faults: list[str],
) -> None:
    run_comments = (
        [(where, prose(_unbroken(text))) for where, text in _comments(found)]
        if found is not None
        else []
    )
    before = (
        " ".join(prose(_unbroken(text)) for _, text in _comments(text_before))
        if text_before is not None
        else ""
    )
    tree = _paths(truth)
    # Moves a run gives for play the truth keeps in words, whose comments are placed as if the
    # truth's tree had them.
    known = tree.keys() | {key for node, play in _plays(truth) for key in _play_keys(node, play)}
    for where, text in _comments(truth):
        place = _place(where, tree)
        # The units of this comment each run comment holds, in the truth's order.
        given: dict[int, list[str]] = {}
        for unit, slack in _units(_unbroken(text)):
            words = len(unit.split())
            quoted = shortened(unit, QUOTED)
            counts["truth_units"] += 1
            counts["truth_words"] += words
            held = [i for i, (_, t) in enumerate(run_comments) if _holds(t, unit)]
            # The words before the first move, or at the run game's later start, may also be in
            # a game of text alone just before it.
            in_text_before = (not where or where == start) and _holds(before, unit)

            if held or in_text_before:
                counts["units_verbatim"] += 1
                counts["words_verbatim"] += words
                at_place_in = [
                    i
                    for i in held
                    if _comment_at_place(
                        run_comments[i][0], where, start, truth, tree, known, slack
                    )
                ]
                if held:
                    given.setdefault((at_place_in or held)[0], []).append(unit)
                if in_text_before or at_place_in:
                    counts["units_attached"] += 1
                else:
                    faults.append(f"- {label}: words not at their place, {place}: «{quoted}»")
                continue
            if pieces := _split_pieces(run_comments, unit):
                counts["units_verbatim"] += 1
                counts["words_verbatim"] += words
                counts["units_split"] += 1
                # A unit cut between comments is at its place when each of its pieces is at a
                # place that would hold the whole unit, as a unit cut at a variation's label,
                # its lines each in a branch of their own, is.
                if any(
                    all(
                        _comment_at_place(
                            run_comments[i][0], where, start, truth, tree, known, slack
                        )
                        for i in covered
                    )
                    for covered in pieces
                ):
                    counts["units_attached"] += 1
                    faults.append(
                        f"- {label}: words split between comments, each piece at its place, "
                        f"{place}: «{quoted}»"
                    )
                else:
                    faults.append(f"- {label}: words split between comments, {place}: «{quoted}»")
                continue
            # A unit the run holds word for word in another game is placed there, whatever it
            # holds near the unit's place.
            elsewhere = [i for i, t in every_comment if i != r_index and _holds(t, unit)]
            if elsewhere:
                counts["units_elsewhere"] += 1
                faults.append(
                    f"- {label}: words in run game {elsewhere[0] + 1} instead: «{quoted}»"
                )
                continue
            near = _nearest(unit, [t for _, t in run_comments])
            if near is not None:
                counts["units_near"] += 1
                faults.append(f"- {label}: words near but not verbatim, {place}: {near}")
                continue
            # Or one holds most of it, in order and close together, though not at its place.
            nearest = _nearest_elsewhere(unit, every_comment, r_index)
            if nearest is not None:
                game, near = nearest
                counts["units_near_elsewhere"] += 1
                faults.append(
                    f"- {label}: words near but not verbatim, in run game {game + 1} instead: "
                    f"{near}"
                )
                continue
            counts["units_missing"] += 1
            faults.append(f"- {label}: words missing, {place}: «{quoted}»")
        for index, units in given.items():
            for unit in _out_of_order(units, run_comments[index][1]):
                counts["units_out_of_order"] += 1
                faults.append(
                    f"- {label}: words out of the book's order, {place}: "
                    f"«{shortened(unit, QUOTED)}»"
                )


def _comment_at_place(
    held: tuple[str, ...],
    where: tuple[str, ...],
    start: tuple[str, ...],
    truth: chess.pgn.Game,
    tree: dict[tuple[str, ...], chess.pgn.ChildNode],
    known: set[tuple[str, ...]],
    slack: int,
) -> bool:
    """Whether a run's comment at held is at the place of the truth's at where (_at_place), or,
    for the words before the first move, at the start of a run game that starts later on the
    truth's main line."""
    return _at_place(held, where, truth, tree, known, slack) or (not where and held == start)


def _split_pieces(run_comments: list[tuple[tuple[str, ...], str]], unit: str) -> list[list[int]]:
    """Where the run's comments hold the unit word for word only taken together, running on from
    one to the next: for each such occurrence, the indexes of the comments it covers; none if
    the unit does not occur so."""
    joined = " ".join(text for _, text in run_comments)
    starts, position = [], 0
    for _, text in run_comments:
        starts.append(position)
        position += len(text) + 1
    found = []
    for match in _unit_pattern(unit).finditer(joined):
        found.append(
            [
                i
                for i, (_, text) in enumerate(run_comments)
                if text and starts[i] < match.end() and starts[i] + len(text) > match.start()
            ]
        )
    return found


# A space after a hyphen or an ellipsis that a page's line break may have put there, or taken
# away: "b4-\nsquares" on a page is "b4-squares" in the book, and a model that reads the page
# cannot tell. Both the truth's comments and the run's are read with such a space taken out.
LINE_BREAK_SPACE = re.compile(r"(?<=[\w)][-\u2013]) (?=\w)|(?<=\u2026) (?=\S)")
# A bracket right after a ! or ?, as in "Rc1!(Simple chess.", which the book prints after a space.
# It is read with the space, so that the move before it is cut off as a move and not glued to the
# word after it.
MARK_BEFORE_BRACKET = re.compile(r"(?<=[!?])(?=\()")


def _unbroken(text: str) -> str:
    return MARK_BEFORE_BRACKET.sub(" ", LINE_BREAK_SPACE.sub("", text))


# A dash that joins a move to a word in the book's text, "Kh1!-Doh", which a run gives as the move
# and a comment that begins with the dash, "-Doh". The move is kept and the dash goes with the
# word. A move is a piece's, or a pawn's with a mark or a check; a bare square, as in "b5-pawn",
# is not one, nor is a move written with a hyphen ("e4-e5", "Ng1-f3"), and a word before the dash
# ("Trebuchet-after") is as it was.
SQUARE = r"[a-h][1-8]"
GLUED_DASH = re.compile(
    rf"(?<![^\s.\u2026\d])((?:[KQRBN\u2654-\u2658][a-h]?x?{SQUARE}(?:=[QRBN])?[+#]?[!?]*"
    rf"|[a-h]?x?{SQUARE}(?:=[QRBN])?(?:[+#][!?]*|[!?]+)|O-O(?:-O)?[+#]?[!?]*))"
    r"(?=[-\u2013\u2014][^\W\d_]{2,}\b)"
)


def _dash_apart(source: BookProse) -> BookProse:
    """The book's readings with a dash that joins a move to a word read as a word boundary."""
    return BookProse(tuple(GLUED_DASH.sub(r"\1 ", reading) for reading in source.readings))


def _score_characters(
    label: str,
    truth: chess.pgn.Game,
    found: chess.pgn.Game | None,
    counts: Counter[str],
    faults: list[str],
) -> None:
    """Each line of the truth's comments of two words or more, character for character in
    the paired game's comments, white space aside: apart from the words, so that a figurine
    written as a letter or a straight quote for a curly one counts as a character's fault. A
    line that holds no unit of prose, a heading such as "Solution 3", which the words' scoring
    keeps no number of, is exact or missing: the words cannot show it present with its
    characters changed, nor tell a number from another."""
    run = (
        " ".join(_unbroken(" ".join(text.split())) for _, text in _comments(found))
        if found is not None
        else ""
    )
    run_paths = _paths(found) if found is not None else {}
    as_moves = [
        play
        for node, play in _plays(truth)
        if all(key in run_paths for key in _play_keys(node, play))
    ]
    for _, text in _comments(truth):
        for line in text.split("\n"):
            line = _unbroken(" ".join(COMMAND.sub(" ", line).split()))
            if len(line.split()) < 2:
                continue
            if any(_holds_play(_notation(line), play) for play in as_moves):
                counts["lines_with_play_as_moves"] += 1
                continue
            counts["truth_lines"] += 1
            if line in run:
                counts["lines_character_exact"] += 1
            elif not _units(line):
                if (
                    found is not None
                    and _notation(line)
                    and _in_tree(found, _notation(line), _start_positions(truth, line))
                ):
                    # A line of notation alone holds no prose, and its moves are in the run's
                    # tree, where they are scored.
                    counts["lines_notation_in_tree"] += 1
                    faults.append(
                        f"- {label}: line of notation alone not in the comments, its moves in "
                        f"the run's tree: «{shortened(line, QUOTED)}»"
                    )
                    continue
                counts["lines_missing"] += 1
                faults.append(f"- {label}: line missing: «{shortened(line, QUOTED)}»")
            elif found is not None and _holds(prose(run), prose(line)):
                counts["lines_characters_differ"] += 1
                faults.append(f"- {label}: characters differ in «{shortened(line, QUOTED)}»")


def _holds(text: str, unit: str) -> bool:
    """Whether the text holds the unit word for word, from the start of a word to the end of
    one, punctuation after it allowed."""
    return _unit_pattern(unit).search(text) is not None


def _unit_pattern(unit: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\S)" + re.escape(unit) + r"(?=[\s.,;:!?'\"]|$)")


def _out_of_order(units: list[str], text: str) -> list[str]:
    """Of units a text holds, given in the truth's order, those it does not hold in that order:
    each found at its first place after the unit before it, else at its first, the units outside
    the longest run of them in order."""
    places, at = [], 0
    for unit in units:
        found = [match.start() for match in _unit_pattern(unit).finditer(text)]
        place = next((p for p in found if p >= at), found[0])
        places.append(place)
        at = place + 1
    in_order = _ordered(places)
    return [unit for index, unit in enumerate(units) if index not in in_order]


def _ordered(values: list[int]) -> set[int]:
    """The indices of a longest run of the values that never decreases, in their order."""
    longest, back = [1] * len(values), [-1] * len(values)
    for i in range(len(values)):
        for j in range(i):
            if values[j] <= values[i] and longest[j] + 1 > longest[i]:
                longest[i], back[i] = longest[j] + 1, j
    index = max(range(len(values)), key=longest.__getitem__, default=-1)
    kept = set()
    while index >= 0:
        kept.add(index)
        index = back[index]
    return kept


def _header_words(text: str) -> list[str]:
    """A header's words, accents and case aside."""
    flat = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.findall(r"\w+", flat.casefold())


def _anywhere(output: Sequence[chess.pgn.Game]) -> str:
    """The words of every header and comment of the run's games, for a header the truth gives
    each game that a run may give once."""
    texts = [v for game in output for v in game.headers.values()]
    texts += [text for game in output for _, text in _comments(game)]
    return " " + " ".join(_header_words(" ".join(texts))) + " "


def _score_headers(
    label: str,
    truth: chess.pgn.Game,
    found: chess.pgn.Game | None,
    given_anywhere: str,
    counts: Counter[str],
    faults: list[str],
) -> None:
    """Each header of the truth that says something of the source, kept or not: a game's own
    (players, place, date, round, result) in the same header of its run game, the round and
    result exactly and the others with all their words in any order ("U. Andersson" or
    "Andersson, U."); any other, such as the chapter's name, which the book prints once, or a
    rating, in a header or comment of any of the run's games, its words in order."""
    for key, value in truth.headers.items():
        if key in NOT_SOURCE_HEADERS or value.strip() in UNKNOWN:
            continue
        counts["truth_headers"] += 1
        given = found.headers.get(key, "") if found is not None else ""
        words = _header_words(value)
        if key in ("Result", "Round"):
            kept = given.strip() == value.strip()
        elif key in GAME_HEADERS:
            kept = set(words) <= set(_header_words(given))
        else:
            kept = f" {' '.join(words)} " in given_anywhere
        if kept:
            counts["headers_kept"] += 1
        else:
            shown = f" (the run's: «{given}»)" if given.strip() not in UNKNOWN else ""
            faults.append(f"- {label}: header {key} «{value}» not kept{shown}")


def _score_structure(
    truth: Sequence[chess.pgn.Game],
    truth_parts: list[int],
    output: Sequence[chess.pgn.Game],
    run_parts: list[int],
    pairs: dict[int, tuple[int, list[str], int]],
    counts: Counter[str],
    faults: list[str],
) -> None:
    """The structure apart from the content, so that no loss is counted twice: the truth games
    out of the book's order, those outside the longest run of them whose run games come in
    order; and each run game of text alone that gives prose in another number of games than
    the truth games it holds, joined, were (a partition, its words scored as the truth's)."""
    paired = [t_index for t_index in range(len(truth)) if t_index in pairs]
    in_order = _ordered([pairs[t_index][0] for t_index in paired])
    for index, t_index in enumerate(paired):
        if index not in in_order:
            counts["games_out_of_order"] += 1
            faults.append(
                f"- {_label(truth[t_index], t_index)}: out of the book's order, as run game "
                f"{pairs[t_index][0] + 1}"
            )
    holding: dict[int, list[int]] = {}
    for t_index in paired:
        if not truth[t_index].variations:
            holding.setdefault(pairs[t_index][0], []).append(t_index)
    for r_index, t_indices in holding.items():
        printed = sum(truth_parts[t_index] for t_index in t_indices)
        if not output[r_index].variations and printed != run_parts[r_index]:
            counts["text_partition_differs"] += 1
            faults.append(
                f"- Run game {r_index + 1}: prose the truth gives in {printed} games of text "
                f"alone given in {run_parts[r_index]} (a partition, its words scored apart)"
            )


def _score_duplicates(
    truth: Sequence[chess.pgn.Game],
    output: Sequence[chess.pgn.Game],
    counts: Counter[str],
    faults: list[str],
) -> None:
    """Each unit of the truth that the run's comments give more often than the truth's do: a
    passage given twice, in two games or in one."""
    truth_texts = [prose(_unbroken(text)) for game in truth for _, text in _comments(game)]
    run_texts = [prose(_unbroken(text)) for game in output for _, text in _comments(game)]
    units = dict.fromkeys(
        unit for game in truth for _, text in _comments(game) for unit, _ in _units(_unbroken(text))
    )
    for unit in units:
        pattern = _unit_pattern(unit)
        given = sum(len(pattern.findall(text)) for text in run_texts)
        printed = sum(len(pattern.findall(text)) for text in truth_texts)
        if given > printed:
            counts["units_duplicated"] += given - printed
            faults.append(
                f"- Words given {given} times, the truth {printed}: «{shortened(unit, QUOTED)}»"
            )


def _score_extra_words(
    truth: Sequence[chess.pgn.Game],
    output: Sequence[chess.pgn.Game],
    source: BookProse,
    counts: Counter[str],
    faults: list[str],
) -> None:
    """Count the words of the run's comments that no truth unit covers, ignoring punctuation at
    word ends: those the source has, those that nearly copy a truth unit (already listed as
    near), and those the source lacks, which are listed. Work sentence by sentence, so that a
    comment joining two passages of the source, such as a heading and the line under it, keeps
    the source's words."""
    source = _dash_apart(source)
    known = [
        bare(unit).split()
        for unit in {
            unit for game in truth for _, t in _comments(game) for unit, _ in _units(_unbroken(t))
        }
    ]
    for index, game in enumerate(output):
        tree = _paths(game)
        for where, text in _comments(game):
            for stretch in _uncovered(_unbroken(text), known):
                piece = " ".join(stretch)
                if source.holds(piece):
                    counts["source_words_outside_truth"] += len(stretch)
                elif _near_any(stretch, known):
                    counts["near_words"] += len(stretch)
                else:
                    counts["unsupported_words"] += len(stretch)
                    faults.append(
                        f"- Run game {index + 1}, {_place(where, tree)}: words the source lacks: "
                        f"«{shortened(piece, QUOTED_UNSUPPORTED)}»"
                    )


def _uncovered(text: str, known: list[list[str]]) -> list[list[str]]:
    """The stretches of a comment's words, punctuation at their ends aside, that no known unit
    covers where it appears word for word, sentence by sentence."""
    stretches: list[list[str]] = []
    for passage in passages(text):
        for sentence in sentences(passage):
            words = bare(sentence).split()
            covered = [False] * len(words)
            for target in known:
                for start in range(len(words) - len(target) + 1):
                    if words[start : start + len(target)] == target:
                        covered[start : start + len(target)] = [True] * len(target)
            stretches.append([])
            for word, cover in zip(words, covered, strict=True):
                if cover:
                    stretches.append([])
                else:
                    stretches[-1].append(word)
    return [stretch for stretch in stretches if stretch]


def _near_any(words: list[str], known: list[list[str]]) -> bool:
    """Whether the words nearly copy a known unit of two or more words: they number between
    half the unit's length less three and _widest's bound, and hold enough of its words in
    order to be _close."""
    for target in known:
        if len(target) < 2 or not 0.5 * len(target) - 3 <= len(words) <= _widest(len(target)):
            continue
        matcher = difflib.SequenceMatcher(None, target, words, autojunk=False)
        if _close(len(target), sum(block.size for block in matcher.get_matching_blocks())):
            return True
    return False


def _close(size: int, matched: int) -> bool:
    """Whether a unit of size words with matched of them found in order is a near copy: at
    least 85% of its words, or all but one of three or more."""
    return matched >= NEAR * size or (size >= 3 and size - matched <= 1)


def _widest(size: int) -> float:
    """The most words over which a near copy of a unit of size words may spread."""
    return 1.5 * size + 3


def _units(text: str) -> list[tuple[str, int]]:
    """The text's units of prose, its sentences cut also at move notation, each with its slack:
    how many moves the text gives before it, and one more if a move follows it at once, so how
    many moves after its comment's place a run may give it. Units of one word, the glue
    between moves such as "and" or "or", are left out."""
    found = []
    for passage, before, followed in notated_passages(text):
        parts = sentences(passage)
        for index, sentence in enumerate(parts):
            if len(sentence.split()) > 1:
                last = index == len(parts) - 1
                found.append((sentence, before + (1 if last and followed else 0)))
    return found


def _nearest(unit: str, texts: list[str]) -> str | None:
    """The words that differ where a comment holds most of the unit's words, in order and close
    together; None if no comment does."""
    found = _closest(unit, texts)
    return None if found is None else _differences(unit, *found)


def _nearest_elsewhere(
    unit: str, every_comment: list[tuple[int, str]], r_index: int | None
) -> tuple[int, str] | None:
    """The run game, other than the paired one, with a comment that holds most of the unit's
    words, at least 85% of them, in order and close together, and the words that differ; the game
    that holds most of them if several do; None if none does. A look-alike of a short unit, or
    a comment with words added to the unit's, is no copy of it there."""
    best: tuple[int, int, str] | None = None
    for game in sorted({i for i, _ in every_comment if i != r_index}):
        found = _closest(unit, [t for i, t in every_comment if i == game], strict=True)
        if found is not None and (best is None or found[0] > best[0]):
            best = (found[0], game, _differences(unit, *found))
    return None if best is None else (best[1], best[2])


def _closest(unit: str, texts: list[str], strict: bool = False) -> tuple[int, list[str]] | None:
    """How many of the unit's words, and which words of the comment in their place, where a
    comment holds most of them, in order and close together and near enough to count as a copy;
    None if no comment does."""
    target = unit.split()
    best, best_matched = None, 0
    for text in texts:
        words = text.split()
        matcher = difflib.SequenceMatcher(None, target, words, autojunk=False)
        blocks = [block for block in matcher.get_matching_blocks() if block.size]
        if not blocks:
            continue
        matched = sum(block.size for block in blocks)
        # The words the comment has in place of the unit's unmatched first and last words.
        start = max(0, blocks[0].b - blocks[0].a)
        end = blocks[-1].b + blocks[-1].size + len(target) - blocks[-1].a - blocks[-1].size
        window = words[start : min(len(words), end)]
        # Words found scattered through a long comment are no copy of the unit.
        if matched > best_matched and len(window) <= _widest(len(target)):
            best_matched, best = matched, window
    # Strictly, as in another game, the copy may hold no more words than the unit's that are not
    # the unit's either: a window with words added is a look-alike.
    if (
        best is not None
        and _close(len(target), best_matched)
        and not (strict and best_matched < NEAR * max(len(target), len(best)))
    ):
        return best_matched, best
    return _closest_short(unit, texts) if len(target) == 2 and not strict else None


def _closest_short(unit: str, texts: list[str]) -> tuple[int, list[str]] | None:
    """For a unit of two words, which cannot lose a word and still hold 85% of them, the words of
    a comment, one to three of them, that hold 85% of its characters, as «Rc1!Simple chess.»,
    the book's glued notation and all, in «Simple chess.»; how many of its words they hold."""
    target = unit.split()
    best, best_ratio = None, NEAR
    for text in texts:
        words = text.split()
        for start in range(len(words)):
            for size in (1, 2, 3):
                window = words[start : start + size]
                ratio = difflib.SequenceMatcher(
                    None, unit, " ".join(window), autojunk=False
                ).ratio()
                if ratio >= best_ratio and window != target:
                    best_ratio, best = ratio, window
    if best is None:
        return None
    return len(set(best) & set(target)), best


def _differences(unit: str, matched: int, window: list[str]) -> str:
    """A near copy's fault: how many of the unit's words it holds, and the words that differ."""
    target = unit.split()
    changes = []
    for op, a0, a1, b0, b1 in difflib.SequenceMatcher(
        None, target, window, autojunk=False
    ).get_opcodes():
        if op != "equal":
            changes.append(f"«{' '.join(target[a0:a1])}» → «{' '.join(window[b0:b1])}»")
    quoted = shortened(unit, QUOTED)
    return f"{matched} of {len(target)} words of «{quoted}»: " + "; ".join(changes)


def _pair(
    truth: Sequence[chess.pgn.Game], output: Sequence[chess.pgn.Game]
) -> dict[int, tuple[int, list[str], int]]:
    """Each truth game's run game, by start position, then by how far the main lines agree,
    then by how many comment words they share, with the squares where their placements
    differ. A run game that starts from no truth game's placement but from a position a truth
    game's main line reaches, as an exercise's position inside a game the book gives whole, pairs
    with that game after a full match: the third value is how many of the truth's plies come
    before the run game's start. For a truth game with moves, a run's game of text alone ranks
    after every run game with moves. A run's game of text alone may pair with more than one of
    the truth's."""
    run_words = [_comment_words(found) for found in output]
    candidates = []
    for t_index, game in enumerate(truth):
        uncertain = set(game.headers.get("Uncertain", "").split())
        words = _comment_words(game)
        for r_index, found in enumerate(output):
            shared = len(words & run_words[r_index])
            differing = _differing(game.board(), found.board(), uncertain)
            if len(differing) <= MOST_DIFFERING:
                agree = _agreeing(found, game)
                text_alone = bool(game.variations) and not found.variations
                candidates.append(
                    (
                        (text_alone, len(differing), 0, -agree, -shared, r_index),
                        t_index,
                        r_index,
                        differing,
                        0,
                    )
                )
            # An exact position the truth's main line reaches beats a start that only nearly
            # matches.
            reached = _reached(found, game) if differing else 0
            if reached:
                agree = _agreeing(_rerooted(found, game, reached), game) - reached
                candidates.append(
                    ((False, 0, 1, -agree, -shared, r_index), t_index, r_index, [], reached)
                )
    pairs: dict[int, tuple[int, list[str], int]] = {}
    used: set[int] = set()
    for _, t_index, r_index, differing, offset in sorted(candidates):
        if t_index in pairs or r_index in used:
            continue
        pairs[t_index] = (r_index, differing, offset)
        used.add(r_index)
    # A run's game of text alone may also hold another of the truth's, from the same position,
    # with at least half its words: prose the run lets run on where a game between them is lost.
    for t_index, game in enumerate(truth):
        words = _comment_words(game)
        if t_index in pairs or game.variations or not words:
            continue
        holding = [
            r_index
            for r_index, found in enumerate(output)
            if not found.variations
            and found.board().fen() == game.board().fen()
            and 2 * len(words & run_words[r_index]) >= len(words)
        ]
        if holding:
            best = max(holding, key=lambda r_index: len(words & run_words[r_index]))
            pairs[t_index] = (best, [], 0)
    return pairs


def _joined(games: Sequence[chess.pgn.Game]) -> tuple[list[chess.pgn.Game], list[int]]:
    """The games with each one that starts where the game before it ends joined on to it: its
    comment after that game's last move, its moves after them. A book that shows a diagram at
    a line's end and goes on with the analysis from it may be given as one game or two; truth
    and run alike are read joined, so that the two count the same. So too a game of text alone
    after another from the same position with the same headers, a Round a run numbers aside:
    prose that runs on in one section, one game or two. With each game, how many it joins."""
    joined: list[chess.pgn.Game] = []
    parts: list[int] = []
    for game in games:
        if (
            joined
            and not joined[-1].variations
            and not game.variations
            and (joined[-1].board().board_fen(), joined[-1].board().turn)
            == (game.board().board_fen(), game.board().turn)
            and _section(joined[-1]) == _section(game)
        ):
            joined[-1].comment = "\n".join(t for t in (joined[-1].comment, game.comment) if t)
            parts[-1] += 1
            continue
        last = joined[-1].end() if joined else None
        if (
            last is not None
            and isinstance(last, chess.pgn.ChildNode)
            and game.variations
            and (last.board().board_fen(), last.board().turn)
            == (game.board().board_fen(), game.board().turn)
        ):
            last.comment = "\n".join(text for text in (last.comment, game.comment) if text)
            _graft(game, last)
            parts[-1] += 1
            continue
        # Joined on copies: the caller's games stay as they were.
        joined.append(copy.deepcopy(game))
        parts.append(1)
    return joined, parts


def _section(game: chess.pgn.Game) -> dict[str, str]:
    """A game's headers, its Round aside, which a run may number itself."""
    return {key: value for key, value in game.headers.items() if key != "Round"}


def _reached(found: chess.pgn.Game, truth: chess.pgn.Game) -> int:
    """After how many plies the truth's main line reaches the run game's start, with the same
    side to move; 0 if it never does."""
    start = found.board()
    key = (start.board_fen(), start.turn)
    board = truth.board()
    for ply, move in enumerate(truth.mainline_moves(), start=1):
        board.push(move)
        if (board.board_fen(), board.turn) == key:
            return ply
    return 0


def _rerooted(found: chess.pgn.Game, truth: chess.pgn.Game, plies: int) -> chess.pgn.Game:
    """The run game as if it went on from the truth's first plies: the truth's start and its
    first moves, bare, then the run game's comment and tree, so that every path compares as the
    truth's does."""
    game = chess.pgn.Game()
    game.headers.update({k: v for k, v in found.headers.items() if k not in ("FEN", "SetUp")})
    if truth.headers.get("FEN"):
        game.setup(truth.board())
    node: chess.pgn.GameNode = game
    for move in list(truth.mainline_moves())[:plies]:
        node = node.add_main_variation(move)
    node.comment = found.comment
    _graft(found, node)
    return game


def _graft(source: chess.pgn.GameNode, target: chess.pgn.GameNode) -> None:
    for child in source.variations:
        copied = target.add_variation(child.move, comment=child.comment, nags=child.nags)
        copied.starting_comment = child.starting_comment
        _graft(child, copied)


def _comment_words(game: chess.pgn.Game) -> set[str]:
    return set(prose(" ".join(_unbroken(text) for _, text in _comments(game))).split())


def _agreeing(found: chess.pgn.Game, truth: chess.pgn.Game) -> int:
    """How many plies of the two main lines agree, from the start."""
    agree = 0
    for a, b in zip(found.mainline_moves(), truth.mainline_moves(), strict=False):
        if a != b:
            break
        agree += 1
    return agree


def _differing(truth: chess.Board, found: chess.Board, uncertain: set[str]) -> list[str]:
    differing = []
    for square in chess.SQUARES:
        name = chess.square_name(square)
        if name in uncertain:
            continue
        a, b = truth.piece_at(square), found.piece_at(square)
        if a != b:
            differing.append(
                f"{name} {b.symbol() if b else 'empty'} for {a.symbol() if a else 'empty'}"
            )
    return differing


def _paths(game: chess.pgn.Game) -> dict[tuple[str, ...], chess.pgn.ChildNode]:
    """Every move of the game's tree by its path from the start, as UCI moves."""
    return {_key(node): node for node in nodes(game) if isinstance(node, chess.pgn.ChildNode)}


def _key(node: chess.pgn.GameNode) -> tuple[str, ...]:
    """A node's path from the start, as UCI moves."""
    moves = []
    while isinstance(node, chess.pgn.ChildNode):
        moves.append(node.move.uci())
        node = node.parent
    return tuple(reversed(moves))


def _variations(game: chess.pgn.Game) -> list[tuple[list[chess.pgn.ChildNode], int | None]]:
    """Each variation of the game's tree, as its moves from where it leaves its parent line to
    its end, with the index of the move from which it is optional, if it is."""
    found = []

    def walk(start: chess.pgn.ChildNode, main: bool) -> None:
        line = [start]
        node = start
        while True:
            for alternative in node.variations[1:]:
                walk(alternative, False)
            if not node.variations:
                break
            node = node.variations[0]
            line.append(node)
        if not main:
            optional = next((i for i, n in enumerate(line) if _optional(n)), None)
            found.append((line, optional))

    if game.variations:
        for alternative in game.variations[1:]:
            walk(alternative, False)
        walk(game.variations[0], True)
    return found


def _optional(node: chess.pgn.GameNode) -> bool:
    """Whether the truth marks this move optional, or a move before it on its line, or the
    move its line is in place of."""
    while node.parent is not None:
        if OPTIONAL in node.comment:
            return True
        main = node.parent.variations[0]
        if main is not node:
            return _optional(main)
        node = node.parent
    return False


def _plays(
    game: chess.pgn.Game, kinds: tuple[str, ...] = ("moves", "continuation")
) -> list[tuple[chess.pgn.ChildNode, list[str]]]:
    """Each play the truth keeps in words after a move, with the moves a run may give for it
    instead, as SAN from the position after that move; by default those marked moves or
    continuation, or only those of the kinds given."""
    return [
        (node, match[2].split())
        for node in nodes(game)
        if isinstance(node, chess.pgn.ChildNode)
        for match in ACCEPT.finditer(node.comment)
        if match[1] in kinds
    ]


def _continuations(node: chess.pgn.GameNode) -> list[list[chess.Move]]:
    """The play the truth marks on a move as [%accept continuation SAN...], each as moves from
    the position after that move."""
    if not isinstance(node, chess.pgn.ChildNode):
        return []
    found = []
    for match in ACCEPT.finditer(node.comment):
        if match[1] == "continuation":
            board = node.board()
            found.append([board.push_san(san) for san in match[2].split()])
    return found


def _main_lines(truth: chess.pgn.Game) -> list[list[chess.Move]]:
    """The main lines with which a run's is exact: the truth's, and the truth's followed by all
    the play it marks on its last move as [%accept continuation ...]."""
    main = list(truth.mainline_moves())
    return [main, *(main + play for play in _continuations(truth.end()))]


def _main_instead(
    truth: chess.pgn.Game, run_main: list[chess.Move]
) -> tuple[list[chess.pgn.ChildNode], list[list[chess.Move]]] | None:
    """Where a run's main line follows the truth's, and at least once leaves it by a variation
    the truth marks [%accept main], and so on from that variation: each variation so taken, and
    the main lines through them as the truth gives them, the path followed and then the truth's
    own course to the end of the line it is then on, and that followed by the play the truth
    marks on that end as [%accept continuation ...]. None where the run's takes no such
    variation. A deviation onto anything unmarked ends the following, and the line stays what
    it was."""
    node: chess.pgn.GameNode = truth
    path: list[chess.Move] = []
    taken: list[chess.pgn.ChildNode] = []
    for move in run_main:
        if not node.variations:
            break
        if move == node.variations[0].move:
            node = node.variations[0]
        else:
            marked = next(
                (v for v in node.variations[1:] if v.move == move and ACCEPT_MAIN in v.comment),
                None,
            )
            if marked is None:
                break
            taken.append(marked)
            node = marked
        path.append(move)
    if not taken:
        return None
    while node.variations:
        node = node.variations[0]
        path.append(node.move)
    return taken, [path, *(path + play for play in _continuations(node))]


def _follow(game: chess.pgn.Game, path: Sequence[str]) -> chess.pgn.GameNode | None:
    """The node a path of UCI moves reaches in the game's tree, taking at each step the first
    child with that move; None where the tree has no such node."""
    node: chess.pgn.GameNode = game
    for uci in path:
        child = next((c for c in node.variations if c.move.uci() == uci), None)
        if child is None:
            return None
        node = child
    return node


def _beyond_main(truth: chess.pgn.Game, found: chess.pgn.Game) -> list[chess.pgn.ChildNode]:
    """Where the run's tree has the truth's main line, however given, the moves it goes on with
    from that line's end, along each first child, unless that is exactly one of the plays the
    truth marks on the end as [%accept continuation ...]; else none."""
    end = _follow(found, _key(truth.end()))
    if end is None:
        return []
    beyond: list[chess.pgn.ChildNode] = []
    node = end
    while node.variations:
        node = node.variations[0]
        beyond.append(node)
    return [] if [n.move for n in beyond] in _continuations(truth.end()) else beyond


def _has(found: chess.pgn.Game, agree: int) -> str:
    """The run's main-line moves after the plies that agree, for a fault."""
    run_moves = list(found.mainline())[agree : agree + 4]
    return f"the run has {_line_text(run_moves)}" if run_moves else "the run's stops there"


def _play_keys(node: chess.pgn.ChildNode, play: list[str]) -> list[tuple[str, ...]]:
    """The paths of a play's moves, each from the start, as UCI moves."""
    board, key, keys = node.board(), _key(node), []
    for san in play:
        move = board.parse_san(san)
        key = (*key, move.uci())
        keys.append(key)
        board.push(move)
    return keys


def _accepted_nags(node: chess.pgn.GameNode) -> list[set[int]]:
    """The sets of NAGs the truth accepts on a move besides its own."""
    return [
        {int(n.lstrip("$")) for n in match[2].split()}
        for match in ACCEPT.finditer(node.comment)
        if match[1] == "nag"
    ]


def _notation(text: str) -> list[str]:
    """The moves a text gives as notation, in order: each without its number and marks, a
    figurine as its piece's letter, castling with letters."""
    found = []
    for token in COMMAND.sub(" ", text).translate(TYPOGRAPHY).split():
        if match := MOVE.fullmatch(token):
            found.append((match["body"] + (match["promotion"] or "")).replace("0", "O"))
    return found


def _holds_play(said: list[str], play: list[str]) -> bool:
    """Whether moves given as notation hold a play's moves, one after another."""
    wanted = _notation(" ".join(play))
    return any(said[i : i + len(wanted)] == wanted for i in range(len(said) - len(wanted) + 1))


ALPHA = re.compile(r"[^\W\d_]")


def _start_positions(truth: chess.pgn.Game, line: str) -> set[str]:
    """The positions, as EPD, before the numbered move a line of notation begins with, in the
    truth's tree: those where the move number and the side to move are the line's; none if its
    first move has no number."""
    pending = ""
    for token in COMMAND.sub(" ", line).translate(TYPOGRAPHY).split():
        if re.fullmatch(r"\d+\.{1,3}", token):
            pending = token
            continue
        match = MOVE.fullmatch(token)
        number = (match["number"] if match else None) or pending
        if match and number:
            digits = int(number.rstrip("."))
            turn = chess.BLACK if "..." in number else chess.WHITE
            return {
                node.board().epd()
                for node in nodes(truth)
                if node.board().fullmove_number == digits and node.board().turn == turn
            }
        if match or ALPHA.search(token):
            break
        pending = ""
    return set()


def _in_tree(game: chess.pgn.Game, moves: list[str], starts: set[str]) -> bool:
    """Whether the game's tree has the moves, as _notation gives them, one after another along a
    line, from a position of starts (EPD)."""

    def follows(node: chess.pgn.GameNode, wanted: list[str]) -> bool:
        if not wanted:
            return True
        return any(
            _notation(node.board().san(child.move))[:1] == wanted[:1] and follows(child, wanted[1:])
            for child in node.variations
        )

    return any(follows(node, moves) for node in nodes(game) if node.board().epd() in starts)


def _in_words(play: list[str], game: chess.pgn.Game) -> bool:
    """Whether a comment of the game holds a play's moves as notation, in order."""
    return any(_holds_play(_notation(text), play) for _, text in _comments(game))


def _rest_of_line(node: chess.pgn.ChildNode) -> list[chess.pgn.ChildNode]:
    """The node and the moves after it on its line."""
    line = [node]
    while line[-1].variations:
        line.append(line[-1].variations[0])
    return line


def _describe(line: list[chess.pgn.ChildNode]) -> str:
    """A line's moves with their numbers, and the move it is in place of or comes after."""
    text = _line_text(line)
    first = line[0]
    parent = first.parent
    main = parent.variations[0]
    if main is not first:
        return f"{text} (in place of {_line_text([main])})"
    if isinstance(parent, chess.pgn.ChildNode):
        return f"{text} (after {_line_text([parent])})"
    return text


def _line_text(line: Sequence[chess.pgn.ChildNode]) -> str:
    parts = []
    for index, node in enumerate(line):
        board = node.parent.board()
        san = board.san(node.move)
        if board.turn == chess.WHITE:
            parts.append(f"{board.fullmove_number}.{san}")
        elif index == 0:
            parts.append(f"{board.fullmove_number}...{san}")
        else:
            parts.append(san)
    return " ".join(parts)


def _comments(game: chess.pgn.Game) -> list[tuple[tuple[str, ...], str]]:
    """Every comment of the game, with the path of moves after which it stands."""
    found: list[tuple[tuple[str, ...], str]] = [((), game.comment)] if game.comment else []
    stack = list(reversed(game.variations))
    while stack:
        node = stack.pop()
        key = _key(node)
        if node.starting_comment:
            found.append((key[:-1], node.starting_comment))
        if node.comment:
            found.append((key, node.comment))
        stack.extend(reversed(node.variations))
    return found


def _at_place(
    held: tuple[str, ...],
    where: tuple[str, ...],
    truth: chess.pgn.Game,
    tree: dict[tuple[str, ...], chess.pgn.ChildNode],
    known: set[tuple[str, ...]],
    slack: int,
) -> bool:
    """Whether a run's comment at held is at the place of the truth's at where: the same place;
    a variation in place of the move that leads there, or that move where the truth's is in a
    variation, but not one variation for another; a variation in place of the truth's next
    move; at most slack moves further along the truth's line; or, where the truth's line ends
    at where, on moves the run plays on from that end, as play the book gives after a line's
    last move that a run builds as moves where the truth keeps it in words. The paths known
    are the truth tree's and those of the moves a run may give for play the truth keeps in
    words."""
    if held == where:
        return True
    end = truth if not where else tree.get(where)
    if (
        held not in known
        and len(held) > len(where)
        and held[: len(where)] == where
        and end is not None
        and not end.variations
    ):
        return True
    if held not in known:
        return False
    size = len(where)
    # Of two moves in place of each other, one must be the line's own: a comment in another
    # variation than the truth's, where both are variations, is in the wrong line.
    parent = truth if size == 1 else tree.get(where[:-1])
    own = parent.variations[0].move.uci() if parent is not None and parent.variations else None
    in_place_of_move_there = (
        size > 0
        and len(held) >= size
        and held[: size - 1] == where[:-1]
        and held[size - 1] != where[-1]
        and own in (held[size - 1], where[-1])
    )
    if in_place_of_move_there:
        return True
    if len(held) <= size or held[:size] != where:
        return False
    node = truth if not where else tree.get(where)
    following = node.variations[0].move.uci() if node is not None and node.variations else None
    return held[size] != following or len(held) - size <= slack


def _place(where: tuple[str, ...], tree: dict[tuple[str, ...], chess.pgn.ChildNode]) -> str:
    return f"after {_line_text([tree[where]])}" if where else "before the first move"


def _label(game: chess.pgn.Game, index: int) -> str:
    return game.headers.get("Item") or f"Truth game {index + 1}"


def _side(game: chess.pgn.Game) -> str:
    return "White" if game.board().turn == chess.WHITE else "Black"


def _claude_version(run: Path, command: dict[str, Any]) -> str | None:
    """The Claude Code release the session reported, else the one its launcher recorded."""
    record = run / "session.json"
    phases = json.loads(record.read_text()).get("phases", []) if record.exists() else []
    reported = [(phase.get("init") or {}).get("claude_code_version") for phase in phases]
    return next((version for version in reported if version), command.get("claude_version"))


def _session(run: Path) -> dict[str, Any]:
    """What the session log says of a run, over its main conversation and its subagents: wall
    time, requests, tokens, thinking, compactions, cost at list price, the advisor's share of
    the cost, tool calls, build_pgn's outcomes, and API requests retried or failed; each agent's
    own account; and the final answer."""
    command = json.loads((run / "command.json").read_text())
    accounts = agents(run)
    if not any(account["requests"] for account in accounts):
        raise ValueError(f"no model's message in {run}'s session log")
    main = accounts[0]
    totals: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    builds: Counter[str] = Counter()
    cost: float | None = 0.0
    advisor_cost: float | None = 0.0
    for account in accounts:
        for kind in (*TOKENS, "requests", "compactions", "api_retries", "api_errors"):
            totals[kind] += account[kind]
        totals["tool_errors"] += account["tool_errors"]
        tools.update(account["tool_calls"])
        builds.update(account["build_pgn"])
        advice: dict[str, Any] = account.get("advisor") or {"calls": 0, "cost_list_usd": 0.0}
        totals["advisor_calls"] += int(advice["calls"])
        advisor_cost = _added(advisor_cost, advice["cost_list_usd"])
        cost = _added(cost, account["cost_list_usd"])
    cost = _added(cost, advisor_cost)
    ended = max((account["ended"] for account in accounts if account["ended"]), default=None)
    return {
        "model": command.get("model"),
        "claude_version": _claude_version(run, command),
        "wall_seconds": _seconds(main["started"], ended),
        "requests": totals["requests"],
        **{kind: totals[kind] for kind in TOKENS},
        "compactions": totals["compactions"],
        "cost_list_usd": round(cost, 4) if cost is not None else None,
        "client_cost_usd": main["client_cost_usd"],
        "advisor_calls": totals["advisor_calls"],
        "advisor_cost_list_usd": round(advisor_cost, 4) if advisor_cost is not None else None,
        "subagents": len(accounts) - 1,
        "tool_calls": dict(tools),
        "build_pgn": dict(builds),
        "tool_errors": totals["tool_errors"],
        "api_retries": totals["api_retries"],
        "api_errors": totals["api_errors"],
        "agents": [
            {key: value for key, value in account.items() if key != "contexts"}
            for account in accounts
        ],
        "final_answer": main["report"],
    }


def agents(run: Path) -> list[dict[str, Any]]:
    """Each agent of a run's session, from its log: the main conversation, from the prompt or
    request sent, then each subagent, by when it started, with its type and task as Claude Code
    recorded them. For each: its models, requests, tokens, cost at list price, the advisor's
    calls, advice, tokens and cost apart, tool calls, build_pgn's outcomes, the context of each
    request, compactions, API requests retried or failed, when it started and ended, and its
    last text."""
    projects = run / "claude-config" / "projects"
    # The prompt or request, pasted or passed: the session starts when it is sent, not with a
    # command such as /login typed before it.
    sent = next((run / name for name in ("prompt.md", "request.md") if (run / name).exists()), None)
    first_line = sent.read_text().strip().split("\n")[0] if sent else ""
    logs = sorted(projects.glob("**/*.jsonl"))
    sub = [log for log in logs if "subagents" in log.relative_to(projects).parts]
    # The main conversation's logs first, so that the messages a fork's log repeats from it,
    # such as the one that started the fork, count as the main conversation's.
    counted: set[str] = set()
    mains = []
    for log in (log for log in logs if log not in sub):
        account = _agent(log, first_line, counted)
        account["agent"] = "main" if not mains else f"main {len(mains) + 1}"
        mains.append(account)
    subagents = []
    for log in sub:
        account = _agent(log, "", counted)
        meta = _meta(log)
        account["agent"] = log.stem.removeprefix("agent-")
        account["type"] = meta.get("agentType")
        account["task"] = meta.get("description")
        subagents.append(account)
    subagents.sort(key=lambda account: account["started"] or "")
    return [*mains, *subagents]


def _meta(log: Path) -> dict[str, Any]:
    """What Claude Code recorded of a subagent beside its log, if anything."""
    path = log.with_suffix(".meta.json")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _agent(log: Path, first_line: str, counted: set[str]) -> dict[str, Any]:
    """One agent's account of its log, as agents gives it; a request already counted, by its
    message's id, is not counted again."""
    usages: dict[str, tuple[str, dict[str, Any]]] = {}
    calls: dict[str, dict[str, Any]] = {}
    texts: list[tuple[str, str]] = []
    advice: list[str] = []
    advisor_calls = compactions = api_retries = api_errors = 0
    started = ended = client = None
    # Lines end only at "\n": the log keeps other line separators as they are, inside its JSON.
    for line in log.read_text(errors="replace").split("\n"):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = entry.get("type")
        stamp = entry.get("timestamp")
        if kind == "cost-state":
            client = entry
        if kind == "system":
            compactions += entry.get("subtype") == "compact_boundary"
            # A request that failed and that Claude Code retried.
            api_retries += entry.get("subtype") == "api_error"
        message = entry.get("message") if isinstance(entry.get("message"), dict) else None
        if not message:
            continue
        if (
            kind == "user"
            and started is None
            and not entry.get("isMeta")
            and (not first_line or first_line in _text(message.get("content")))
        ):
            started = stamp
        if kind == "assistant":
            key = message.get("id") or line
            if key in counted and key not in usages:
                continue
            # Claude Code's own messages, such as an API error's, are no request of a model.
            if message.get("model") == "<synthetic>":
                api_errors += bool(entry.get("isApiErrorMessage"))
            else:
                counted.add(key)
                usage = message.get("usage") or {}
                model, before = usages.get(key, (str(message.get("model")), {}))
                usages[key] = (model, final_usage(before, usage))
            ended = stamp
            for block in message.get("content") or []:
                if block.get("type") == "tool_use" and block["id"] not in calls:
                    calls[block["id"]] = {
                        "name": block["name"].removeprefix(TOOL_PREFIX),
                        "input": block["input"],
                    }
                elif block.get("type") == "server_tool_use" and block.get("name") == "advisor":
                    advisor_calls += 1
                elif block.get("type") == "advisor_tool_result":
                    advice.append(_text(block.get("content")))
                elif block.get("type") == "text" and block.get("text", "").strip():
                    texts.append((key, block["text"]))
        if kind == "user" and isinstance(message.get("content"), list):
            for block in message["content"]:
                if block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                    calls[block["tool_use_id"]]["result"] = _text(block.get("content"))
                    calls[block["tool_use_id"]]["is_error"] = bool(block.get("is_error"))
    # A call Claude Code refused for its name, such as a pgnify tool's without its prefix.
    for call in calls.values():
        if "No such tool available" in call.get("result", ""):
            call["name"] += ", no such tool"
    totals: Counter[str] = Counter()
    advised: Counter[str] = Counter()
    advisor_models: set[str] = set()
    cost: float | None = 0.0
    advisor_cost: float | None = 0.0
    contexts = []
    for model, usage in usages.values():
        for kind, count in _tokens(usage).items():
            totals[kind] += count
        cost = _added(cost, usage_cost(model, usage))
        contexts.append(_context(usage))
        for advice_pass in advisor_passes(usage):
            advisor_models.add(str(advice_pass.get("model")))
            advised.update(_tokens(advice_pass))
            advisor_cost = _added(
                advisor_cost, usage_cost(str(advice_pass.get("model")), advice_pass)
            )
    # The last text: the text of the agent's last message that has any, all its blocks.
    report = "\n\n".join(text for id_, text in texts if id_ == texts[-1][0]) if texts else None
    account: dict[str, Any] = {
        "agent": None,
        "type": None,
        "task": None,
        "models": sorted({model for model, _ in usages.values()}),
        "started": started,
        "ended": ended,
        "seconds": _seconds(started, ended),
        "requests": len(usages),
        **{kind: totals[kind] for kind in TOKENS},
        "cost_list_usd": round(cost, 4) if cost is not None else None,
        "peak_context": max(contexts, default=0),
        "contexts": contexts,
        "compactions": compactions,
        "api_retries": api_retries,
        "api_errors": api_errors,
        "tool_calls": dict(Counter(call["name"] for call in calls.values())),
        "build_pgn": dict(
            Counter(_build_outcome(call) for call in calls.values() if call["name"] == "build_pgn")
        ),
        "tool_errors": sum(call.get("is_error", False) for call in calls.values()),
        # Claude Code's own account of the session's cost, by its prices, for comparison.
        "client_cost_usd": client.get("totalCostUSD") if client else None,
        "report": report,
    }
    if advisor_calls or advised:
        account["advisor"] = {
            "calls": advisor_calls,
            "models": sorted(advisor_models),
            **{kind: advised[kind] for kind in TOKENS},
            "cost_list_usd": round(advisor_cost, 4) if advisor_cost is not None else None,
            "advice": advice,
        }
    return account


def request_cost(model: str, usage: dict[str, Any]) -> float | None:
    """A request's whole cost at list price, in dollars: its model's tokens and the advisor's
    passes, each at its own model's price; None if one of them has no price."""
    cost = usage_cost(model, usage)
    for advice_pass in advisor_passes(usage):
        cost = _added(cost, usage_cost(str(advice_pass.get("model")), advice_pass))
    return cost


def advisor_passes(usage: dict[str, Any]) -> list[dict[str, Any]]:
    """The advisor's passes in a request, which its usage lists apart from its model's, each
    with the advisor's model and tokens."""
    return [
        iteration
        for iteration in usage.get("iterations") or []
        if isinstance(iteration, dict) and iteration.get("type") == "advisor_message"
    ]


def _added(total: float | None, cost: float | None) -> float | None:
    """A sum of costs, None once one of them is."""
    return total + cost if total is not None and cost is not None else None


def usage_cost(model: str, usage: dict[str, Any]) -> float | None:
    """The cost at list price, in dollars, of one request's tokens as its usage gives them, by
    the model's price, or its long-prompt price for a prompt past its threshold: cache writes at
    the five-minute or one-hour rate as the usage splits them, else at the one-hour rate, the
    higher; None for a model without a price."""
    price = next((listed for name, listed in PRICES.items() if model.startswith(name)), None)
    if price is None:
        return None
    tokens = _tokens(usage)
    long = next((rule for name, rule in LONG_PROMPT.items() if model.startswith(name)), None)
    prompt = tokens["input_tokens"] + tokens["cache_read_tokens"] + tokens["cache_write_tokens"]
    if long and prompt > long[0]:
        price = long[1]
    split = usage.get("cache_creation")
    one_hour = tokens["cache_write_1h_tokens"] if split else tokens["cache_write_tokens"]
    return (
        tokens["input_tokens"] * price.input
        + tokens["output_tokens"] * price.output
        + tokens["cache_read_tokens"] * price.cache_read
        + (tokens["cache_write_tokens"] - one_hour) * price.cache_write_5m
        + one_hour * price.cache_write_1h
    ) / 1e6


def _tokens(usage: dict[str, Any]) -> dict[str, int]:
    """A request's token counts, as TOKENS names them."""
    split = usage.get("cache_creation") or {}
    return {
        "input_tokens": usage.get("input_tokens") or 0,
        "cache_read_tokens": usage.get("cache_read_input_tokens") or 0,
        "cache_write_tokens": usage.get("cache_creation_input_tokens") or 0,
        "cache_write_1h_tokens": split.get("ephemeral_1h_input_tokens") or 0,
        "output_tokens": usage.get("output_tokens") or 0,
        "thinking_tokens": (usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0,
    }


def final_usage(before: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    """Of two entries' usage for one request, which the log may record in parts, the final one:
    the one with more output, then with more tokens. Ollama's first entries of a request count
    its input from the request's size, the data of its pictures included, many times what the
    model read; its final entry counts what the model read."""

    def order(entry: dict[str, Any]) -> tuple[int, int]:
        tokens = _tokens(entry)
        return tokens["output_tokens"], sum(tokens.values())

    return usage if order(usage) >= order(before) else before


def _context(usage: dict[str, Any]) -> int:
    """The tokens of context a request sent: of its model's last pass, when the advisor or a
    compaction made several."""
    passes = [
        iteration
        for iteration in usage.get("iterations") or []
        if isinstance(iteration, dict) and iteration.get("type") == "message"
    ]
    last = passes[-1] if passes else usage
    tokens = _tokens(last)
    return tokens["input_tokens"] + tokens["cache_read_tokens"] + tokens["cache_write_tokens"]


def _text(content: Any) -> str:
    """The text of a message's or a block's content: a string, or the texts of its parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _text(content.get("text") or content.get("content"))
    if isinstance(content, list):
        return " ".join(_text(part) for part in content)
    return ""


def _build_outcome(call: dict[str, Any]) -> str:
    text = call.get("result", "")
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # "not JSON" is build_pgn's earlier message, "spec is text that is not JSON", in
        # recorded runs.
        if "JSON is malformed" in text or "not JSON" in text:
            return "unreadable JSON"
        if "game's name" in text:
            return "bad name"
        return "refused for its shape"
    if result.get("errors"):
        return "with errors"
    return "saved" if result.get("saved") else "built"


def _seconds(started: str | None, ended: str | None) -> float | None:
    if not started or not ended:
        return None
    elapsed = datetime.fromisoformat(ended) - datetime.fromisoformat(started)
    return round(elapsed.total_seconds(), 1)


if __name__ == "__main__":
    main()
