"""Tests of check_fen and render_board, which take a FEN or a placement alone."""

from pathlib import Path

import pymupdf
from support import FORK

from pgnify.chess_tools import check_fen, render_board


def test_check_fen_accepts_a_full_fen() -> None:
    result = check_fen(FORK)
    assert result["ok"] is True
    assert result["side_to_move"] == "w"
    assert result["assumed"] == []


def test_check_fen_completes_a_placement_and_says_what_it_assumed() -> None:
    placement = FORK.split()[0]
    result = check_fen(placement)
    assert result["ok"] is True
    assert result["fen"].startswith(placement + " w")
    assert "side_to_move" in result["assumed"]


def test_check_fen_rejects_two_white_kings() -> None:
    result = check_fen("4k3/8/8/8/8/8/8/K3K3 w - - 0 1")
    assert result["ok"] is False
    assert "too_many_kings" in result["problems"]


def test_render_board_draws_a_position_400_pixels_square(tmp_path: Path) -> None:
    png = tmp_path / "board.png"
    render_board(FORK, png)
    drawn = pymupdf.Pixmap(str(png))
    assert (drawn.width, drawn.height) == (400, 400)
