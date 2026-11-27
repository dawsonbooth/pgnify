"""Tests of building a game in parts: build_pgn's after."""

import re
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from support import BLACK_FORK, write_text_epub

from pgnify import copies
from pgnify.chess_tools import build_pgn, read_games, write_pgn
from pgnify.doc_tools import lines_of

PASSAGES = (
    "<p>White opens with the king pawn and Black copies it.</p>"
    '<p>Then 2 <img src="board.png" alt="N"/> c3 develops the knight at once.</p>'
    '<p>After that Black replies 2...<img src="board.png" alt="N"/> f6 in kind.</p>'
    "<p>White gambits with 3.f4 exf4 and the game is open.</p>"
    "<p>The quiet 2.Nf3 is the main alternative.</p>"
)
OPENS = {"page": 1, "from": "White opens", "to": "copies it."}
DEVELOPS = {"page": 1, "from": "Then 2", "to": "at once."}
REPLIES = {"page": 1, "from": "After that", "to": "in kind."}
GAMBIT = {"page": 1, "from": "White gambits", "to": "is open."}
QUIET = {"page": 1, "from": "The quiet", "to": "alternative."}


@pytest.fixture(scope="module")
def book(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An EPUB of one page of prose with two figurines among its moves."""
    return write_text_epub(tmp_path_factory.mktemp("epub") / "figurines.epub", PASSAGES)


def _picture(book: Path) -> str:
    """The id of the figurine the book prints, which is the same picture throughout."""
    with pymupdf.open(book) as doc:
        (identifier,) = set(
            re.findall(r"\[picture \d+: ([0-9a-f]{6})\]", " ".join(lines_of(doc, 1)))
        )
    return identifier


class Games:
    """A folder of saved games and the figurines record, as a server keeps them for one book."""

    def __init__(self, folder: Path, book: Path | None = None) -> None:
        self.store = folder / "games"
        self.figurines = folder / "pictures.json"
        self.book = book

    def build(self, spec: Any, name: str | None = "g", **arguments: Any) -> dict[str, Any]:
        return copies.build_pgn(
            spec,
            name,
            str(self.store),
            str(self.figurines),
            book=str(self.book) if self.book else None,
            **arguments,
        )

    def pgn(self, name: str = "g") -> str:
        return (self.store / f"{name}.pgn").read_text(encoding="utf-8")

    def files(self) -> dict[str, bytes]:
        """Every file of the store and the figurines record, as bytes."""
        found = {path.name: path.read_bytes() for path in sorted(self.store.glob("*"))}
        if self.figurines.exists():
            found[self.figurines.name] = self.figurines.read_bytes()
        return found


def _without(result: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key not in keys}


def _whole_and_parts(
    tmp_path: Path,
    book: Path | None,
    whole: tuple[Any, dict[str, Any]],
    parts: list[tuple[Any, dict[str, Any]]],
) -> tuple[Games, dict[str, Any], Games, dict[str, Any]]:
    """The same game built whole, and in parts, each with the arguments it is given, in folders
    of their own: the games and the last result of each."""
    built, in_parts = Games(tmp_path / "whole", book), Games(tmp_path / "parts", book)
    result = built.build(whole[0], **whole[1])
    for spec, arguments in parts:
        last = in_parts.build(spec, **arguments)
        assert last["ok"] is True, last["errors"]
    return built, result, in_parts, last


def test_a_game_built_in_parts_is_the_game_built_whole(book: Path, tmp_path: Path) -> None:
    pic = f"[{_picture(book)}]"
    pieces = {_picture(book): "N"}
    whole = (
        {
            "headers": {"Event": "Parts", "Round": "1.1", "Zeta": "z", "Alpha": "a"},
            "comment": "A new introduction.",
            "moves": [
                {"san": "e4", "comment": "Best.", "nags": [3]},
                {"san": "e5"},
                {
                    "san": f"{pic}c3",
                    "comment": ["Replaced:", GAMBIT],
                    "variations": [[{"san": f"{pic}f3", "comment": [QUIET]}, {"san": f"{pic}c6"}]],
                },
                {"san": f"{pic}f6", "comment": [REPLIES]},
                {"san": "f4"},
                {"san": "exf4"},
            ],
            "result": "1-0",
        },
        {"pieces": pieces},
    )
    first = {
        "headers": {"Event": "Parts", "Round": "1.1", "Zeta": "z", "Alpha": "a"},
        "comment": [OPENS],
        "moves": [
            {"san": "e4", "comment": "Best.", "nags": [1]},
            {"san": "e5"},
            {"san": f"{pic}c3", "comment": [DEVELOPS]},
        ],
    }
    parts: list[tuple[Any, dict[str, Any]]] = [
        (first, {"pieces": pieces}),
        # The main line goes on, in a later part that gives no pieces.
        (
            {"moves": [{"san": f"{pic}f6", "comment": [REPLIES]}, {"san": "f4"}, {"san": "exf4"}]},
            {"after": "2w"},
        ),
        # A variation on an earlier move, its move written as a letter, and a comment replaced.
        (
            {
                "moves": [
                    {"san": "e5"},
                    {
                        "san": "Nc3",
                        "comment": ["Replaced:", GAMBIT],
                        "variations": [[{"san": f"{pic}f3", "comment": [QUIET]}]],
                    },
                ]
            },
            {"after": "1w"},
        ),
        # A variation goes on, and the result is given.
        ({"moves": [{"san": f"{pic}c6"}], "result": "1-0"}, {"after": "2w/v1/2w"}),
        # The game's comment and a move's nags are replaced; the move's comment stays.
        (
            {"comment": "A new introduction.", "moves": [{"san": "e4", "nags": [3]}]},
            {"after": "start"},
        ),
    ]
    built, result, in_parts, last = _whole_and_parts(tmp_path, book, whole, parts)
    assert result["ok"] is True, result["errors"]
    assert in_parts.pgn() == built.pgn()
    assert _without(last, "replaced") == _without(result, "replaced")
    assert result["variations"] == 1 and result["comments"] == 5 and result["plies"] == 6
    assert last["replaced"] is True and result["replaced"] is False
    assert last["copied"] == result["copied"] and len(result["copied"]) == 3


def test_a_game_from_a_fen_built_in_parts_is_the_game_built_whole(tmp_path: Path) -> None:
    whole = {
        "headers": {"Event": "Fork"},
        "start_fen": BLACK_FORK,
        "comment": "The fork.",
        "moves": [
            {"san": "Ne2+", "comment": "Check.", "variations": [[{"san": "Nf3+"}]]},
            {"san": "Kf1"},
            {"san": "Nxc1", "nags": [1]},
        ],
        "result": "0-1",
    }
    parts: list[tuple[Any, dict[str, Any]]] = [
        (
            {
                "headers": {"Event": "Fork"},
                "start_fen": BLACK_FORK,
                "moves": [{"san": "Ne2+", "comment": "Hm."}],
            },
            {},
        ),
        ({"moves": [{"san": "Kf1"}, {"san": "Nxc1!"}], "result": "0-1"}, {"after": "1b"}),
        (
            {"comment": "The fork.", "moves": [{"san": "Ne2+", "comment": "Check."}]},
            {"after": "start"},
        ),
        ({"moves": [{"san": "Ne2+", "variations": [[{"san": "Nf3+"}]]}]}, {"after": "start"}),
    ]
    built, result, in_parts, last = _whole_and_parts(tmp_path, None, (whole, {}), parts)
    assert in_parts.pgn() == built.pgn()
    assert f'FEN "{BLACK_FORK} b - - 0 1"' in in_parts.pgn()
    assert _without(last, "replaced") == _without(result, "replaced")


def test_a_placement_alone_with_no_moves_leaves_the_side_to_move_to_the_merged_line(
    tmp_path: Path,
) -> None:
    whole = {
        "start_fen": BLACK_FORK,
        "comment": "Black to play.",
        "moves": [{"san": "Ne2+"}, {"san": "Kf1"}],
    }
    parts: list[tuple[Any, dict[str, Any]]] = [
        ({"start_fen": BLACK_FORK, "comment": "Black to play."}, {}),
        ({"moves": [{"san": "Ne2+"}, {"san": "Kf1"}]}, {"after": "start"}),
    ]
    built, result, in_parts, last = _whole_and_parts(tmp_path, None, (whole, {}), parts)
    assert in_parts.pgn() == built.pgn()
    assert f'FEN "{BLACK_FORK} b - - 0 1"' in in_parts.pgn()
    assert _without(last, "replaced") == _without(result, "replaced")


def test_a_placement_alone_whose_saved_line_both_sides_can_play_is_left_to_the_merged_line(
    tmp_path: Path,
) -> None:
    # Either side can play Ra4 alone, so the first part's game has White to move; only Black can
    # play Ra4 Ra3, so a whole build deduces Black to move.
    placement = "r3k3/8/8/8/8/8/8/R3K3"
    whole = {"start_fen": placement, "moves": [{"san": "Ra4"}, {"san": "Ra3"}]}
    parts: list[tuple[Any, dict[str, Any]]] = [
        ({"start_fen": placement, "moves": [{"san": "Ra4"}]}, {}),
        ({"moves": [{"san": "Ra3"}]}, {"after": "1w"}),
    ]
    built, result, in_parts, last = _whole_and_parts(tmp_path, None, (whole, {}), parts)
    assert f'FEN "{placement} b - - 0 1"' in built.pgn()
    assert in_parts.pgn() == built.pgn()
    assert _without(last, "replaced") == _without(result, "replaced")


def test_a_placement_alone_whose_line_one_side_alone_could_play_keeps_that_side(
    tmp_path: Path,
) -> None:
    games = Games(tmp_path)
    games.build({"start_fen": BLACK_FORK, "moves": [{"san": "Ne2+"}]})
    assert f'FEN "{BLACK_FORK} b - - 0 1"' in games.pgn()
    # White has no queen: the error is at the part's own move, not at the first part's.
    result = games.build({"moves": [{"san": "Qd1"}]}, after="1b")
    assert result["ok"] is False
    (error,) = result["errors"]
    assert error["path"] == "2w" and error["move"] == "Qd1"


def test_a_part_of_a_game_saved_by_chess_tools_adds_to_it(tmp_path: Path) -> None:
    store = str(tmp_path / "games")
    build_pgn({"moves": [{"san": "e4"}]}, name="g", store=store)
    result = build_pgn({"moves": [{"san": "e5"}]}, name="g", store=store, after="1w")
    assert result["saved"] == "g" and result["replaced"] is True and result["plies"] == 2


def test_write_pgn_writes_a_game_built_in_parts_as_it_writes_the_game_built_whole(
    tmp_path: Path,
) -> None:
    whole = {"headers": {"Event": "A"}, "moves": [{"san": "e4"}, {"san": "e5"}, {"san": "Nf3"}]}
    parts: list[tuple[Any, dict[str, Any]]] = [
        ({"headers": {"Event": "A"}, "moves": [{"san": "e4"}]}, {}),
        ({"moves": [{"san": "e5"}]}, {"after": "1w"}),
        ({"moves": [{"san": "Nf3"}]}, {"after": "1b"}),
    ]
    built, _, in_parts, _ = _whole_and_parts(tmp_path, None, (whole, {}), parts)
    whole_file, parts_file = tmp_path / "whole.pgn", tmp_path / "parts.pgn"
    written = write_pgn(path=str(whole_file), names=["g"], store=str(built.store))
    in_the_end = write_pgn(path=str(parts_file), names=["g"], store=str(in_parts.store))
    assert parts_file.read_bytes() == whole_file.read_bytes()
    assert _without(in_the_end, "path") == _without(written, "path")


def test_the_record_beside_a_game_is_no_saved_game(tmp_path: Path) -> None:
    games = Games(tmp_path)
    games.build({"moves": [{"san": "e4"}]})
    with pytest.raises(ValueError, match=r"the games saved are: g$"):
        write_pgn(path=str(tmp_path / "x.pgn"), names=["nope"], store=str(games.store))
    with pytest.raises(ValueError, match=r"no game saved as 'g\.spec'"):
        write_pgn(path=str(tmp_path / "x.pgn"), names=["g.spec"], store=str(games.store))
    with pytest.raises(ValueError, match=r"the games saved are: g\."):
        copies.build_pgn({}, "nope", str(games.store), after="start")


def _refused(games: Games, spec: Any, says: str, **arguments: Any) -> None:
    """Building the part is refused with ValueError, and the store and the figurines record are
    as they were."""
    before = games.files()
    with pytest.raises(ValueError, match=says):
        games.build(spec, **arguments)
    assert games.files() == before


@pytest.fixture
def saved(tmp_path: Path) -> Games:
    """A game saved with a picture read as a knight: 1.e4 e5 2.Nf3 Nc6, with 2.Nc3 Nc6 as a
    variation of 2.Nf3."""
    games = Games(tmp_path)
    result = games.build(
        {
            "headers": {"Event": "Saved"},
            "moves": [
                {"san": "e4"},
                {"san": "e5"},
                {
                    "san": "[aaaaaa]f3",
                    "comment": "Develops.",
                    "variations": [[{"san": "Nc3"}, {"san": "Nc6"}]],
                },
                {"san": "Nc6"},
            ],
        },
        pieces={"aaaaaa": "N"},
    )
    assert result["ok"] is True, result["errors"]
    return games


def test_after_without_a_name_is_refused_and_says_to_give_one(saved: Games) -> None:
    _refused(
        saved,
        {"moves": [{"san": "Bb5"}]},
        "after adds a part to the game saved under name, so give name",
        name=None,
        after="2b",
    )


def test_after_for_a_game_not_saved_is_refused_and_says_to_build_its_first_part(
    saved: Games,
) -> None:
    _refused(
        saved,
        {"moves": [{"san": "Bb5"}]},
        "no game is saved as 'other'; the games saved are: g\\. Build the game's first part "
        "without after, or give the name of a game saved",
        name="other",
        after="2b",
    )


@pytest.mark.parametrize(
    ("after", "says"),
    [
        ("9w", "no move 9w: its main line has the moves 1w to 2b"),
        ("1b/v1/1b", "move 1b has no variation v1: it has none"),
        ("2w/v2/2w", "move 2w has no variation v2: its only variation is v1"),
        ("2w/v1/3w", "no move 2w/v1/3w: the saved game's line 2w/v1 has the moves 2w to 2b"),
    ],
)
def test_a_path_the_game_does_not_have_is_refused_and_says_what_it_has(
    saved: Games, after: str, says: str
) -> None:
    _refused(saved, {"moves": [{"san": "Bb5"}]}, says, after=after)


@pytest.mark.parametrize("after", ["12", "3x", "", "2w/v1"])
def test_an_after_that_is_not_a_path_is_refused_and_says_how_a_path_is_given(
    saved: Games, after: str
) -> None:
    _refused(
        saved,
        {"moves": [{"san": "Bb5"}]},
        "is not a path: give a move's path as errors give it",
        after=after,
    )


def test_a_part_that_is_not_the_saved_continuation_is_refused_and_names_the_saved_move(
    saved: Games,
) -> None:
    says = (
        "the saved game continues with Nf3 at 2w, not 'Nc3': a part's move there is Nf3, with an "
        "alternative in that move's variations; if the saved move is wrong, build the game again "
        "without after"
    )
    _refused(saved, {"moves": [{"san": "Nc3"}]}, says, after="1b")


def test_a_move_further_along_the_line_or_inside_a_variation_is_checked_as_the_first_is(
    saved: Games,
) -> None:
    along = {"moves": [{"san": "e5"}, {"san": "Nf3"}, {"san": "Nf6"}]}
    _refused(saved, along, "continues with Nc6 at 2b, not 'Nf6'", after="1w")
    variation = {"moves": [{"san": "Nf3", "variations": [[{"san": "Nc3"}, {"san": "Nf6"}]]}]}
    _refused(saved, variation, "continues with Nc6 at 2w/v1/2b, not 'Nf6'", after="1b")


def test_a_parts_move_that_is_not_legal_is_refused_as_not_the_continuation(saved: Games) -> None:
    _refused(
        saved,
        {"moves": [{"san": "Nf7"}]},
        "continues with Nf3 at 2w, not 'Nf7', which is not a legal move there",
        after="1b",
    )


@pytest.mark.parametrize("given", [{"headers": {"Event": "Other"}}, {"start_fen": BLACK_FORK}])
def test_headers_and_a_start_fen_in_a_part_are_refused_and_say_they_belong_to_the_first_part(
    saved: Games, given: dict[str, Any]
) -> None:
    field = next(iter(given))
    _refused(
        saved,
        {**given, "moves": [{"san": "Bb5"}]},
        f"a part cannot give {field}, which only the game's first part gives",
        after="2b",
    )


def test_a_part_with_empty_headers_is_taken_as_one_without(saved: Games) -> None:
    result = saved.build({"headers": {}, "moves": [{"san": "Bb5"}]}, after="2b")
    assert result["ok"] is True, result["errors"]


def test_a_game_saved_without_its_record_is_refused_and_says_to_build_it_again(
    saved: Games,
) -> None:
    says = "has no record of the spec it was built from, or its PGN has changed since"
    (saved.store / "g.spec.json").unlink()
    _refused(saved, {"moves": [{"san": "Bb5"}]}, says, after="2b")


def test_a_game_whose_pgn_no_longer_matches_its_record_is_refused_and_says_to_build_it_again(
    saved: Games,
) -> None:
    pgn = saved.store / "g.pgn"
    pgn.write_text(pgn.read_text(encoding="utf-8").replace("Develops.", "Moves."), encoding="utf-8")
    _refused(
        saved,
        {"moves": [{"san": "Bb5"}]},
        "PGN has changed since it was saved: build it again without after",
        after="2b",
    )


def test_a_record_that_holds_no_spec_is_refused_and_says_to_build_the_game_again(
    saved: Games,
) -> None:
    (saved.store / "g.spec.json").write_text("{}", encoding="utf-8")
    _refused(
        saved,
        {"moves": [{"san": "Bb5"}]},
        "PGN has changed since it was saved: build it again without after",
        after="2b",
    )


def test_a_merged_game_with_errors_is_not_saved_and_names_paths_in_the_whole_game(
    saved: Games,
) -> None:
    before = saved.files()
    result = saved.build({"moves": [{"san": "Bb5"}, {"san": "Nf6"}, {"san": "Bb5"}]}, after="2b")
    assert result["ok"] is False and result["saved"] is None and result["replaced"] is False
    (error,) = result["errors"]
    assert error["path"] == "4w" and error["move"] == "Bb5"
    assert saved.files() == before


@pytest.mark.parametrize(
    ("san", "says"),
    [
        ("Nxf3", "marks a capture, but the move captures nothing"),
        ("Nf3+", "marks check, but the move gives no check"),
        ("Nf3#", "marks mate, but the move does not mate"),
    ],
)
def test_a_mark_that_a_move_both_games_have_contradicts_is_an_error_as_in_a_whole_build(
    saved: Games, san: str, says: str
) -> None:
    before = saved.files()
    result = saved.build({"moves": [{"san": "e5"}, {"san": san}]}, after="1w")
    assert result["ok"] is False and says in result["errors"][0]["message"]
    assert result["errors"][0]["path"] == "2w"
    assert saved.files() == before


def test_a_check_mark_a_part_adds_to_a_saved_move_that_gives_check_is_kept(
    tmp_path: Path,
) -> None:
    games = Games(tmp_path)
    games.build({"moves": [{"san": "e4"}, {"san": "f5"}, {"san": "Qh5"}]})
    result = games.build({"moves": [{"san": "f5"}, {"san": "Qh5+"}]}, after="1w")
    assert result["ok"] is True, result["errors"]
    assert "2. Qh5+" in games.pgn()


def test_what_a_part_gives_replaces_the_games_and_what_it_leaves_out_stays(
    tmp_path: Path,
) -> None:
    games = Games(tmp_path)
    games.build(
        {
            "comment": "Intro.",
            "moves": [
                {"san": "e4", "comment": "King.", "nags": [1]},
                {"san": "d5!", "comment": "Counter."},
                {"san": "exd5", "nags": [2]},
            ],
            "result": "1-0",
        }
    )

    def seen() -> list[tuple[str, str, set[int]]]:
        (game,) = read_games(games.pgn())
        return [(node.san(), node.comment, set(node.nags)) for node in game.mainline()]

    kept = [("e4", "King.", {1}), ("d5", "Counter.", {1}), ("exd5", "", {2})]
    assert seen() == kept
    # A part that gives only the moves keeps everything, the nag of a saved suffix included.
    games.build({"moves": [{"san": "e4"}, {"san": "d5"}, {"san": "exd5"}]}, after="start")
    assert seen() == kept
    assert read_games(games.pgn())[0].comment == "Intro."
    assert read_games(games.pgn())[0].headers["Result"] == "1-0"
    # What it gives, even empty, replaces: comments, nags, the game's comment and its result. The
    # suffix of a saved san does not bring back a nag the part replaced.
    games.build(
        {
            "comment": "",
            "moves": [{"san": "e4", "comment": "", "nags": []}, {"san": "d5", "nags": []}],
            "result": "*",
        },
        after="start",
    )
    assert seen() == [("e4", "", set()), ("d5", "Counter.", set()), ("exd5", "", {2})]
    assert read_games(games.pgn())[0].comment == ""
    assert read_games(games.pgn())[0].headers["Result"] == "*"
    # A suffix in the part is a nag, and replaces the saved nags.
    games.build({"moves": [{"san": "e4"}, {"san": "d5?", "nags": [3]}]}, after="start")
    assert seen()[1] == ("d5", "Counter.", {2, 3})


def test_a_part_after_a_move_replaces_that_moves_comment(tmp_path: Path) -> None:
    games = Games(tmp_path)
    games.build({"moves": [{"san": "e4", "comment": "King."}, {"san": "e5", "comment": "Same."}]})

    def comments() -> list[str]:
        return [node.comment for node in read_games(games.pgn())[0].mainline()]

    games.build({"comment": "Pawn.", "moves": [{"san": "Nf3"}]}, after="1b")
    assert comments() == ["King.", "Pawn.", ""]
    games.build({"comment": ""}, after="1w")
    assert comments() == ["", "Pawn.", ""]
    games.build({"moves": [{"san": "Nc6"}]}, after="2w")
    assert comments() == ["", "Pawn.", "", ""]


def test_the_variations_of_a_part_merge_into_those_of_the_game(tmp_path: Path) -> None:
    games = Games(tmp_path)
    games.build(
        {
            "moves": [
                {"san": "e4", "variations": [[{"san": "d4", "comment": "Queen."}, {"san": "d5"}]]},
                {"san": "e5"},
            ]
        }
    )
    games.build(
        {
            "moves": [
                {
                    "san": "e4",
                    "variations": [
                        [{"san": "c4"}],
                        [{"san": "d4", "comment": "Pawn."}, {"san": "d5"}, {"san": "c4"}],
                    ],
                }
            ]
        },
        after="start",
    )
    (game,) = read_games(games.pgn())
    # The line that begins with the variation's first move goes into it; the other is added after
    # the variations.
    assert [(node.san(), node.comment) for node in game.variations] == [
        ("e4", ""),
        ("d4", "Pawn."),
        ("c4", ""),
    ]
    assert [node.san() for node in game.variations[1].mainline()] == ["d5", "c4"]


def test_an_empty_saved_variation_is_skipped_when_a_part_adds_a_variation(tmp_path: Path) -> None:
    games = Games(tmp_path)
    assert games.build({"moves": [{"san": "e4", "variations": [[]]}, {"san": "e5"}]})["ok"]
    result = games.build({"moves": [{"san": "e4", "variations": [[{"san": "d4"}]]}]}, after="start")
    assert result["ok"] is True, result["errors"]
    assert "1. e4 ( 1. d4 ) 1... e5" in " ".join(games.pgn().split())


def test_two_variations_with_the_same_first_move_merge_as_in_a_whole_build(saved: Games) -> None:
    both = {
        "moves": [
            {"san": "e5"},
            {
                "san": "Nf3",
                "variations": [[{"san": "Nc3"}, {"san": "Nc6"}], [{"san": "Nc3"}, {"san": "Nf6"}]],
            },
        ]
    }
    whole = Games(saved.store.parent / "whole")
    whole.build(
        {
            "headers": {"Event": "Saved"},
            "moves": [
                {"san": "e4"},
                {"san": "e5"},
                {
                    "san": "[aaaaaa]f3",
                    "comment": "Develops.",
                    "variations": [
                        [{"san": "Nc3"}, {"san": "Nc6"}],
                        [{"san": "Nc3"}, {"san": "Nf6"}],
                    ],
                },
                {"san": "Nc6"},
            ],
        },
        pieces={"aaaaaa": "N"},
    )
    once = saved.build(both, after="1w")
    assert once["ok"] is True, once["errors"]
    assert saved.pgn() == whole.pgn()
    twice = saved.build(both, after="1w")
    assert twice == once and saved.pgn() == whole.pgn()


def test_a_part_adding_two_lines_that_begin_alike_given_twice_gives_the_game_given_once(
    tmp_path: Path,
) -> None:
    games = Games(tmp_path)
    games.build({"moves": [{"san": "e4"}, {"san": "e5"}, {"san": "Nf3"}, {"san": "Nc6"}]})
    lines = [[{"san": "Nc3"}, {"san": "Nc6"}], [{"san": "Nc3"}, {"san": "Nf6"}]]
    part = {"moves": [{"san": "e5"}, {"san": "Nf3", "variations": lines}]}
    once = games.build(part, after="1w")
    pgn = games.pgn()
    assert "( 2. Nc3 Nc6 ) ( 2. Nc3 Nf6 )" in " ".join(pgn.split())
    assert games.build(part, after="1w") == once and games.pgn() == pgn


def test_a_part_given_twice_gives_the_game_it_gave_once(tmp_path: Path) -> None:
    games = Games(tmp_path)
    games.build({"headers": {"Event": "E"}, "moves": [{"san": "e4"}, {"san": "e5"}]})
    part = {
        "comment": "Given.",
        "moves": [
            {"san": "e5", "comment": "Again.", "variations": [[{"san": "c5"}, {"san": "Nf3"}]]},
            {"san": "Nf3", "nags": [1]},
        ],
        "result": "1-0",
    }
    once = games.build(part, after="1w")
    after_once = games.files()
    twice = games.build(part, after="1w")
    assert once["ok"] is twice["ok"] is True
    assert games.files() == after_once
    assert twice == once


def test_pieces_carry_over_within_a_game_and_a_later_key_takes_the_later_letter(
    tmp_path: Path,
) -> None:
    games = Games(tmp_path)
    first = games.build(
        {"moves": [{"san": "e4", "comment": "A ♗ and a ♘."}, {"san": "e5"}, {"san": "[aaaaaa]f3"}]},
        pieces={"aaaaaa": "N", "♗": "B"},
    )
    assert first["ok"] is True, first["errors"]
    assert "Nf3" in games.pgn() and "A B and a ♘." in games.pgn()
    # A later part that leaves the pieces out leaves the earlier comments and moves as they were.
    second = games.build(
        {"moves": [{"san": "e4"}, {"san": "e5"}, {"san": "[aaaaaa]f3"}]}, after="start"
    )
    assert second["ok"] is True, second["errors"]
    third = games.build({"moves": [{"san": "e4", "comment": "A ♗ and a ♘."}]}, after="start")
    assert third["ok"] is True, third["errors"]
    assert "Nf3" in games.pgn() and "A B and a ♘." in games.pgn()
    # A later part adds a key, and a key given again takes the later letter.
    games.build(
        {"moves": [{"san": "e4", "comment": "A ♗ and a ♘."}]},
        after="start",
        pieces={"♘": "N", "♗": "R"},
    )
    assert "A R and a N." in games.pgn() and "Nf3" in games.pgn()
    games.build({"moves": [{"san": "e4", "comment": "A ♗ and a ♘."}]}, after="start")
    assert "A R and a N." in games.pgn()
    # A build without after starts afresh.
    games.build({"moves": [{"san": "e4", "comment": "A ♗ and a ♘."}]})
    games.build({"moves": [{"san": "e4"}, {"san": "e5"}, {"san": "Nf3"}]}, after="start")
    assert "A ♗ and a ♘." in games.pgn() and "Nf3" in games.pgn()


def test_a_picture_is_compared_as_the_saved_game_read_it(saved: Games) -> None:
    # aaaaaa was read as a knight, so [aaaaaa]f3 is the saved Nf3; bbbbbb can only be a knight on
    # c6, so [bbbbbb]c6 is the saved Nc6.
    result = saved.build(
        {"moves": [{"san": "e5"}, {"san": "[aaaaaa]f3", "comment": "Again."}]}, after="1w"
    )
    assert result["ok"] is True, result["errors"]
    result = saved.build({"moves": [{"san": "[bbbbbb]c6"}]}, after="2w")
    assert result["ok"] is True, result["errors"]
    assert result["pictures"] == {"aaaaaa": "N", "bbbbbb": "N"}
    assert "2. Nf3 { Again. }" in " ".join(saved.pgn().split())


def test_a_picture_that_the_moves_do_not_settle_is_never_taken_for_the_saved_move(
    tmp_path: Path,
) -> None:
    games = Games(tmp_path)
    games.build({"moves": [{"san": san} for san in ("e4", "e5", "Nf3", "Nc6", "Be2")]})
    # The picture could be the bishop, the queen or the king on e2.
    _refused(
        games,
        {"moves": [{"san": "[bbbbbb]e2"}]},
        "the picture in '\\[bbbbbb\\]e2' at 3w could be more than one piece, and the saved game "
        "continues with Be2 there: give the piece's letter in the move, or in pieces",
        after="2b",
    )
    result = games.build({"moves": [{"san": "[bbbbbb]e2"}]}, after="2b", pieces={"bbbbbb": "B"})
    assert result["ok"] is True, result["errors"]


@pytest.mark.parametrize("letter", ["Q", "B"])
def test_pieces_that_read_a_saved_moves_picture_as_another_piece_are_refused(
    saved: Games, letter: str
) -> None:
    _refused(
        saved,
        {"moves": [{"san": "Bb5"}]},
        f"pieces gives the picture aaaaaa as {letter}, where the saved game read it as N: a part "
        "cannot change a saved move, so leave each such picture out of pieces, or build the game "
        "again without after",
        after="2b",
        pieces={"aaaaaa": letter},
    )


def test_a_part_keeps_to_the_game_named(tmp_path: Path) -> None:
    games = Games(tmp_path)
    games.build({"moves": [{"san": "e4"}]}, name="a")
    games.build({"moves": [{"san": "d4"}]}, name="b")
    games.build({"moves": [{"san": "e5"}]}, name="a", after="1w")
    assert "1. e4 e5" in " ".join(games.pgn("a").split()) and "1. d4" in games.pgn("b")
