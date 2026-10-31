"""Score runs against their slice's truth (experiments/slices.toml), from each run's
output PGN alone, and list every fault, for review against the run's final answer.

- Games: each truth game is paired with the run's game that starts from its placement, squares
  the truth marks uncertain left out (on a scan, squares too dark to read), else with the one
  whose start differs in the fewest squares, at most four, reported as such; of several, the one
  whose main line agrees longest, then the one sharing most comment words. The side to move is
  compared. Run games paired with no truth game are listed.
- Moves: the main line, exact or the plies that agree. Each variation of the truth's tree, from
  where it leaves its parent line to its end, is kept, kept in part or lost according to which
  of its moves the run's tree has at the same path from the start, as main line or variation. A
  line is optional from the move whose comment the truth marks [%optional]. Moves of the run on
  no path of the truth are unsupported, each listed from its first move.
- Commentary: the truth's comments cut into prose units, the words between move notation, with
  typography and spacing made uniform. A unit is verbatim when the paired game's comments hold
  it word for word, in one comment or split between comments; near when one holds at least 85%
  of its words in order, or all but one of three or more (the differences listed); and
  elsewhere when another game holds it. A unit in one comment is attached when the comment is
  at the unit's place: after the same move; within a variation in place of that move or of the
  next; up to as many moves further along the truth's line as the truth's comment gives before
  the unit or right after it; or, for the words before a game's first move, in a game of text
  alone just before it.
- Words of the run's comments outside the truth's units: those the source has, such as running
  heads and page numbers, are counted; those it lacks are listed as unsupported.
- NAGs on the moves the run shares with the truth.
- Running: from the session log, wall time, requests, tokens, thinking, compactions, cost at
  list price, and the tool calls, build_pgn's by outcome, over the main conversation and its
  subagents; each agent's share apart, and the advisor's; and the final answer.

Writes score.json and faults.md into each run's folder and prints a summary.

    uv run python experiments/score_slice.py SLICE RUN_DIR...
"""

import argparse
import difflib
import json
import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

import chess
import chess.pgn
import session

from pgnify.chess_tools import nodes, read_games
from pgnify.prose import (
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
NEAR = 0.85
# The prefix Claude Code gives the names of pgnify's tools.
TOOL_PREFIX = "mcp__pgnify__"
# The most squares in which a run game's start may differ from a truth game's for the two to pair.
MOST_DIFFERING = 4
# How many words a fault quotes of a truth unit, and of the words the source lacks.
QUOTED = 12
QUOTED_UNSUPPORTED = 30


class Price(NamedTuple):
    """Dollars per million tokens at list price."""

    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float


# As Claude Code's model catalog gives them (2.1.314), which its own cost accounting uses: the
# sessions' model, and those a subagent or the advisor may run on.
PRICES = {
    "claude-sonnet-5-5": Price(2.0, 10.0, 0.10, 2.50, 4.0),
    "claude-opus-5-5": Price(4.0, 20.0, 0.20, 5.0, 8.0),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, 0.125, 0.20),
    "claude-fable-5-1": Price(10.0, 50.0, 0.25, 12.5, 20.0),
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
    for run_dir in args.run_dirs:
        score, faults = score_run(run_dir, truth, source)
        (run_dir / "score.json").write_text(json.dumps(score, indent=1))
        (run_dir / "faults.md").write_text(faults)
        print(json.dumps(score))


def score_run(
    run: Path, truth: Sequence[chess.pgn.Game], source: BookProse
) -> tuple[dict[str, Any], str]:
    """A run's score, and its faults as a page for the review."""
    output = [
        game for pgn in sorted((run / "work").glob("*.pgn")) for game in read_games(pgn.read_text())
    ]
    score, faults = score_output(truth, output, source)
    running = _session(run)
    answer = running.pop("final_answer")
    score.update(running)
    lines = [f"# Faults: {run.relative_to(ROOT) if run.is_relative_to(ROOT) else run}", ""]
    lines += faults
    lines += ["", "## Running", ""]
    lines += running_lines(running)
    lines += ["", "## Final answer", "", answer or "(none)", ""]
    return score, "\n".join(lines)


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
    pairs = _pair(truth, output)
    faults: list[str] = []
    counts: Counter[str] = Counter()
    every_comment = [
        (index, prose(text)) for index, game in enumerate(output) for _, text in _comments(game)
    ]
    for t_index, game in enumerate(truth):
        label = _label(game, t_index)
        counts["truth_games"] += 1
        r_index, differing = pairs.get(t_index, (None, []))
        found = output[r_index] if r_index is not None else None
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
        _score_moves(label, game, found, counts, faults)
        before = output[r_index - 1] if r_index else None
        text_before = before if before is not None and not before.variations else None
        _score_comments(label, game, found, r_index, text_before, every_comment, counts, faults)
    paired = {r_index for r_index, _ in pairs.values()}
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
    return dict(counts), faults


def _score_moves(
    label: str,
    truth: chess.pgn.Game,
    found: chess.pgn.Game | None,
    counts: Counter[str],
    faults: list[str],
) -> None:
    truth_paths = _paths(truth)
    run_paths = _paths(found) if found is not None else {}
    main = list(truth.mainline())
    counts["truth_plies"] += len(main)
    if found is not None:
        agree = _agreeing(found, truth)
        counts["main_line_plies_agreeing"] += agree
        exact = list(found.mainline_moves()) == list(truth.mainline_moves())
        counts["main_lines_exact"] += exact
        if not exact:
            run_moves = list(found.mainline())[agree : agree + 4]
            has = f"the run has {_line_text(run_moves)}" if run_moves else "the run's stops there"
            faults.append(f"- {label}: main line differs after {agree} of {len(main)} plies: {has}")
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
    for key, node in run_paths.items():
        if key in truth_paths:
            continue
        if key[:-1] in run_paths and key[:-1] not in truth_paths:
            continue  # within an unsupported line already listed from its first move
        counts["unsupported_lines"] += 1
        faults.append(f"- {label}: unsupported moves: {_describe(_rest_of_line(node))}")
    for key, node in truth_paths.items():
        counts["truth_nags"] += bool(node.nags)
        if key not in run_paths:
            continue
        run_nags = run_paths[key].nags
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
    every_comment: list[tuple[int, str]],
    counts: Counter[str],
    faults: list[str],
) -> None:
    run_comments = (
        [(where, prose(text)) for where, text in _comments(found)] if found is not None else []
    )
    joined = " ".join(text for _, text in run_comments)
    before = (
        " ".join(prose(text) for _, text in _comments(text_before))
        if text_before is not None
        else ""
    )
    tree = _paths(truth)
    for where, text in _comments(truth):
        place = _place(where, tree)
        for unit, slack in _units(text):
            words = len(unit.split())
            quoted = shortened(unit, QUOTED)
            counts["truth_units"] += 1
            counts["truth_words"] += words
            held = [w for w, t in run_comments if _holds(t, unit)]
            in_text_before = not where and _holds(before, unit)
            if held or in_text_before:
                counts["units_verbatim"] += 1
                counts["words_verbatim"] += words
                if in_text_before or any(_at_place(w, where, truth, tree, slack) for w in held):
                    counts["units_attached"] += 1
                else:
                    faults.append(f"- {label}: words not at their place, {place}: «{quoted}»")
                continue
            if _holds(joined, unit):
                counts["units_verbatim"] += 1
                counts["words_verbatim"] += words
                counts["units_split"] += 1
                faults.append(f"- {label}: words split between comments, {place}: «{quoted}»")
                continue
            near = _nearest(unit, [t for _, t in run_comments])
            if near is not None:
                counts["units_near"] += 1
                faults.append(f"- {label}: words near but not verbatim, {place}: {near}")
                continue
            elsewhere = [i for i, t in every_comment if i != r_index and _holds(t, unit)]
            if elsewhere:
                counts["units_elsewhere"] += 1
                faults.append(
                    f"- {label}: words in run game {elsewhere[0] + 1} instead: «{quoted}»"
                )
                continue
            counts["units_missing"] += 1
            faults.append(f"- {label}: words missing, {place}: «{quoted}»")


def _holds(text: str, unit: str) -> bool:
    """Whether the text holds the unit word for word, from the start of a word to the end of
    one, punctuation after it allowed."""
    return re.search(r"(?<!\S)" + re.escape(unit) + r"(?=[\s.,;:!?'\"]|$)", text) is not None


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
    known = [
        bare(unit).split()
        for unit in {unit for game in truth for _, t in _comments(game) for unit, _ in _units(t)}
    ]
    for index, game in enumerate(output):
        tree = _paths(game)
        for where, text in _comments(game):
            for stretch in _uncovered(text, known):
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
    if best is None or not _close(len(target), best_matched):
        return None
    changes = []
    for op, a0, a1, b0, b1 in difflib.SequenceMatcher(
        None, target, best, autojunk=False
    ).get_opcodes():
        if op != "equal":
            changes.append(f"«{' '.join(target[a0:a1])}» → «{' '.join(best[b0:b1])}»")
    quoted = shortened(unit, QUOTED)
    return f"{best_matched} of {len(target)} words of «{quoted}»: " + "; ".join(changes)


def _pair(
    truth: Sequence[chess.pgn.Game], output: Sequence[chess.pgn.Game]
) -> dict[int, tuple[int, list[str]]]:
    """Each truth game's run game, by start position, then by how far the main lines agree,
    then by how many comment words they share, with the squares where their placements
    differ."""
    run_words = [_comment_words(found) for found in output]
    candidates = []
    for t_index, game in enumerate(truth):
        uncertain = set(game.headers.get("Uncertain", "").split())
        words = _comment_words(game)
        for r_index, found in enumerate(output):
            differing = _differing(game.board(), found.board(), uncertain)
            if len(differing) > MOST_DIFFERING:
                continue
            agree = _agreeing(found, game)
            shared = len(words & run_words[r_index])
            candidates.append(
                ((len(differing), -agree, -shared, r_index), t_index, r_index, differing)
            )
    pairs: dict[int, tuple[int, list[str]]] = {}
    used: set[int] = set()
    for _, t_index, r_index, differing in sorted(candidates):
        if t_index in pairs or r_index in used:
            continue
        pairs[t_index] = (r_index, differing)
        used.add(r_index)
    return pairs


def _comment_words(game: chess.pgn.Game) -> set[str]:
    return set(prose(" ".join(text for _, text in _comments(game))).split())


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
    slack: int,
) -> bool:
    """Whether a run's comment at held is at the place of the truth's at where: the same place;
    a variation in place of the move that leads there or of the truth's next move; or at most
    slack moves further along the truth's line."""
    if held == where:
        return True
    if held not in tree:
        return False
    size = len(where)
    in_place_of_move_there = (
        size > 0
        and len(held) >= size
        and held[: size - 1] == where[:-1]
        and held[size - 1] != where[-1]
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
        "claude_version": command.get("claude_version"),
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
                usages[key] = (model, larger_usage(before, usage))
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
    the model's price: cache writes at the five-minute or one-hour rate as the usage splits them,
    else at the one-hour rate, the higher; None for a model without a price."""
    price = next((listed for name, listed in PRICES.items() if model.startswith(name)), None)
    if price is None:
        return None
    tokens = _tokens(usage)
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


def larger_usage(before: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    """Of two entries' usage for one request, which the log may record in parts, the one with
    more tokens, so that a request counts at its highest."""
    return usage if sum(_tokens(usage).values()) >= sum(_tokens(before).values()) else before


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
