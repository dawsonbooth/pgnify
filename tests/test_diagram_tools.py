"""The live tests need the diagrams extra and the reader's weights: `just models`, or a checkout's
models/."""

import os
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from support import FORK, ROOT, png

from pgnify.chess_tools import render_board
from pgnify.diagram_tools import problems, read_diagram

SKEWER = "6k1/5ppp/8/8/8/8/1Q3PPP/6K1 w - - 0 1"


@pytest.fixture(scope="module", autouse=True)
def checkout_weights() -> Iterator[None]:
    """Live tests use a checkout's models/ weights, if present, unless PGNIFY_WEIGHTS is set."""
    weights = ROOT / "models" / "chess-diagram-to-fen"
    with pytest.MonkeyPatch.context() as patch:
        if weights.is_dir() and "PGNIFY_WEIGHTS" not in os.environ:
            patch.setenv("PGNIFY_WEIGHTS", str(weights))
        yield


@pytest.mark.live
def test_read_diagram_reads_the_placement_of_a_drawn_board(tmp_path: Path) -> None:
    board = tmp_path / "fork.png"
    render_board(FORK, board)
    reading = read_diagram(str(board))
    assert reading["ok"] is True
    assert reading["placement"] == FORK.split()[0]
    assert reading["flipped"] is False
    assert reading["doubts"] == []
    assert reading["problems"] == []


@pytest.mark.live
def test_read_diagram_says_why_when_a_picture_holds_no_board(tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    blank.write_bytes(png(200, 200, 255))
    reading = read_diagram(str(blank))
    assert reading["ok"] is False
    assert reading["reason"]


@pytest.mark.live
def test_read_diagram_reads_each_of_two_boards_by_its_box_and_locates_it_in_the_whole_picture(
    tmp_path: Path,
) -> None:
    left, right = tmp_path / "left.png", tmp_path / "right.png"
    render_board(FORK, left)
    render_board(SKEWER, right)
    doc = pymupdf.open()
    page = doc.new_page(width=820, height=400)
    page.insert_image(pymupdf.Rect(0, 0, 400, 400), filename=str(left))
    page.insert_image(pymupdf.Rect(420, 0, 820, 400), filename=str(right))
    pair = tmp_path / "pair.png"
    page.get_pixmap().save(str(pair))
    assert read_diagram(str(pair), [0, 0, 410, 400])["placement"] == FORK.split()[0]
    reading = read_diagram(str(pair), [410, 0, 820, 400])
    assert reading["placement"] == SKEWER.split()[0]
    assert reading["board"][0] > 410


@pytest.mark.live
def test_read_diagram_cuts_a_box_to_the_picture_and_refuses_one_outside_or_reversed(
    tmp_path: Path,
) -> None:
    board = tmp_path / "fork.png"
    render_board(FORK, board)
    assert read_diagram(str(board), [0, 0, 900, 900])["placement"] == FORK.split()[0]
    outside = read_diagram(str(board), [500, 500, 900, 900])
    assert outside["ok"] is False
    assert "outside the picture" in outside["reason"]
    reversed_box = read_diagram(str(board), [300, 0, 100, 400])
    assert reversed_box["ok"] is False
    assert "empty" in reversed_box["reason"]


@pytest.mark.live
def test_read_diagram_refuses_a_file_that_is_not_a_picture_without_naming_it(
    tmp_path: Path,
) -> None:
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not a picture")
    reading = read_diagram(str(junk))
    assert reading == {
        "ok": False,
        "reason": "the picture could not be read: UnidentifiedImageError",
    }


def test_read_diagram_says_to_install_the_diagrams_extra_when_the_reader_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pgnify.diagram_tools.NEEDED", ("no_module_pgnify_installs",))
    with pytest.raises(RuntimeError, match=r"pgnify\[diagrams\], then run pgnify setup"):
        read_diagram(str(tmp_path / "board.png"))


def test_read_diagram_says_to_run_setup_when_the_weights_are_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("pgnify.diagram_tools.NEEDED", ())
    monkeypatch.setenv("PGNIFY_WEIGHTS", str(tmp_path / "empty"))
    with pytest.raises(RuntimeError, match="weights are not installed: run pgnify setup"):
        read_diagram(str(tmp_path / "board.png"))


def test_problems_lists_what_makes_a_placement_impossible_with_either_side_to_move() -> None:
    assert problems("8/8/8/8/8/8/8/8") == ["no_white_king", "no_black_king", "empty"]
    assert problems("P5k1/8/8/8/8/8/8/6K1") == ["pawns_on_backrank"]
    # Black in check: no problem, since Black may be the side to move.
    assert problems("4k3/8/8/8/8/8/8/4R1K1") == []
    # Three pieces giving check: no move gives that, and with Black to move White is in check.
    assert problems("k3q3/8/8/8/1b6/3n4/8/4K3") == [
        "opposite_check",
        "too_many_checkers",
        "impossible_check",
    ]
