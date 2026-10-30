import json
from pathlib import Path

import pytest
from support import BLACK_FORK, FORK, move_specs

from pgnify.chess_tools import GameSpec, MoveSpec, build_pgn, read_games, validate_moves


@pytest.fixture
def store(tmp_path: Path) -> str:
    return str(tmp_path / "games")


@pytest.fixture
def figurines(tmp_path: Path) -> str:
    return str(tmp_path / "pictures.json")


def _movetext(pgn: str) -> str:
    """The movetext of one game's PGN, its whitespace folded to single spaces."""
    return " ".join(pgn.split("\n\n", 1)[1].split())


def test_build_pgn_round_trips_a_variation_and_a_comment() -> None:
    spec: GameSpec = {
        "headers": {"Event": "Test Book", "Round": "1.1", "Annotator": "Author"},
        "moves": [
            {"san": "e4", "comment": "Best by test."},
            {"san": "e5", "variations": [[{"san": "c5", "comment": "The Sicilian."}]]},
            {"san": "Nf3", "nags": [1]},
        ],
        "result": "*",
    }
    result = build_pgn(spec)
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert game.headers["Event"] == "Test Book"
    assert game.headers["Round"] == "1.1"
    first = game.next()
    assert first is not None and first.comment == "Best by test."
    second = first.next()
    assert second is not None
    assert [v.san() for v in first.variations] == ["e5", "c5"]
    assert first.variations[1].comment == "The Sicilian."
    third = second.next()
    assert third is not None and 1 in third.nags
    assert game.errors == []


def test_build_pgn_emits_setup_and_fen_for_a_position() -> None:
    result = build_pgn({"headers": {}, "start_fen": FORK, "moves": move_specs("Nh7+")})
    (game,) = read_games(result["pgn"])
    assert game.headers["SetUp"] == "1"
    assert game.headers["FEN"] == FORK


@pytest.mark.parametrize("first", ["Ne2+", "Ne2+!"])
def test_build_pgn_deduces_black_to_move_from_a_placement(first: str) -> None:
    result = build_pgn({"start_fen": BLACK_FORK, "moves": move_specs(first, "Kf1", "Nxc1")})
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert game.headers["FEN"] == BLACK_FORK + " b - - 0 1"


def test_build_pgn_builds_a_text_only_game() -> None:
    spec: GameSpec = {
        "headers": {"Event": "Book"},
        "comment": "Preface prose.",
        "moves": [],
        "result": "*",
    }
    (game,) = read_games(build_pgn(spec)["pgn"])
    assert game.comment == "Preface prose."
    assert game.next() is None


def test_build_pgn_takes_the_result_from_the_headers() -> None:
    result = build_pgn({"headers": {"Result": "1-0"}, "moves": move_specs("e4")})
    (game,) = read_games(result["pgn"])
    assert game.headers["Result"] == "1-0"
    assert result["pgn"].rstrip().endswith("1-0")


def test_build_pgn_refuses_a_result_that_pgn_cannot_hold() -> None:
    with pytest.raises(ValueError, match="is not one of"):
        build_pgn({"moves": move_specs("e4"), "result": "white wins"})


def test_build_pgn_reads_a_suffix_annotation_on_a_move_as_its_nag() -> None:
    spec: GameSpec = {"moves": [{"san": "e4!"}, {"san": "e5?!", "nags": [6]}, {"san": "Nf3!!"}]}
    result = build_pgn(spec)
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert [node.nags for node in game.mainline()] == [{1}, {6}, {3}]


def test_build_pgn_reads_a_spec_given_as_json_text() -> None:
    spec: GameSpec = {"moves": [{"san": "e4", "variations": [move_specs("d4", "d5")]}]}
    assert build_pgn(json.dumps(spec)) == build_pgn(spec)


def test_build_pgn_says_why_and_where_json_text_breaks() -> None:
    # The variations list is closed one line too early, a slip models make in nested lists.
    text = '{"moves": [{"san": "e4", "variations": [[{"san": "d4"}]], [{"san": "c4"}]}]}'
    where = r'"d4"\}\]\], +<<here>> \[\{"san": "c4"'
    with pytest.raises(ValueError, match=r"the spec's JSON is malformed: Expecting .*" + where):
        build_pgn(text)


def test_build_pgn_refuses_json_text_that_holds_no_object() -> None:
    with pytest.raises(ValueError, match="the spec's JSON is not an object"):
        build_pgn('[{"san": "e4"}]')


def test_build_pgn_reports_an_illegal_move_by_its_path() -> None:
    result = build_pgn({"headers": {}, "moves": move_specs("e4", "e4")})
    assert result["ok"] is False
    assert result["errors"][0]["path"] == "1b"
    assert "e4" in result["errors"][0]["message"]


def test_build_pgn_reports_an_illegal_move_in_a_variation_and_keeps_the_main_line() -> None:
    line: list[MoveSpec] = [
        *move_specs("e4", "e5", "Nf3", "Nc6"),
        {"san": "Bb5", "variations": [move_specs("Bc4", "Qd5")]},
    ]
    result = build_pgn({"moves": line})
    assert [error["path"] for error in result["errors"]] == ["3w/v1/3b"]
    (game,) = read_games(result["pgn"])
    assert len(list(game.mainline_moves())) == 5


def test_build_pgn_numbers_paths_from_the_start_fens_move() -> None:
    result = build_pgn({"start_fen": FORK, "moves": move_specs("Nh7+", "Kh8")})
    assert result["errors"][0]["path"] == "2b"


def test_build_pgn_names_the_next_move_on_which_a_misplaced_variation_would_be_legal() -> None:
    # 2.d4 exd4, White's alternative to 2.Nf3, given on Black's 1...e5 instead.
    line: list[MoveSpec] = [
        {"san": "e4"},
        {"san": "e5", "variations": [move_specs("d4", "exd4")]},
        {"san": "Nf3"},
    ]
    result = build_pgn({"moves": line})
    assert [(e["path"], e["move"]) for e in result["errors"]] == [("1b/v1/1b", "d4")]
    message = result["errors"][0]["message"]
    assert (
        "A variation replaces the move it is given on: its first move is made instead of that "
        "move, by the same side, and both sides then move in turn." in message
    )
    assert (
        "Given on 2w Nf3 instead, this line would be legal, though legality alone does not say "
        "the book means it there" in message
    )
    assert "1w e4" not in message  # on 1.e4 it would be 1.d4 exd4, which is not legal


def test_build_pgn_names_the_move_before_on_which_a_misplaced_variation_would_be_legal() -> None:
    # 1...e6, Black's alternative to 1...e5, given on White's 2.Nf3 instead.
    line: list[MoveSpec] = [
        *move_specs("e4", "e5"),
        {"san": "Nf3", "variations": [move_specs("e6")]},
    ]
    result = build_pgn({"moves": line})
    assert [(e["path"], e["move"]) for e in result["errors"]] == [("2w/v1/2w", "e6")]
    assert "Given on 1b e5 instead, this line would be legal" in result["errors"][0]["message"]


def test_build_pgn_names_the_move_for_a_capturing_variation_given_one_move_early() -> None:
    # 3...Bxc5 4.Nc3, Black's alternative to 3...Bb7, given on White's 3.Bc5 instead, where
    # python-chess would read Bxc5 as 3.Bc5 itself and the line would fail only at 4.Nc3.
    line: list[MoveSpec] = [
        *move_specs("b3", "e6", "Ba3", "b6"),
        {"san": "Bc5", "variations": [move_specs("Bxc5", "Nc3")]},
        {"san": "Bb7"},
    ]
    result = build_pgn({"moves": line})
    assert [(e["path"], e["move"]) for e in result["errors"]] == [("3w/v1/3w", "Bxc5")]
    assert "Given on 3b Bb7 instead, this line would be legal" in result["errors"][0]["message"]


def test_build_pgn_names_a_move_for_a_variation_that_fails_after_its_first_move() -> None:
    # 3...Nd4 4.Nxd4 exd4, Black's alternative to 3...Bc5, given on White's 3.Bc4 instead: there
    # Nd4 is White's knight move, and the line fails only at exd4.
    line: list[MoveSpec] = [
        *move_specs("e4", "e5", "Nf3", "Nc6"),
        {"san": "Bc4", "variations": [move_specs("Nd4", "Nxd4", "exd4")]},
        {"san": "Bc5"},
    ]
    result = build_pgn({"moves": line})
    assert [(e["path"], e["move"]) for e in result["errors"]] == [("3w/v1/4w", "exd4")]
    assert "Given on 3b Bc5 instead, this line would be legal" in result["errors"][0]["message"]


def test_build_pgn_names_no_move_for_a_variation_when_no_nearby_move_fits() -> None:
    line: list[MoveSpec] = [
        {"san": "e4"},
        {"san": "e5", "variations": [move_specs("Ke3")]},
        {"san": "Nf3"},
    ]
    message = build_pgn({"moves": line})["errors"][0]["message"]
    assert "Given on" not in message


def test_build_pgn_names_no_move_for_a_later_failure_when_no_nearby_move_fits() -> None:
    # A mistyped second move, legal on no move nearby: 3.d4 exd5 on 3.Bc4.
    line: list[MoveSpec] = [
        *move_specs("e4", "e5", "Nf3", "Nc6"),
        {"san": "Bc4", "variations": [move_specs("d4", "exd5")]},
        {"san": "Bc5"},
    ]
    result = build_pgn({"moves": line})
    assert [(e["path"], e["move"]) for e in result["errors"]] == [("3w/v1/3b", "exd5")]
    assert "Given on" not in result["errors"][0]["message"]


def test_build_pgn_saves_a_named_game_and_gives_its_outline_not_its_pgn(store: str) -> None:
    spec: GameSpec = {
        "comment": "A hint about the position.",
        "moves": [{"san": "e4", "comment": "The best move by test, they say."}, {"san": "e5"}],
    }
    result = build_pgn(spec, name="g", store=store)
    assert "pgn" not in result
    assert (result["ok"], result["saved"], result["replaced"]) == (True, "g", False)
    assert result["outline"] == (
        "{ A hint about the position. } 1. e4 { The best move by test,… } 1... e5 *"
    )
    assert _movetext((Path(store) / "g.pgn").read_text()) == (
        "{ A hint about the position. } 1. e4 { The best move by test, they say. } 1... e5 *"
    )


def test_build_pgn_replaces_a_game_saved_under_the_same_name(store: str) -> None:
    build_pgn({"moves": move_specs("e4")}, name="g", store=store)
    result = build_pgn({"moves": move_specs("d4")}, name="g", store=store)
    assert result["replaced"] is True
    assert "1. d4 *" in (Path(store) / "g.pgn").read_text()


def test_build_pgn_saves_nothing_when_a_move_is_illegal(store: str) -> None:
    build_pgn({"moves": move_specs("e4")}, name="g", store=store)
    result = build_pgn({"moves": move_specs("e5")}, name="g", store=store)
    assert (result["ok"], result["saved"]) == (False, None)
    assert result["errors"]
    assert "1. e4 *" in (Path(store) / "g.pgn").read_text()


@pytest.mark.parametrize("name", ["../out", "a/b", "x" * 41])
def test_build_pgn_refuses_a_name_that_is_not_a_plain_name(store: str, name: str) -> None:
    with pytest.raises(ValueError, match="a game's name"):
        build_pgn({"moves": move_specs("e4")}, name=name, store=store)


def test_build_pgn_reads_a_figurine_in_a_variation_or_comment_as_the_main_line_reads_it() -> None:
    # Alone, 2.[aaaaaa]e2 could be a bishop, king, knight or queen; the main line makes it a knight.
    spec: GameSpec = {
        "moves": [
            {"san": "e4"},
            {"san": "e5"},
            {
                "san": "[aaaaaa]f3",
                "comment": "Better than 2 [aaaaaa]e2, which blocks the bishop.",
                "variations": [move_specs("[aaaaaa]e2")],
            },
            {"san": "[picture 7: aaaaaa] c6"},
        ]
    }
    result = build_pgn(spec)
    assert result["ok"] is True, result["errors"]
    assert result["pictures"] == {"aaaaaa": "N"}
    assert _movetext(result["pgn"]) == (
        "1. e4 e5 2. Nf3 { Better than 2 Ne2, which blocks the bishop. } ( 2. Ne2 ) 2... Nc6 *"
    )


def test_build_pgn_reads_figurines_as_far_as_a_line_is_legal_and_reports_where_it_is_not() -> None:
    # 3.e5 is not legal. As a queen, [aaaaaa] stops the line sooner, at 2...Qc6.
    result = build_pgn({"moves": move_specs("e4", "e5", "[aaaaaa]f3", "[aaaaaa]c6", "e5")})
    assert result["ok"] is False
    assert [(e["path"], e["move"]) for e in result["errors"]] == [("3w", "e5")]
    assert "2. Nf3 Nc6" in result["pgn"]
    assert result["pictures"] == {"aaaaaa": "N"}


def test_build_pgn_writes_a_figurine_in_a_comment_joined_to_the_rest_of_its_move() -> None:
    comment = "Or 2 [picture 2: aaaaaa]\n f3, developing the [aaaaaa] by a move."
    moves: list[MoveSpec] = [
        {"san": "e4"},
        {"san": "e5", "comment": comment},
        *move_specs("[aaaaaa]f3", "[aaaaaa]c6"),
    ]
    result = build_pgn({"moves": moves})
    assert result["ok"] is True, result["errors"]
    assert "{ Or 2 Nf3, developing the N by a move. }" in _movetext(result["pgn"])


def test_build_pgn_reports_a_figurine_in_a_comment_that_no_move_reads() -> None:
    result = build_pgn({"moves": [{"san": "e4", "comment": "Not 1 [cccccc]h5 at once."}]})
    assert result["ok"] is False
    assert [(e["path"], e["move"]) for e in result["errors"]] == [("1w", "e4")]
    assert "cccccc" in result["errors"][0]["message"]


@pytest.mark.parametrize(
    ("sans", "path", "says"),
    [
        (("e4", "e5", "[cccccc]e2"), "2w", "could be B, K, N or Q"),
        (("e4", "[cccccc]e5"), "1b", "no piece"),
    ],
)
def test_build_pgn_names_the_pieces_a_figurine_could_be_or_says_none_fits(
    sans: tuple[str, ...], path: str, says: str
) -> None:
    result = build_pgn({"moves": move_specs(*sans)})
    assert [(e["path"], e["move"]) for e in result["errors"]] == [(path, sans[-1])]
    assert says in result["errors"][0]["message"]


def test_build_pgn_says_where_a_line_stops_when_a_figurine_is_left_open() -> None:
    result = build_pgn({"moves": move_specs("e4", "e5", "[aaaaaa]f3", "d6", "d5")})
    assert [(e["path"], e["move"]) for e in result["errors"]] == [("2w", "[aaaaaa]f3")]
    assert "3w, 'd5'" in result["errors"][0]["message"]


def test_a_figurine_read_in_a_saved_game_keeps_its_piece_in_later_calls(
    store: str, figurines: str
) -> None:
    moves = move_specs("e4", "e5", "[aaaaaa]f3", "[aaaaaa]c6")
    build_pgn({"moves": moves}, name="g", store=store, figurines=figurines)
    # Alone, 2.[aaaaaa]e2 could be a bishop, king, knight or queen.
    later = validate_moves("start", "1 e4 e5 2 [aaaaaa]e2", figurines=figurines)
    assert later["ok"] is True, later["error"]
    assert later["plies"][-1]["san"] == "Ne2"
    alone = move_specs("e4", "e5", "[aaaaaa]e2")
    assert build_pgn({"moves": alone}, name="h", store=store, figurines=figurines)["ok"] is True


def test_a_figurine_read_in_a_game_not_saved_keeps_no_piece(store: str, figurines: str) -> None:
    moves = move_specs("e4", "e5", "[aaaaaa]f3", "[aaaaaa]c6", "e5")
    # 3.e5 is not legal, so g is not saved.
    build_pgn({"moves": moves}, name="g", store=store, figurines=figurines)
    build_pgn({"moves": moves[:4]}, figurines=figurines)
    later = validate_moves("start", "1 e4 e5 2 [aaaaaa]e2", figurines=figurines)
    assert "could be B, K, N or Q" in later["error"]["message"]


def test_saving_a_game_again_reads_its_figurines_afresh(store: str, figurines: str) -> None:
    # The first save mistypes the square: only a queen goes to h5.
    first = move_specs("e4", "e5", "[bbbbbb]h5")
    saved = build_pgn({"moves": first}, name="g", store=store, figurines=figurines)
    assert saved["pictures"] == {"bbbbbb": "Q"}
    again = move_specs("e4", "e5", "[bbbbbb]f3", "[cccccc]c6", "[bbbbbb]xe5")
    result = build_pgn({"moves": again}, name="g", store=store, figurines=figurines)
    assert result["ok"] is True, result["errors"]
    assert result["pictures"] == {"bbbbbb": "N", "cccccc": "N"}


def test_figurines_read_in_two_saved_games_both_hold_later(store: str, figurines: str) -> None:
    a = move_specs("e4", "e5", "[aaaaaa]f3", "[aaaaaa]c6")
    b = move_specs("e4", "e5", "[bbbbbb]c4")
    build_pgn({"moves": a}, name="a", store=store, figurines=figurines)
    build_pgn({"moves": b}, name="b", store=store, figurines=figurines)
    # Alone, 2.[bbbbbb]e2 and 2...[aaaaaa]e7 could each be a bishop, king, knight or queen.
    later = validate_moves("start", "1 e4 e5 2 [bbbbbb]e2 [aaaaaa]e7", figurines=figurines)
    assert later["ok"] is True, later["error"]
    assert [p["san"] for p in later["plies"]][2:] == ["Be2", "Ne7"]
