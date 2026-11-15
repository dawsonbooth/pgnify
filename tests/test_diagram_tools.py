"""The live tests need the diagrams extra and the reader's weights: `just models`, or a checkout's
models/."""

import itertools
from collections.abc import Callable
from pathlib import Path

import chess
import chess.svg
import pymupdf
import pytest
from support import (
    EXERCISES,
    FORK,
    PROSE,
    SKEWER,
    exercises,
    needs_tesseract,
    png,
    stored_sideways,
)

from pgnify import tesseract
from pgnify.chess_tools import render_board
from pgnify.diagram_tools import problems, read_diagram, read_page, settle
from pgnify.doc_tools import doc_page, doc_text

pytestmark = pytest.mark.usefixtures("checkout_weights")


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
def test_read_diagram_reads_each_board_of_a_page_doc_page_showed_by_its_box(tmp_path: Path) -> None:
    left, right = tmp_path / "left.png", tmp_path / "right.png"
    render_board(FORK, left)
    render_board(SKEWER, right)
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((40, 40), "Two diagrams side by side", fontsize=12)
    page.insert_image(pymupdf.Rect(40, 60, 280, 300), filename=str(left))
    page.insert_image(pymupdf.Rect(320, 60, 560, 300), filename=str(right))
    book = tmp_path / "pair.pdf"
    doc.save(book)
    shown = doc_page(str(book), 1, str(tmp_path / "shown"))
    # A box in the page's points, as doc_search gives them, in the picture's pixels.
    scale = shown["width"] / 600
    for (x0, x1), board in (((30, 290), FORK), ((310, 570), SKEWER)):
        box = [x0 * scale, 50 * scale, x1 * scale, 310 * scale]
        assert read_diagram(shown["path"], box)["placement"] == board.split()[0]


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


# The placements of the two exercises' boards.
PLACEMENTS = [FORK.split()[0], SKEWER.split()[0]]


def _near(found: list[float], printed: pymupdf.Rect) -> bool:
    """Whether a board was found where its picture is printed: its squares, inside the margin
    python-chess draws its coordinates in, 15 of the picture's 390 units on each side."""
    inset = printed.width * 15 / 390
    squares = printed + pymupdf.Rect(inset, inset, -inset, -inset)
    return all(abs(a - b) < 1.5 for a, b in zip(found, squares, strict=True))


def _place(box: list[int], found_by: str, placement: str | None, doubts: int = 0) -> dict:
    """A place the search found, as the reader answered for it: a board of the placement, read
    within box, with doubts doubtful squares, or none."""
    if placement is None:
        return {"box": box, "found_by": found_by, "edge": 1.0, "reading": {"failure": "none"}}
    doubted = [{"square": "a1", "piece": "", "confidence": 0.5, "second": "P"}] * doubts
    reading = {"placement": placement, "board": box, "doubts": doubted}
    return {"box": box, "found_by": found_by, "edge": 1.0, "reading": reading}


def test_settle_keeps_one_reading_of_each_board_the_one_with_fewest_doubts() -> None:
    boards, unread = settle(
        [
            _place([0, 0, 100, 100], "square", "8/8/8/8/8/8/8/K6k", doubts=2),
            _place([2, 1, 101, 99], "two lines", "8/8/8/8/8/8/8/K5k1"),
            _place([0, 300, 100, 400], "one line", None),
        ]
    )
    (board,) = boards
    assert board["reading"]["placement"] == "8/8/8/8/8/8/8/K5k1"
    assert board["other_readings"] == ["8/8/8/8/8/8/8/K6k"]
    assert unread == []


def test_settle_lists_a_square_frame_the_reader_read_no_board_in_unless_a_board_fills_it() -> None:
    boards, unread = settle(
        [
            _place([0, 0, 100, 100], "square", "8/8/8/8/8/8/8/K6k"),
            _place([1, 1, 99, 99], "square", None),
            _place([0, 300, 100, 400], "square", None),
            _place([200, 300, 300, 400], "two lines", None),
        ]
    )
    assert len(boards) == 1
    assert unread == [
        {"box": [0, 300, 100, 400], "why": "a square frame in which the reader read no board"}
    ]


def test_settle_lists_a_board_the_reader_read_no_piece_on() -> None:
    empty = {"failure": "no piece read on the board", "board": [205, 5, 395, 195]}
    boards, unread = settle(
        [{"box": [200, 0, 400, 200], "found_by": "two lines", "edge": 0.5, "reading": empty}]
    )
    assert boards == []
    assert unread == [
        {"box": [205, 5, 395, 195], "why": "a board on which the reader read no piece"}
    ]


def test_settle_lists_a_board_the_reader_doubted_every_square_of() -> None:
    # Such as a crosstable, ruled in squares, whose names and scores the reader reads as pieces.
    boards, unread = settle([_place([0, 0, 100, 100], "square", "QqQQ4/8/8/8/8/8/8/8", doubts=64)])
    assert boards == []
    assert unread == [
        {"box": [0, 0, 100, 100], "why": "a board on which the reader doubted every square"}
    ]


def test_settle_leaves_out_a_sliver_the_reader_found_corners_in() -> None:
    sliver = {"failure": "no piece read on the board", "board": [210, 20, 230, 80]}
    place = {"box": [200, 0, 400, 200], "found_by": "two lines", "edge": 0.5, "reading": sliver}
    assert settle([place]) == ([], [])


def test_only_a_board_read_keeps_the_places_it_covers_from_being_read() -> None:
    pytest.importorskip("cv2")
    from pgnify.recogniser import frames

    inside = {"box": [10, 10, 110, 110], "found_by": "square"}
    # A place spanning several boards, which the reader rejected, is no board, and covers nothing.
    assert not frames.covered(inside, [])
    assert frames.covered(inside, [[12, 12, 108, 108]])
    beside = {"box": [100, 10, 200, 110], "found_by": "one line"}
    assert frames.covered(beside, [[12, 12, 108, 108]])
    assert not frames.covered({**beside, "found_by": "square"}, [[12, 12, 108, 108]])


def test_settle_gives_boards_from_the_top_down_and_left_to_right_side_by_side() -> None:
    boards, _ = settle(
        [
            _place([300, 10, 400, 110], "square", "8/8/8/8/8/8/8/K6k"),
            _place([0, 200, 100, 300], "square", "8/8/8/8/8/8/8/K5k1"),
            _place([0, 15, 100, 115], "square", "8/8/8/8/8/8/8/K4k2"),
        ]
    )
    assert [board["reading"]["placement"] for board in boards] == [
        "8/8/8/8/8/8/8/K4k2",
        "8/8/8/8/8/8/8/K6k",
        "8/8/8/8/8/8/8/K5k1",
    ]


def test_the_search_finds_a_board_whose_frame_a_scan_broke_by_two_of_its_lines() -> None:
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from pgnify.recogniser import frames

    page = np.full((1000, 700), 255, np.uint8)
    page[200:203, 150:550] = 0  # the top and bottom lines, whole
    page[597:600, 150:550] = 0
    for top in range(200, 600, 40):  # the sides, broken every 40 pixels
        page[top : top + 30, 150:153] = 0
        page[top : top + 30, 547:550] = 0
    (pair,) = [place for place in frames.candidates(page) if place["found_by"] == "two lines"]
    assert all(abs(a - b) <= 3 for a, b in zip(pair["box"], [150, 200, 550, 600], strict=True))


@pytest.mark.live
def test_read_diagram_finds_each_board_on_a_page_with_the_text_beside_it(tmp_path: Path) -> None:
    result = read_page(str(exercises(tmp_path)), 1, str(tmp_path / "shown"))
    assert [board["placement"] for board in result["boards"]] == PLACEMENTS
    assert result["text_source"] == "the page's text layer"
    for board, (box, _, (label, side), players) in zip(result["boards"], EXERCISES, strict=True):
        assert _near(board["box"], box)
        assert [line["text"] for line in board["text"]["left"]] == [label, side]
        assert [line["text"] for line in board["text"]["below"]] == [players]
        assert board["found_by"] == "square" and board["doubts"] == []
    # The second exercise's label stands between the boards, beside both.
    assert [line["text"] for line in result["boards"][0]["text"]["right"]] == ["2", "B"]
    assert result["unread"] == []


@pytest.mark.live
def test_read_diagram_gives_the_text_beside_each_board_by_side_as_the_page_is_shown(
    tmp_path: Path,
) -> None:
    book = stored_sideways(tmp_path, exercises(tmp_path))
    result = read_page(str(book), 1, str(tmp_path / "shown"))
    assert [board["placement"] for board in result["boards"]] == PLACEMENTS
    for board, (_, _, (label, side), players) in zip(result["boards"], EXERCISES, strict=True):
        assert [line["text"] for line in board["text"]["left"]] == [label, side]
        assert [line["text"] for line in board["text"]["below"]] == [players]


@pytest.mark.live
def test_read_diagram_gives_a_boards_box_on_a_turned_page_in_its_points_unturned(
    tmp_path: Path,
) -> None:
    result = read_page(str(exercises(tmp_path, rotated=True)), 1, str(tmp_path / "shown"))
    assert [board["placement"] for board in result["boards"]] == PLACEMENTS
    for board, (box, *_) in zip(result["boards"], EXERCISES, strict=True):
        assert _near(board["box"], box)


@pytest.mark.live
def test_read_diagram_finds_each_board_inside_a_frame_drawn_round_several(tmp_path: Path) -> None:
    result = read_page(str(exercises(tmp_path, boxed=True)), 1, str(tmp_path / "shown"))
    assert [board["placement"] for board in result["boards"]] == PLACEMENTS


@pytest.mark.live
def test_read_diagram_finds_no_board_on_a_page_of_prose(two_pages: Path, tmp_path: Path) -> None:
    result = read_page(str(two_pages), 2, str(tmp_path))
    assert result["boards"] == [] and result["unread"] == []


@pytest.mark.live
@needs_tesseract
def test_read_diagram_reads_the_text_beside_a_scanned_board_with_tesseract(tmp_path: Path) -> None:
    result = read_page(str(exercises(tmp_path, scanned=True)), 1, str(tmp_path / "shown"))
    assert [board["placement"] for board in result["boards"]] == PLACEMENTS
    assert "reading of the strips beside each board, a machine reading" in result["text_source"]
    for board, (_, _, (label, side), players) in zip(result["boards"], EXERCISES, strict=True):
        assert [line["text"] for line in board["text"]["left"]] == [label, side]
        assert [line["text"] for line in board["text"]["below"]] == [players]


@pytest.mark.live
@pytest.mark.parametrize(
    ("script", "why"),
    [
        ("echo 'Error in pixReadMem' >&2; exit 1", "Tesseract failed: Error in pixReadMem"),
        ("sleep 5", "Tesseract gave no answer within 2 seconds"),
    ],
)
def test_read_diagram_gives_a_scans_boards_and_why_no_text_where_tesseract_fails(
    fake_tesseract: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    script: str,
    why: str,
) -> None:
    fake_tesseract(f'if [ "$1" = --version ]; then echo "tesseract 5.5.0"; exit 0; fi; {script}')
    monkeypatch.setattr(tesseract, "TIMEOUT", 2)
    result = read_page(str(exercises(tmp_path, scanned=True)), 1, str(tmp_path / "shown"))
    assert [board["placement"] for board in result["boards"]] == PLACEMENTS
    assert result["text_source"] == f"none: the page has no text layer, and {why}"
    assert [board["text"] for board in result["boards"]] == [{}, {}]


@pytest.mark.live
@needs_tesseract
def test_doc_text_with_ocr_leaves_the_boards_out_of_its_reading(tmp_path: Path) -> None:
    scan = str(exercises(tmp_path, scanned=True))
    (page,) = doc_text(scan, 1, ocr=True, out_dir=str(tmp_path / "shown"))["pages"]
    assert len(page["boards_left_out"]) == 2
    assert page["text"].startswith("Exercises\n")
    assert PROSE in page["text"]


# An ending the orientation model takes for a board printed from Black's side, with a score of
# 0.79, though it is printed from White's; and one it doubts, with a score of 0.37.
MISREAD = "8/1K6/5R2/8/8/2P5/4P3/1k6"
DOUBTED = "8/8/4k3/5P2/8/2bB4/8/k7"
# One the model reads right, as printed from White's side, with a score of 0.52; turned round it
# is not a position, since both kings are in check.
UNSURE = "8/2Q2q2/6K1/8/8/5B2/5P2/6k1"


def _printed(
    placement: str, path: Path, coordinates: bool = True, side: chess.Color = chess.WHITE
) -> None:
    """Draw a position from the side given, with or without the files and ranks printed."""
    board = chess.Board(f"{placement} w - - 0 1")
    svg = chess.svg.board(board, size=400, coordinates=coordinates, orientation=side)
    with pymupdf.open("svg", svg.encode()) as doc:
        doc[0].get_pixmap().save(str(path))


@pytest.mark.live
@needs_tesseract
def test_read_diagram_takes_the_side_a_board_is_printed_from_by_the_letters_under_it(
    tmp_path: Path,
) -> None:
    picture = tmp_path / "white.png"
    _printed(MISREAD, picture)
    reading = read_diagram(str(picture))
    assert reading["placement"] == MISREAD
    assert reading["flipped"] is False
    assert reading["orientation_doubt"] is None


@pytest.mark.live
def test_read_diagram_reports_the_orientation_doubtful_where_no_letters_settle_it(
    tmp_path: Path,
) -> None:
    misread, doubted, clear = (tmp_path / name for name in ("misread.png", "doubted.png", "a.png"))
    _printed(MISREAD, misread, coordinates=False)
    _printed(DOUBTED, doubted, coordinates=False)
    _printed(SKEWER.split()[0], clear, coordinates=False)
    # The net's call stands, with its score: here a wrong one, which only the letters correct.
    reading = read_diagram(str(misread))
    assert reading["flipped"] is True
    assert reading["orientation_doubt"] == pytest.approx(0.79, abs=0.05)
    assert read_diagram(str(doubted))["orientation_doubt"] == pytest.approx(0.37, abs=0.05)
    assert read_diagram(str(clear))["orientation_doubt"] is None


def _letters_read(monkeypatch: pytest.MonkeyPatch, letters: str) -> list[str]:
    """Make Tesseract read these letters, one to a file, for every board; the list it fills
    holds what it was asked to read."""
    asked: list[str] = []
    reads = itertools.cycle(letters)

    def read_character(picture: bytes, characters: str) -> str:
        asked.append(characters)
        return next(reads)

    monkeypatch.setattr(tesseract, "read_character", read_character)
    return asked


@pytest.mark.live
@pytest.mark.parametrize(
    ("letters", "turned"),
    [("abcdefgh", True), ("abcxxxxx", True), ("abxxxxxx", False), ("hgfedcba", False)],
)
def test_the_letters_turn_a_board_the_model_calls_from_blacks_side_only_by_a_margin_of_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, letters: str, turned: bool
) -> None:
    picture = tmp_path / "board.png"
    _printed(MISREAD, picture, coordinates=False)
    _letters_read(monkeypatch, letters)
    reading = read_diagram(str(picture))
    assert reading["placement"] == (MISREAD if turned else MISREAD[::-1])
    assert reading["flipped"] is not turned
    expected = None if turned or letters == "hgfedcba" else pytest.approx(0.79, abs=0.05)
    assert reading["orientation_doubt"] == expected


@pytest.mark.live
def test_the_letters_turn_a_board_the_model_calls_from_whites_side_and_problems_follow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picture = tmp_path / "board.png"
    _printed(UNSURE, picture, coordinates=False)
    _letters_read(monkeypatch, "abcdefgh")
    agreed = read_diagram(str(picture))
    assert (agreed["placement"], agreed["flipped"]) == (UNSURE, False)
    assert agreed["orientation_doubt"] is None and agreed["problems"] == []
    _letters_read(monkeypatch, "hgfedcba")
    turned = read_diagram(str(picture))
    assert (turned["placement"], turned["flipped"]) == (UNSURE[::-1], True)
    assert turned["orientation_doubt"] is None
    assert turned["problems"] == problems(UNSURE[::-1]) == ["opposite_check"]


@pytest.mark.live
def test_the_letters_turn_the_squares_of_a_doubt_with_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picture = tmp_path / "board.png"
    svg = chess.svg.board(chess.Board(f"{UNSURE} w - - 0 1"), size=400, coordinates=False)
    with pymupdf.open("svg", svg.encode()) as doc:
        pixels = doc[0].get_pixmap()
    # Grey over most of the queen on c7, which the reader then doubts.
    pixels.set_rect(pymupdf.IRect(110, 60, 140, 95), (140, 140, 140))
    pixels.save(str(picture))
    _letters_read(monkeypatch, "abcdefgh")
    [doubt] = read_diagram(str(picture))["doubts"]
    assert (doubt["square"], doubt["piece"]) == ("c7", "Q")
    _letters_read(monkeypatch, "hgfedcba")
    [doubt] = read_diagram(str(picture))["doubts"]
    assert (doubt["square"], doubt["piece"]) == ("f2", "Q")


@pytest.mark.live
def test_the_letters_are_not_read_where_the_model_is_sure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    picture = tmp_path / "board.png"
    _printed(SKEWER.split()[0], picture, coordinates=False)
    asked = _letters_read(monkeypatch, "hgfedcba")
    reading = read_diagram(str(picture))
    assert reading["placement"] == SKEWER.split()[0] and reading["flipped"] is False
    assert reading["orientation_doubt"] is None
    assert asked == []


def _page(
    folder: Path, placement: str, under: str, letters: bool = False, above: bool = False
) -> Path:
    """A one-page PDF of a framed board of the placement, with the text under it: its file
    letters, if letters, one to a file, or a caption; and the letters above it, if above."""
    picture = folder / "board.png"
    _printed(placement, picture, coordinates=False)
    doc = pymupdf.open()
    page = doc.new_page(width=460, height=400)
    box = pymupdf.Rect(100, 100, 300, 300)
    page.insert_image(box, filename=str(picture))
    page.draw_rect(box, color=(0, 0, 0), width=1.5)
    for index, letter in enumerate("abcdefgh"):
        x = box.x0 + (index + 0.5) * box.width / 8 - 3
        if above:
            page.insert_text((x, box.y0 - 8), letter, fontsize=11)
        if letters:
            page.insert_text((x, box.y1 + 16), letter, fontsize=11)
    if not letters:
        page.insert_text((110, box.y1 + 20), under, fontsize=12)
    path = folder / "page.pdf"
    doc.save(path)
    return path


@pytest.mark.live
@needs_tesseract
def test_read_page_takes_the_side_a_board_is_printed_from_by_the_letters_under_it(
    tmp_path: Path,
) -> None:
    path = _page(tmp_path, MISREAD, "", letters=True)
    [board] = read_page(str(path), 1, str(tmp_path / "shown"))["boards"]
    assert (board["placement"], board["flipped"], board["orientation_doubt"]) == (
        MISREAD,
        False,
        None,
    )


@pytest.mark.live
@needs_tesseract
def test_read_page_leaves_a_board_as_it_was_read_where_a_caption_stands_under_it(
    tmp_path: Path,
) -> None:
    caption = "TEST 136. Black to play."
    sure = _page(tmp_path, SKEWER.split()[0], caption)
    [board] = read_page(str(sure), 1, str(tmp_path / "shown"))["boards"]
    assert (board["placement"], board["flipped"]) == (SKEWER.split()[0], False)
    assert board["orientation_doubt"] is None
    # Where the model is unsure the caption is read, and decides nothing.
    unsure = _page(tmp_path, MISREAD, caption)
    [board] = read_page(str(unsure), 1, str(tmp_path / "shown"))["boards"]
    assert (board["placement"], board["flipped"]) == (MISREAD[::-1], True)
    assert board["orientation_doubt"] == pytest.approx(0.79, abs=0.05)


@pytest.mark.live
@needs_tesseract
def test_the_letters_above_a_board_do_not_decide_the_side_it_is_printed_from(
    tmp_path: Path,
) -> None:
    path = _page(tmp_path, MISREAD, "", above=True)
    [board] = read_page(str(path), 1, str(tmp_path / "shown"))["boards"]
    assert (board["placement"], board["flipped"]) == (MISREAD[::-1], True)
    assert board["orientation_doubt"] == pytest.approx(0.79, abs=0.05)
