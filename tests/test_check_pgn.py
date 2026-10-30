import logging

import pytest
from support import FORK

from pgnify.chess_tools import GameSpec, build_pgn, check_pgn


def test_check_pgn_counts_games_plies_variations_and_comments() -> None:
    pgn = (
        '[Event "A"]\n[Result "*"]\n\n1. e4 e5 (1... c5 {Sicilian}) 2. Nf3 *\n\n'
        '[Event "B"]\n[Result "*"]\n\n1. d4 d5 *\n'
    )
    result = check_pgn(pgn)
    assert result["ok"] is True
    assert [g["plies"] for g in result["games"]] == [3, 2]
    assert result["games"][0]["variations"] == 1
    assert result["games"][0]["comments"] == 1


def test_check_pgn_passes_a_game_with_suffix_annotations_nags_comments_and_variations() -> None:
    pgn = (
        '[Event "A b"]\n[Result "*"]\n\n'
        "{Pre} 1. e4! {c1} e5?! 2. Nf3 $1 (2. Nc3 {Vienna} Nf6) Nc6 *\n"
    )
    result = check_pgn(pgn)
    assert result["ok"] is True, result
    assert result["dropped"] == []


def test_check_pgn_passes_what_build_pgn_builds() -> None:
    spec: GameSpec = {
        "headers": {"Event": "Test Book"},
        "start_fen": FORK,
        "comment": "Use a knight fork.",
        "moves": [
            {"san": "Nh7+", "nags": [1]},
            {"san": "Kg7", "variations": [[{"san": "Ke7"}, {"san": "Nxf6", "comment": "Also."}]]},
            {"san": "Nxf6", "comment": "White is up a queen (9 points) for a knight."},
        ],
    }
    assert check_pgn(build_pgn(spec)["pgn"])["ok"] is True


def test_check_pgn_reports_an_illegal_move_in_a_game() -> None:
    result = check_pgn('[Event "A"]\n[Result "*"]\n\n1. e4 e4 2. Nf3 *\n')
    assert result["ok"] is False
    assert result["games"][0]["errors"]


def test_check_pgn_reports_what_it_cannot_read_without_logging_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        (game,) = check_pgn('[Result "*"]\n\n1. e4 e5 2. Ke3 *\n')["games"]
    assert game["errors"]
    assert not caplog.records


def test_check_pgn_reports_a_fen_tag_that_holds_only_a_placement() -> None:
    pgn = f'[FEN "{FORK.split()[0]}"]\n[SetUp "1"]\n\n1. Nh7+ *\n'
    result = check_pgn(pgn)
    assert result["ok"] is False
    assert "FEN" in result["games"][0]["errors"][0]


def test_check_pgn_reports_a_fen_tag_python_chess_cannot_read() -> None:
    result = check_pgn('[SetUp "1"]\n[FEN "not a fen"]\n[Result "*"]\n\n1. e4 *\n')
    assert result["ok"] is False
    assert any("FEN" in error for error in result["games"][0]["errors"])


def test_check_pgn_reports_commentary_left_outside_braces() -> None:
    result = check_pgn("1. e4 The best move by test. e5 2. Nf3 Nc6 *\n")
    assert result["ok"] is False
    assert result["dropped"] == ["line 1: The best move by test."]


def test_check_pgn_reports_a_comment_never_closed() -> None:
    result = check_pgn('[Result "*"]\n\n1. e4 {never closed e5 2. Nf3 *\n')
    assert result["ok"] is False
    assert result["dropped"] == ["line 3: { is never closed, so the rest became a comment"]


def test_check_pgn_reports_tags_read_as_moves_when_games_run_together() -> None:
    pgn = '[Result "*"]\n\n1. e4 *\n[Event "B"]\n[Result "*"]\n\n1. d4 *\n'
    result = check_pgn(pgn)
    assert result["ok"] is False
    assert result["dropped"] == [
        "lines 4 to 5: tags inside a game's moves are not read as tags; "
        "leave a blank line between games"
    ]


def test_check_pgn_reports_a_result_that_disagrees_with_the_result_tag() -> None:
    result = check_pgn('[Result "1-0"]\n\n1. e4 0-1\n')
    assert result["ok"] is False
    assert "0-1" in result["games"][0]["errors"][0]


def test_check_pgn_reports_movetext_without_a_result() -> None:
    result = check_pgn("1. e4 e5\n")
    assert result["ok"] is False
    assert "result" in result["games"][0]["errors"][0]


def test_check_pgn_reports_a_result_the_final_position_contradicts() -> None:
    mated = "1. f3 e5 2. g4 Qh4#"
    (game,) = check_pgn(f'[Result "1-0"]\n\n{mated} 1-0\n')["games"]
    assert game["errors"] == ["the game ends in checkmate, so its result is 0-1, not 1-0"]
    assert check_pgn(f'[Result "*"]\n\n{mated} *\n')["ok"] is True
