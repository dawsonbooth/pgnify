import pytest
from support import BLACK_FORK, FORK, RUY

from pgnify.chess_tools import validate_moves


def test_validate_moves_replays_a_legal_line_from_the_start() -> None:
    result = validate_moves("start", ["e4", "e5", "Nf3", "Nc6", "Bb5"])
    assert result["ok"] is True
    assert result["final_fen"] == RUY
    assert [p["san"] for p in result["plies"]] == ["e4", "e5", "Nf3", "Nc6", "Bb5"]


def test_validate_moves_reads_pgn_movetext() -> None:
    result = validate_moves("start", "1 e4 e5 2. Nf3! Nc6?! 3.Bb5 $1 1-0")
    assert result["ok"] is True, result["error"]
    assert result["final_fen"] == RUY


def test_validate_moves_reports_the_first_illegal_move_with_the_legal_moves_there() -> None:
    result = validate_moves("start", ["e4", "e4", "Nf3"])
    assert result["ok"] is False
    assert result["error"]["index"] == 1
    assert result["error"]["move"] == "e4"
    assert "e5" in result["error"]["legal_moves"]
    assert len(result["plies"]) == 1


def test_validate_moves_refuses_a_null_move() -> None:
    result = validate_moves("start", "1. e4 -- 2. d4")
    assert result["ok"] is False
    assert (result["error"]["index"], result["error"]["move"]) == (1, "--")


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("1. e4 e5 2. Nxf3", "'Nxf3' marks a capture, but the move captures nothing"),
        ("1. e4 e5 2. Qh5+", "'Qh5+' marks check, but the move gives no check"),
        ("1. e4 f5 2. Qh5#", "'Qh5#' marks mate, but the move does not mate"),  # 2...g6 blocks.
    ],
)
def test_validate_moves_refuses_a_capture_check_or_mate_mark_the_move_contradicts(
    line: str, message: str
) -> None:
    result = validate_moves("start", line)
    assert (result["error"]["index"], result["error"]["move"]) == (2, line.split()[-1])
    assert result["error"]["message"] == message


def test_validate_moves_accepts_a_check_the_source_leaves_unmarked() -> None:
    result = validate_moves("start", "1. e4 f5 2. Qh5")
    assert result["ok"] is True
    assert result["plies"][-1]["san"] == "Qh5+"


def test_validate_moves_accepts_the_marks_of_a_mate_and_of_an_en_passant_capture() -> None:
    mate = validate_moves("start", "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7#")
    en_passant = validate_moves("start", "1. e4 Nf6 2. e5 d5 3. exd6")
    assert (mate["ok"], en_passant["ok"]) == (True, True)


def test_validate_moves_takes_the_side_to_move_a_full_fen_gives() -> None:
    result = validate_moves(FORK, ["Nh7+", "Kg7", "Nxf6"])
    assert result["ok"] is True
    assert (result["side_to_move"], result["side_to_move_basis"]) == ("w", "given")


@pytest.mark.parametrize(
    "line", ["1...Ne2+ 2.Kf1 Nxc1", ["1...Ne2+", "2.Kf1", "Nxc1"], "1... Ne2+ 2. Kf1 Nxc1"]
)
def test_validate_moves_takes_the_side_to_move_from_a_black_move_number(
    line: str | list[str],
) -> None:
    result = validate_moves(BLACK_FORK, line)
    assert result["ok"] is True, result["error"]
    assert (result["side_to_move"], result["side_to_move_basis"]) == ("b", "notation")


def test_validate_moves_refuses_a_move_number_that_contradicts_the_side_to_move() -> None:
    result = validate_moves(FORK, "2...Kg7")
    assert result["ok"] is False
    assert result["error"]["index"] == 0
    assert "Black" in result["error"]["message"]


def test_validate_moves_refuses_dots_without_a_move_number() -> None:
    result = validate_moves(BLACK_FORK, "...Ne2+")
    assert result["ok"] is False
    assert result["error"]["move"] == "...Ne2+"


def test_validate_moves_deduces_black_to_move_when_only_black_can_play_the_line() -> None:
    result = validate_moves(BLACK_FORK, ["Ne2+", "Kf1", "Nxc1"])
    assert result["ok"] is True, result["error"]
    assert result["start_fen"] == BLACK_FORK + " b - - 0 1"
    assert (result["side_to_move"], result["side_to_move_basis"]) == ("b", "deduced")


def test_validate_moves_deduces_the_side_to_move_despite_a_wrong_check_mark() -> None:
    # Only Black can play a6 Ba4 Nf6 from the Ruy Lopez's placement: the wrong check mark is
    # reported as such, not as a line that neither side can play.
    result = validate_moves(RUY.split()[0], "a6+ Ba4 Nf6")
    assert (result["side_to_move"], result["side_to_move_basis"]) == ("b", "deduced")
    assert result["error"]["message"] == "'a6+' marks check, but the move gives no check"


def test_validate_moves_assumes_white_when_either_side_can_play_the_line() -> None:
    result = validate_moves("r3k3/8/8/8/8/8/8/R3K3", ["Ra5"])
    assert result["ok"] is True
    assert (result["side_to_move"], result["side_to_move_basis"]) == ("w", "assumed")


def test_validate_moves_says_when_neither_side_can_play_the_line() -> None:
    result = validate_moves(BLACK_FORK, ["Qh5"])
    assert result["ok"] is False
    assert result["side_to_move_basis"] == "assumed"
    assert "neither side" in result["error"]["message"]


def test_validate_moves_adds_no_neither_side_note_when_both_sides_can_play_the_line() -> None:
    # Either side can move a rook to a4, and for White a queen could too.
    result = validate_moves("r3k3/8/8/8/8/8/8/R2QK3", "[aaaaaa]a4")
    assert result["ok"] is False
    assert "could be Q or R" in result["error"]["message"]
    assert "neither side" not in result["error"]["message"]


def test_validate_moves_reads_each_figurine_as_the_only_piece_that_plays_the_line() -> None:
    # 2.[aaaaaa]f3 alone could be Nf3 or Qf3; only a knight also plays 2...[aaaaaa]c6.
    line = "1 e4 e5 2 [picture 1: aaaaaa] f3 [aaaaaa]c6 3 [bbbbbb]b5"
    result = validate_moves("start", line)
    assert result["ok"] is True, result["error"]
    assert [p["san"] for p in result["plies"]] == ["e4", "e5", "Nf3", "Nc6", "Bb5"]
    assert result["final_fen"] == RUY
    assert result["pictures"] == {"aaaaaa": "N", "bbbbbb": "B"}


def test_validate_moves_reads_separate_figurines_as_the_same_piece() -> None:
    result = validate_moves("start", "1 e4 e5 2 [aaaaaa]f3 [bbbbbb]c6 3 [aaaaaa]g5")
    assert result["ok"] is True, result["error"]
    assert result["pictures"] == {"aaaaaa": "N", "bbbbbb": "N"}


def test_validate_moves_names_the_pieces_a_figurine_could_be_instead_of_choosing() -> None:
    # A bishop, king, knight or queen can each go to e2, and no later move settles which.
    result = validate_moves("start", "1 e4 e5 2 [cccccc]e2")
    assert result["ok"] is False
    assert (result["error"]["index"], result["error"]["move"]) == (2, "[cccccc]e2")
    assert "could be B, K, N or Q" in result["error"]["message"]
    assert [p["san"] for p in result["plies"]] == ["e4", "e5"]


def test_validate_moves_does_not_let_a_check_mark_decide_a_figurines_piece() -> None:
    # The knight and the queen can both go to f3, and only the queen gives check there; a mark
    # the book may have wrong does not settle the figurine.
    result = validate_moves("5k2/8/8/8/8/8/8/3QK1N1 w - - 0 1", "1. [aaaaaa]f3+")
    assert "the picture aaaaaa could be N or Q here" in result["error"]["message"]


def test_validate_moves_lets_no_move_after_a_lines_last_figurine_decide_its_piece() -> None:
    # 3.Bc4 is legal only if the bishop went to e2, since anything else there blocks the f1
    # bishop; but it comes after the line's last figurine, and moves there decide nothing, since
    # one may be mistyped.
    result = validate_moves("start", "1 e4 e5 2 [bbbbbb]e2 Nc6 3 Bc4")
    assert result["error"]["index"] == 2
    assert "could be B, K, N or Q" in result["error"]["message"]


def test_validate_moves_says_where_a_line_stops_when_a_figurine_is_left_open() -> None:
    # Whether a knight or a queen went to f3, 3.d5 is not legal.
    result = validate_moves("start", "1 e4 e5 2 [aaaaaa]f3 d6 3 d5")
    assert result["error"]["index"] == 2
    assert "index 4, 'd5'" in result["error"]["message"]


def test_validate_moves_deduces_the_side_to_move_of_a_line_given_with_figurines() -> None:
    result = validate_moves(BLACK_FORK, ["[aaaaaa]e2+", "[bbbbbb]f1", "[aaaaaa]xc1"])
    assert result["ok"] is True, result["error"]
    assert (result["side_to_move"], result["side_to_move_basis"]) == ("b", "deduced")
    assert result["pictures"] == {"aaaaaa": "N", "bbbbbb": "K"}


@pytest.mark.parametrize(
    "line",
    [
        "e8=[aaaaaa] Kf6 [aaaaaa]e6+ Kg5 [aaaaaa]d5+",
        ["e8=[aaaaaa]", "Kf6", "[aaaaaa]e6+", "Kg5", "[aaaaaa]d5+"],
    ],
)
def test_validate_moves_reads_a_figurine_promotion_without_joining_it_to_the_next_move(
    line: str | list[str],
) -> None:
    # Only a queen plays both 2.[aaaaaa]e6+ and 3.[aaaaaa]d5+ from e8.
    result = validate_moves("8/4P1k1/8/8/8/8/8/K7 w - - 0 1", line)
    assert result["ok"] is True, result["error"]
    assert [p["san"] for p in result["plies"]] == ["e8=Q", "Kf6", "Qe6+", "Kg5", "Qd5+"]
