import re
from pathlib import Path

import pytest

from pgnify.chess_tools import read_games
from pgnify.doc_tools import doc_text
from pgnify.spans import Span, SpannedGameSpec, build_pgn, copy_span


def test_doc_text_numbers_each_line_when_asked(primer: Path) -> None:
    text = doc_text(str(primer), 9, numbered=True)["pages"][0]["text"]
    assert "6| 1.Kg7! h4 2.Kf6 Kb6\n" in text
    assert text.startswith("1| of 1921.\n")


def test_a_span_on_one_line_is_copied_from_its_first_words_to_its_last(primer: Path) -> None:
    span: Span = {"page": 9, "line": 7, "first": "If Black", "last": "instead,"}
    assert copy_span(str(primer), span) == "If Black pushes with 2...h3 instead,"


def test_a_span_over_lines_is_copied_with_the_lines_joined(primer: Path) -> None:
    span: Span = {
        "page": 9,
        "line": 10,
        "first": "The white king",
        "last_line": 12,
        "last": "in time.",
    }
    assert copy_span(str(primer), span) == (
        "The white king walks down the long diagonal, and every step brings it closer both to "
        "the h-pawn and to its own pawn on c6. Black cannot deal with both threats in time."
    )


def test_a_span_may_begin_and_end_within_its_lines(primer: Path) -> None:
    span: Span = {"page": 9, "line": 7, "first": "White plays", "last_line": 8, "last": "Kb7"}
    assert copy_span(str(primer), span) == "White plays 3.Ke7 h2 4.c7 Kb7"


def test_a_line_ending_in_a_hyphen_joins_the_next_directly(hyphenated: Path) -> None:
    span: Span = {"page": 1, "line": 1, "first": "The pawn", "last_line": 2, "last": "runner,"}
    assert copy_span(str(hyphenated), span) == "The pawn is a well-known runner,"


@pytest.mark.parametrize(
    ("span", "says"),
    [
        ({"page": 9, "line": 14, "first": "the", "last": "drawn."}, "holds 'the' 3 times"),
        ({"page": 9, "line": 13, "first": "Kxc7", "last": "Kxh2"}, "does not hold 'Kxc7'"),
        ({"page": 9, "line": 8, "first": "Kb7", "last": "queen"}, "does not hold 'queen'"),
        ({"page": 9, "line": 8, "first": "both sides", "last": "Kb7"}, "come before"),
        ({"page": 9, "line": 99, "first": "If", "last": "queen."}, "no line 99"),
        ({"page": 9, "line": 8, "first": "Kb7", "last_line": 7, "last": "h3"}, "before line 8"),
        ({"page": 99, "line": 1, "first": "If", "last": "queen."}, "no page 99"),
    ],
)
def test_copy_span_refuses_a_span_the_page_does_not_hold_as_given(
    primer: Path, span: Span, says: str
) -> None:
    with pytest.raises(ValueError, match=says):
        copy_span(str(primer), span)


def test_build_pgn_copies_spans_and_text_into_comments(primer: Path) -> None:
    spec: SpannedGameSpec = {
        "comment": [{"page": 9, "line": 4, "first": "White\u2019s king", "last": "h5,"}],
        "moves": [
            {
                "san": "e4",
                "comment": [
                    "As the book says:",
                    {"page": 9, "line": 14, "first": "The king", "last_line": 15, "last": "drawn."},
                ],
            },
            {"san": "e5"},
        ],
    }
    result = build_pgn(spec, book=str(primer))
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert game.comment == "White\u2019s king on h8 is outside the square of the black pawn on h5,"
    first = game.next()
    assert first is not None and first.comment == (
        "As the book says: The king enters the square of the h-pawn just in time and the game is "
        "drawn."
    )
    assert [copied["path"] for copied in result["copied"]] == ["comment", "1w"]
    assert "repeated" not in result


def test_a_span_that_cannot_be_copied_fails_the_build_and_saves_nothing(
    primer: Path, tmp_path: Path
) -> None:
    spec: SpannedGameSpec = {
        "moves": [
            {"san": "e4", "comment": [{"page": 9, "line": 14, "first": "the", "last": "drawn."}]}
        ]
    }
    result = build_pgn(spec, name="game", store=str(tmp_path), book=str(primer))
    assert result["ok"] is False and result["saved"] is None
    assert not (tmp_path / "game.pgn").exists()
    assert result["errors"][0]["path"] == "1w"
    assert "comment part 1" in result["errors"][0]["message"]


def test_a_span_copied_twice_is_reported(primer: Path) -> None:
    span = {"page": 9, "line": 7, "first": "If Black", "last": "instead,"}
    spec: SpannedGameSpec = {
        "moves": [{"san": "e4", "comment": [span]}, {"san": "e5", "comment": [span]}]
    }
    result = build_pgn(spec, book=str(primer))
    assert result["ok"] is True
    assert result["repeated"] == [
        {"path": "1b", "also_at": "1w", "text": "If Black pushes with 2...h3 instead,"}
    ]


def test_a_figurine_in_a_span_is_written_as_the_piece_its_move_reads(
    inline_picture: Path,
) -> None:
    line = doc_text(str(inline_picture), 1, numbered=True)["pages"][0]["text"].splitlines()[0]
    (identifier,) = re.findall(r"\[picture \d+: ([0-9a-f]{6})\]", line)
    spec: SpannedGameSpec = {
        "moves": [
            {"san": "e4"},
            {"san": "e5"},
            {
                "san": f"[{identifier}]c3",
                "comment": [{"page": 1, "line": 1, "first": "2", "last": "c3"}],
            },
        ]
    }
    result = build_pgn(spec, book=str(inline_picture))
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert list(game.mainline())[2].comment == "2 Nc3"


def test_build_pgn_without_a_book_copies_no_span() -> None:
    spec: SpannedGameSpec = {
        "moves": [{"san": "e4", "comment": [{"page": 9, "line": 7, "first": "If", "last": "h3"}]}]
    }
    result = build_pgn(spec)
    assert result["ok"] is False
    assert result["errors"][0]["message"] == "name the book to copy spans from"


def test_build_pgn_reports_a_spans_missing_or_wrong_fields(primer: Path) -> None:
    spec: SpannedGameSpec = {
        "moves": [
            {"san": "e4", "comment": [{"page": 9, "line": 7, "first": "If Black"}]},
            {
                "san": "e5",
                "comment": [{"page": "9", "line": 7, "first": "If", "last": "h3", "to": 8}],
            },
        ]
    }
    result = build_pgn(spec, book=str(primer))
    messages = [error["message"] for error in result["errors"]]
    assert messages == [
        "comment part 1: a span is {page, line, first, last_line, last}, last_line optional: "
        "it has no last",
        "comment part 1: a span is {page, line, first, last_line, last}, last_line optional: "
        "not a field of a span: to; page should be a number",
    ]
