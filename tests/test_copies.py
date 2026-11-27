import re
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from support import write_text_epub

from pgnify import copies
from pgnify.chess_tools import read_games
from pgnify.copies import (
    Copy,
    CopyingGameSpec,
    CopyingMoveSpec,
    Place,
    _joined,
    _Pages,
    build_pgn,
    copy_passage,
)
from pgnify.doc_tools import lines_of


def _lines(book: Path, page: int) -> list[str]:
    """The lines of the page, as doc_text gives them."""
    with pymupdf.open(book) as doc:
        return lines_of(doc, page)


def _passage(book: Path, copy: Copy) -> tuple[str, Place, Place, list[tuple[str, str]]]:
    with _Pages(str(book)) as pages:
        return copy_passage(pages, copy)


def _copy(book: Path, page: int, begin: str, end: str, fix: list[list[str]] | None = None) -> str:
    copy: Copy = {"page": page, "from": begin, "to": end}
    if fix is not None:
        copy["fix"] = fix
    return _passage(book, copy)[0]


def test_a_copy_within_a_line_runs_from_its_first_words_to_its_last(primer: Path) -> None:
    assert _copy(primer, 9, "If Black", "instead,") == "If Black pushes with 2...h3 instead,"


def test_a_copy_may_begin_and_end_within_its_lines(primer: Path) -> None:
    assert _copy(primer, 9, "White plays", "Kb7") == "White plays 3.Ke7 h2 4.c7 Kb7"


def test_a_copy_over_lines_joins_them_and_ignores_white_space_in_the_words_given(
    primer: Path,
) -> None:
    expected = (
        "The white king walks down the long diagonal, and every step brings it closer both to "
        "the h-pawn and to its own pawn on c6. Black cannot deal with both threats in time."
    )
    assert _copy(primer, 9, "The white king", "in time.") == expected
    assert _copy(primer, 9, "The   white\nking", "in\n time.") == expected


def test_a_copy_across_a_paragraph_break_keeps_the_line_break(primer: Path) -> None:
    assert _copy(primer, 9, "White to move draws", "h5,") == (
        "White to move draws\nWhite\u2019s king on h8 is outside the square of the black "
        "pawn on h5,"
    )


def test_a_copy_across_a_page_break_joins_the_pages_like_two_lines(primer: Path) -> None:
    assert _lines(primer, 9)[0] == "of 1921."
    assert _copy(primer, 8, "Sometimes a king", "of 1921.") == (
        "Sometimes a king outside the square can still stop the pawn by doing two jobs at "
        "once. Richard Réti showed this in a famous study of 1921."
    )


def test_a_block_at_the_top_of_the_next_page_begins_a_line(primer: Path) -> None:
    text = _copy(primer, 9, "The king enters", "CHAPTER 2")
    assert text == (
        "The king enters the square of the h-pawn just in time and the game is drawn.\nCHAPTER 2"
    )


def test_a_line_ending_in_a_hyphen_joins_the_next_directly_in_a_pdf(hyphenated: Path) -> None:
    assert _copy(hyphenated, 1, "The pawn", "runner,") == "The pawn is a well-known runner,"
    # Where a line broke after a hyphen, the words may be given with or without a space.
    assert _copy(hyphenated, 1, "a well- known", "runner,") == "a well-known runner,"


def test_a_soft_hyphen_ends_a_line_and_is_removed_where_the_word_joins() -> None:
    lines = [("Black cannot stop the inevi\u00ad", False), ("table mate on the h-file.", False)]
    assert _joined([lines])[0] == "Black cannot stop the inevitable mate on the h-file."


def test_a_copy_in_a_pdf_stays_on_its_page_and_says_to_copy_the_next_pages_part_apart(
    two_pages: Path,
) -> None:
    assert _copy(two_pages, 1, "Nf3", "Nc6") == "Nf3 Nc6"
    with pytest.raises(ValueError) as error:
        _copy(two_pages, 1, "Nf3", "Endgame")
    message = str(error.value)
    assert "page 1 holds no 'Endgame'" in message
    assert message.endswith(
        ". A copy in a PDF stays on its page: end this copy with its last words on page 1, and "
        "copy the rest from page 2 as a copy of its own"
    )
    assert _copy(two_pages, 2, "Chapter 2", "Endgame") == "Chapter 2 Endgame"


def test_no_period_follows_a_window_that_ends_in_an_ellipsis(long_pages: Path) -> None:
    with pytest.raises(ValueError) as error:
        _passage(long_pages, {"page": 1, "from": "Page 1 line 20", "to": "x"})
    message = str(error.value)
    assert "…" in message and ". A copy in a PDF" not in message
    assert "… A copy in a PDF stays on its page: end this copy" in message


def test_a_copy_in_an_epub_may_run_over_one_page_break_but_not_two(primer: Path) -> None:
    assert _copy(primer, 8, "a famous study", "of 1921.") == "a famous study of 1921."
    with pytest.raises(ValueError, match="pages 8 and 9 hold no 'CHAPTER 3'"):
        _copy(primer, 8, "a famous study", "CHAPTER 3")


def test_the_words_a_copy_ends_with_on_its_own_page_win_over_those_on_the_next(
    primer: Path,
) -> None:
    # "pawn" stands in "stop the pawn" on page 8 and again on page 9.
    passage, _, end, _ = _passage(primer, {"page": 8, "from": "Sometimes a king", "to": "pawn"})
    assert passage == "Sometimes a king outside the square can still stop the pawn"
    assert end[0] == 8


def test_the_lines_of_a_page_join_without_a_space_after_an_ellipsis_before_a_figurine() -> None:
    def joined(*lines: str) -> str:
        return _joined([[(line, False) for line in lines]])[0]

    assert joined("2...", "[picture 5: f4c1b7] f6:") == "2...[picture 5: f4c1b7] f6:"
    assert joined("2\u2026", "\u2657b5") == "2\u2026\u2657b5"
    assert joined("2...", "Nf3") == "2... Nf3"
    assert joined("then", "[picture 5: f4c1b7] f6") == "then [picture 5: f4c1b7] f6"
    # A block begins a line of its own.
    assert _joined([[("2...", False), ("\u2657b5", True)]])[0] == "2...\n\u2657b5"


def test_a_copy_matches_straight_and_curved_quotes_alike_and_copies_the_books(
    tmp_path: Path,
) -> None:
    book = write_text_epub(
        tmp_path / "quotes.epub", "<p>He said \u201cno\u201d, and it\u2019s over.</p>"
    )
    assert _copy(book, 1, 'said "no",', "it's over.") == "said \u201cno\u201d, and it\u2019s over."
    assert _copy(book, 1, "said \u201cno\u201d,", "it\u2019s over.") == (
        "said \u201cno\u201d, and it\u2019s over."
    )


@pytest.mark.parametrize(
    ("copy", "says"),
    [
        ({"page": 9, "from": "Kxc7", "to": "Kxh2"}, "page 9 does not hold 'Kxc7'"),
        ({"page": 9, "from": "the", "to": "drawn."}, "page 9 holds 'the' 9 times"),
        ({"page": 9, "from": "Kb7", "to": "queen"}, "pages 9 and 10 hold no 'queen'"),
        # The words it ends with come before those it begins with.
        ({"page": 9, "from": "both sides", "to": "Kb7"}, "hold no 'Kb7'"),
        ({"page": 14, "from": "Index", "to": "queen."}, "page 14 holds no 'queen.'"),
        ({"page": 99, "from": "If", "to": "queen."}, "no page 99"),
        ({"page": 9, "from": " ", "to": "queen."}, "no words given"),
    ],
)
def test_a_copy_the_page_does_not_hold_as_given_is_refused_and_says_why(
    primer: Path, copy: Copy, says: str
) -> None:
    with pytest.raises(ValueError, match=says):
        _passage(primer, copy)


def _nearest(error: pytest.ExceptionInfo[ValueError]) -> str:
    """The text an error shows as the nearest to the words that were not found."""
    return (
        str(error.value).split("the nearest text is: ")[1].split(" A copy in a PDF")[0].rstrip(".")
    )


def test_an_error_for_words_that_are_not_there_shows_the_text_around_the_nearest_miss(
    primer: Path,
) -> None:
    with pytest.raises(ValueError) as error:
        _passage(primer, {"page": 9, "from": "Kxc7", "to": "Kxh2"})
    # Not the page: the words around the place that shares the most with "Kxc7", at Kxc6.
    assert "Kxc6 4.Kf4" in _nearest(error)
    assert "1.Kg7!" not in _nearest(error)


def test_the_window_an_error_shows_is_about_a_hundred_and_twenty_characters(
    primer: Path, long_pages: Path
) -> None:
    with pytest.raises(ValueError) as error:
        _passage(primer, {"page": 9, "from": "If Black pushes with 2...h3 insted,", "to": "x"})
    assert "pushes with 2...h3 instead, White plays" in _nearest(error)
    with pytest.raises(ValueError) as error:
        _passage(long_pages, {"page": 1, "from": "Page 1 line 20 " + "y" * 200, "to": "x"})
    # However long the words looked for are, and however much text the page has.
    assert 20 < len(_nearest(error)) <= 125


def test_an_error_for_words_that_are_there_twice_shows_where(primer: Path) -> None:
    with pytest.raises(ValueError) as error:
        _passage(primer, {"page": 9, "from": "Black", "to": "drawn."})
    message = str(error.value)
    assert "holds 'Black' 2 times" in message
    assert "If Black pushes with" in message and "Black cannot deal" in message


def test_an_error_for_the_words_it_ends_with_points_at_the_nearest_miss_after_where_it_begins(
    primer: Path,
) -> None:
    with pytest.raises(ValueError) as error:
        _passage(primer, {"page": 9, "from": "If Black", "to": "Kb8"})
    message = str(error.value)
    assert message.startswith("pages 9 and 10 hold no 'Kb8'")
    assert "Kb7 5.Kd7" in _nearest(error)
    # A place before where the copy begins is not the one.
    with pytest.raises(ValueError) as error:
        _passage(primer, {"page": 9, "from": "White plays", "to": "Kb6"})
    assert "1.Kg7!" not in _nearest(error)


def test_the_words_it_ends_with_may_be_the_ones_it_begins_with(primer: Path) -> None:
    assert _copy(primer, 9, "Kxc6", "Kxc6") == "Kxc6"


def test_a_passage_ends_at_the_first_place_for_its_last_words_after_its_first(
    primer: Path,
) -> None:
    # "h2" stands in "h2 4.c7" and again in "h2 6.Kxh2"; a passage from "Kf4" ends at the second.
    assert _copy(primer, 9, "2...h3", "h2") == "2...h3 instead, White plays 3.Ke7 h2"
    assert _copy(primer, 9, "4.Kf4", "h2") == "4.Kf4 h3 5.Kg3 h2"


def test_a_fix_replaces_each_printed_text_once(primer: Path) -> None:
    fix = [["Kb6", "Kb7"], ["2...h3  instead,", "2...h4 instead,"]]
    assert _copy(primer, 9, "1.Kg7!", "4.c7", fix) == (
        "1.Kg7! h4 2.Kf6 Kb7\nIf Black pushes with 2...h4 instead, White plays 3.Ke7 h2 4.c7"
    )


@pytest.mark.parametrize(
    ("fix", "says"),
    [
        ([["Kxx", "Kb7"]], "fix 'Kxx' does not occur in the passage; the nearest text is: 1.Kg7!"),
        ([["2.", "3."]], "fix '2.' occurs 2 times"),
        ([["Kb", "Kc"], ["Kb6", "Kb7"]], "two fixes replace the same words"),
        ([[" ", "a"]], "no printed text"),
        # "Kb6" ends a line of the book's, and "If" begins the next.
        ([["Kb6 If", "Kb6, if"]], "fix 'Kb6 If' runs over a line break of the passage"),
    ],
)
def test_a_fix_that_does_not_stand_exactly_once_is_refused(
    primer: Path, fix: list[list[str]], says: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(says) if "." in says else says):
        _copy(primer, 9, "1.Kg7!", "4.c7", fix)


def test_a_fix_may_stand_inside_a_word_and_an_empty_written_leaves_one_space(
    primer: Path,
) -> None:
    assert _copy(primer, 9, "1.Kg7!", "4.c7", [["Kg", "Kh"]]).startswith("1.Kh7! h4")
    # Between spaces, at the start of the passage, and at the end of a line.
    fixes = [["h4", ""], ["Kb6", ""], ["Why", ""]]
    assert _copy(primer, 9, "1.Kg7!", "4.c7", fixes[:1]).startswith("1.Kg7! 2.Kf6 Kb6\nIf")
    assert _copy(primer, 9, "1.Kg7!", "4.c7", fixes[1:2]).startswith("1.Kg7! h4 2.Kf6\nIf")
    assert _copy(primer, 9, "1.Kg7!", "4.c7", [["1.Kg7!", ""]]).startswith("h4 2.Kf6")
    assert _copy(primer, 9, "Why the", "Helps", [["Why", ""]]) == "the Diagonal Helps"
    assert _copy(primer, 9, "Why the", "Helps", [["Helps", ""]]) == "Why the Diagonal"


def _built(book: Path, spec: CopyingGameSpec, **arguments: Any) -> dict[str, Any]:
    return build_pgn(spec, book=str(book), **arguments)


def test_build_pgn_copies_into_comments_and_reports_each_copy(primer: Path) -> None:
    spec: CopyingGameSpec = {
        "comment": [{"page": 9, "from": "White\u2019s king", "to": "h5,"}],
        "moves": [
            {
                "san": "e4",
                "comment": [
                    "As the book says:",
                    {"page": 9, "from": "The king", "to": "drawn."},
                ],
            },
            {"san": "e5"},
        ],
    }
    result = _built(primer, spec)
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert game.comment == "White\u2019s king on h8 is outside the square of the black pawn on h5,"
    first = game.next()
    assert first is not None and first.comment == (
        "As the book says: The king enters the square of the h-pawn just in time and the game is "
        "drawn."
    )
    assert result["copied"] == [
        {
            "path": "comment",
            "text": "White\u2019s king on h8 is … the black pawn on h5,",
            "words": 14,
        },
        {
            "path": "1w",
            "text": "The king enters the square … and the game is drawn.",
            "words": 16,
        },
    ]
    assert "repeated" not in result and "fixed" not in result


def test_a_fix_is_applied_and_reported_with_its_path(primer: Path) -> None:
    copy = {"page": 9, "from": "If Black", "to": "4.c7", "fix": [["2...h3", "2...h4"]]}
    spec: CopyingGameSpec = {"moves": [{"san": "e4"}, {"san": "e5", "comment": [copy]}]}
    result = _built(primer, spec)
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert (
        list(game.mainline())[1].comment
        == "If Black pushes with 2...h4 instead, White plays 3.Ke7 h2 4.c7"
    )
    assert result["fixed"] == [{"path": "1b", "printed": "2...h3", "written": "2...h4"}]


@pytest.mark.parametrize(
    ("copy", "says"),
    [
        ({"page": 9, "from": "Kxc7", "to": "Kxh2"}, "does not hold 'Kxc7'"),
        ({"page": 9, "from": "the", "to": "drawn."}, "holds 'the' 9 times"),
        ({"page": 9, "from": "If Black", "to": "queen"}, "hold no 'queen'"),
        ({"page": 9, "from": "If Black", "to": "4.c7", "fix": [["Kxx", "Kb7"]]}, "fix 'Kxx'"),
        ({"page": 9, "from": "If Black", "to": "4.c7", "fix": [["h", "x"]]}, "fix 'h' occurs"),
    ],
)
def test_a_copy_that_cannot_be_made_fails_the_build_and_copies_and_saves_nothing(
    primer: Path, tmp_path: Path, copy: dict[str, Any], says: str
) -> None:
    spec: CopyingGameSpec = {"moves": [{"san": "e4", "comment": ["Text.", copy]}]}
    result = _built(primer, spec, name="game", store=str(tmp_path))
    assert result["ok"] is False and result["saved"] is None
    assert not (tmp_path / "game.pgn").exists()
    assert "copied" not in result and "fixed" not in result
    assert result["errors"][0]["path"] == "1w"
    assert says in result["errors"][0]["message"]
    assert result["errors"][0]["message"].startswith("comment part 2: ")


def test_a_passage_copied_twice_is_reported(primer: Path) -> None:
    copy = {"page": 9, "from": "If Black", "to": "instead,"}
    spec: CopyingGameSpec = {
        "moves": [{"san": "e4", "comment": [copy]}, {"san": "e5", "comment": [copy]}]
    }
    result = _built(primer, spec)
    assert result["ok"] is True
    assert result["repeated"] == [
        {"path": "1b", "also_at": "1w", "text": "If Black pushes with 2...h3 instead,"}
    ]


def test_a_repeat_is_of_the_passage_copied_not_of_the_fields_given(primer: Path) -> None:
    spec: CopyingGameSpec = {
        "moves": [
            {"san": "e4", "comment": [{"page": 9, "from": "If Black", "to": "4.c7"}]},
            # Other words, and a passage that overlaps the one before.
            {"san": "e5", "comment": [{"page": 9, "from": "White plays", "to": "Kb7"}]},
            # Another passage that begins where the second ends.
            {"san": "Nf3", "comment": [{"page": 9, "from": "5.Kd7", "to": "queen."}]},
        ]
    }
    result = _built(primer, spec)
    assert result["ok"] is True, result["errors"]
    assert [(r["path"], r["also_at"]) for r in result["repeated"]] == [("1b", "1w")]


def test_a_passage_copied_across_a_page_break_overlaps_one_on_the_next_page(
    primer: Path,
) -> None:
    spec: CopyingGameSpec = {
        "moves": [
            {"san": "e4", "comment": [{"page": 8, "from": "Sometimes", "to": "of 1921."}]},
            {"san": "e5", "comment": [{"page": 9, "from": "of 1921.", "to": "h5,"}]},
        ]
    }
    result = _built(primer, spec)
    assert [(r["path"], r["also_at"]) for r in result["repeated"]] == [("1b", "1w")]


def test_text_parts_with_line_breaks_set_the_copies_on_lines_of_their_own(primer: Path) -> None:
    first = {"page": 9, "from": "If Black", "to": "instead,"}
    second = {"page": 9, "from": "The king", "to": "drawn."}
    spec: CopyingGameSpec = {"comment": ["Heading\nSecond line\n\n", first, "\n\n", second, " "]}
    result = _built(primer, spec)
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert game.comment == (
        "Heading\nSecond line\n\nIf Black pushes with 2...h3 instead,\n\n"
        "The king enters the square of the h-pawn just in time and the game is drawn."
    )


def test_copies_and_text_without_a_line_break_are_joined_by_single_spaces(primer: Path) -> None:
    first = {"page": 9, "from": "If Black", "to": "instead,"}
    second = {"page": 9, "from": "The king", "to": "drawn."}
    spec: CopyingGameSpec = {"comment": ["Before ", first, "  and ", second, "  after."]}
    result = _built(primer, spec)
    (game,) = read_games(result["pgn"])
    assert game.comment == (
        "Before If Black pushes with 2...h3 instead, and "
        "The king enters the square of the h-pawn just in time and the game is drawn. after."
    )


def test_a_copy_across_a_paragraph_break_and_a_fix_make_one_comment(primer: Path) -> None:
    copy = {
        "page": 9,
        "from": "White to move draws",
        "to": "h5,",
        "fix": [["White\u2019s", "White's"]],
    }
    spec: CopyingGameSpec = {"comment": ["Diagram.\n", copy, "\n", "Then:", {**copy, "fix": []}]}
    result = _built(primer, spec)
    (game,) = read_games(result["pgn"])
    assert game.comment == (
        "Diagram.\nWhite to move draws\nWhite's king on h8 is outside the square of the black "
        "pawn on h5,\nThen: White to move draws\nWhite\u2019s king on h8 is outside the square of "
        "the black pawn on h5,"
    )


def test_a_figurine_in_a_copy_is_written_as_the_piece_its_move_reads(
    inline_picture: Path,
) -> None:
    (identifier,) = re.findall(r"\[picture \d+: ([0-9a-f]{6})\]", _lines(inline_picture, 1)[0])
    spec: CopyingGameSpec = {
        "moves": [
            {"san": "e4"},
            {"san": "e5"},
            {
                "san": f"[{identifier}]c3",
                "comment": [{"page": 1, "from": "2", "to": "c3"}],
            },
        ]
    }
    result = _built(inline_picture, spec)
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert list(game.mainline())[2].comment == "2 Nc3"


def test_a_figurine_no_move_reads_is_written_by_a_fix(inline_picture: Path) -> None:
    copy: dict[str, Any] = {"page": 1, "from": "2", "to": "c3"}
    spec: CopyingGameSpec = {"moves": [{"san": "e4", "comment": [copy]}]}
    result = _built(inline_picture, spec)
    assert result["ok"] is False
    assert "no move reads as a piece" in result["errors"][0]["message"]
    (identifier,) = re.findall(r"\[picture \d+: ([0-9a-f]{6})\]", _lines(inline_picture, 1)[0])
    copy["fix"] = [[f"[picture 1: {identifier}] c3", "Nc3"]]
    result = _built(inline_picture, spec)
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert next(iter(game.mainline())).comment == "2 Nc3"


def test_build_pgn_without_a_book_copies_nothing() -> None:
    spec: CopyingGameSpec = {
        "moves": [{"san": "e4", "comment": [{"page": 9, "from": "If", "to": "h3"}]}]
    }
    result = build_pgn(spec)
    assert result["ok"] is False
    assert result["errors"][0]["message"] == "name the book to copy from"


def test_build_pgn_reports_a_copys_missing_or_wrong_fields(primer: Path) -> None:
    spec: CopyingGameSpec = {
        "moves": [
            {"san": "e4", "comment": [{"page": 9, "from": "If Black"}]},
            {
                "san": "e5",
                "comment": [
                    {"page": "9", "from": "If", "to": "h3", "line": 8, "fix": [["a"]]},
                ],
            },
        ]
    }
    result = _built(primer, spec)
    messages = [error["message"] for error in result["errors"]]
    assert messages == [
        "comment part 1: a copy is {page, from, to, fix}, fix optional: it has no to",
        "comment part 1: a copy is {page, from, to, fix}, fix optional: not a field of a copy: "
        "line; page should be a number; fix should be a list of [printed, written] pairs of text",
    ]


QR_FEN = "4k3/8/8/8/7R/8/8/Q3K3 w - - 0 1"


def _identifier(book: Path) -> str:
    (identifier,) = re.findall(r"\[picture \d+: ([0-9a-f]{6})\]", _lines(book, 1)[0])
    return identifier


def test_pieces_write_the_letters_of_picture_marks_and_characters_in_copied_and_typed_comments(
    primer: Path,
) -> None:
    pieces = {"♖": "R", "♔": "K", "♗": "B"}
    copy = {"page": 10, "from": "1.d6", "to": "4.♔c6"}
    spec: CopyingGameSpec = {
        "comment": ["Typed ♗b5.", {**copy, "to": "♖c1+"}],
        "moves": [{"san": "e4", "comment": [copy, " Typed ♔."]}],
    }
    result = _built(primer, spec, pieces=pieces)
    assert result["ok"] is True, result["errors"]
    (game,) = read_games(result["pgn"])
    assert game.comment == "Typed Bb5. 1.d6 Rb1! 2.Ke6 Re1+ 3.Kd5 Rd1+ 4.Kc6 Rc1+"
    assert next(iter(game.mainline())).comment == ("1.d6 Rb1! 2.Ke6 Re1+ 3.Kd5 Rd1+ 4.Kc6 Typed K.")
    assert result["copied"][0]["text"] == "1.d6 Rb1! 2.Ke6 Re1+ 3.Kd5 Rd1+ 4.Kc6 Rc1+"
    # Without pieces the book's characters are copied as printed.
    plain = _built(primer, {"comment": [{"page": 10, "from": "1.d6", "to": "2.♔e6"}]})
    assert read_games(plain["pgn"])[0].comment == "1.d6 ♖b1! 2.♔e6"


def test_pieces_write_the_letter_of_a_chess_fonts_code(tmp_path: Path) -> None:
    book = write_text_epub(tmp_path / "font.epub", "<p>29...¢xh8 30.£h6† Done.</p>")
    spec: CopyingGameSpec = {
        "comment": [{"page": 1, "from": "29...¢xh8", "to": "30.£h6†"}, " ¢ typed"]
    }
    result = _built(book, spec, pieces={"¢": "R", "£": "Q"})
    assert result["ok"] is True, result["errors"]
    assert read_games(result["pgn"])[0].comment == "29...Rxh8 30.Qh6† R typed"


def test_pieces_write_the_letter_of_a_picture_in_a_copy_that_no_move_reads(
    inline_picture: Path,
) -> None:
    identifier = _identifier(inline_picture)
    spec: CopyingGameSpec = {"comment": [{"page": 1, "from": "2", "to": "c3"}]}
    refused = _built(inline_picture, spec)
    assert refused["ok"] is False
    assert "no move reads as a piece" in refused["errors"][0]["message"]
    assert refused["errors"][0]["message"].endswith(
        "give each such piece's letter in pieces or in the comment, or leave out a picture that "
        "is not a piece"
    )
    result = _built(inline_picture, spec, pieces={identifier: "N"})
    assert result["ok"] is True, result["errors"]
    assert read_games(result["pgn"])[0].comment == "2 Nc3"
    assert "pictures" not in result  # no move gave it


def test_a_comment_that_still_holds_a_unicode_figurine_is_listed_and_is_no_error(
    primer: Path,
) -> None:
    copy = {"page": 10, "from": "1.d6", "to": "2.\u2654e6"}
    spec: CopyingGameSpec = {
        "comment": "The key: \u2654 king, \u2655 queen, \u2654 again.",
        "moves": [{"san": "e4", "comment": [copy, "Then \u2656 \u2656."]}, {"san": "e5"}],
    }
    result = _built(primer, spec, pieces={"\u2656": "R"})
    assert result["ok"] is True, result["errors"]
    assert result["figurines_left"] == [
        {"path": "comment", "figurines": ["\u2654", "\u2655"]},
        {"path": "1w", "figurines": ["\u2654"]},
    ]
    assert "Then R R." in result["pgn"]
    # None are left once each is given a letter.
    given = _built(primer, spec, pieces={"\u2656": "R", "\u2654": "K", "\u2655": "Q"})
    assert given["ok"] is True and "figurines_left" not in given


def test_a_moves_unread_picture_error_gets_no_note_of_the_pieces_given() -> None:
    spec: CopyingGameSpec = {
        "start_fen": QR_FEN,
        "moves": [{"san": "[0ebed3]a4", "comment": "the [aaaaaa] there"}],
    }
    result = build_pgn(spec, pieces={"0ebed3": "R"})
    assert result["ok"] is False
    (error,) = result["errors"]
    assert error["move"] == "[0ebed3]a4" and "legal_moves" not in error
    assert "aaaaaa, which no move reads as a piece" in error["message"]
    assert "give each such piece's letter in pieces or in the comment" in error["message"]
    assert "pieces gives" not in error["message"]


def test_pieces_settle_a_picture_whose_move_is_legal_as_a_queen_or_a_rook() -> None:
    spec: CopyingGameSpec = {
        "start_fen": QR_FEN,
        "moves": [{"san": "[0ebed3]a4", "comment": "the [0ebed3] moves"}],
    }
    refused = build_pgn(spec)
    assert refused["ok"] is False
    assert "could be Q or R" in refused["errors"][0]["message"]
    for letter in "QR":
        result = build_pgn(spec, pieces={"0ebed3": letter})
        assert result["ok"] is True, result["errors"]
        assert f"1. {letter}a4" in result["pgn"] and f"the {letter} moves" in result["pgn"]
        assert result["pictures"] == {"0ebed3": letter}


def test_pieces_that_contradict_legality_are_an_error_that_names_them(
    inline_picture: Path,
) -> None:
    identifier = _identifier(inline_picture)
    moves: list[CopyingMoveSpec] = [{"san": "e4"}, {"san": "e5"}, {"san": f"[{identifier}]c3"}]
    assert build_pgn({"moves": moves})["ok"] is True
    assert build_pgn({"moves": moves}, pieces={identifier: "N"})["ok"] is True
    result = build_pgn({"moves": moves}, pieces={identifier: "Q"})
    assert result["ok"] is False
    (error,) = result["errors"]
    assert error["path"] == "2w"
    assert f"pieces gives the picture {identifier} as Q" in error["message"]


def test_pieces_override_what_an_earlier_game_read(tmp_path: Path) -> None:
    spec: CopyingGameSpec = {"start_fen": QR_FEN, "moves": [{"san": "[0ebed3]a4"}]}
    kept: dict[str, Any] = {"store": str(tmp_path), "figurines": str(tmp_path / "figurines.json")}
    assert build_pgn(spec, "a", pieces={"0ebed3": "R"}, **kept)["pictures"] == {"0ebed3": "R"}
    # An earlier game's reading settles it, unless the model gives another.
    assert build_pgn(spec, "b", **kept)["pictures"] == {"0ebed3": "R"}
    assert build_pgn(spec, "c", pieces={"0ebed3": "Q"}, **kept)["pictures"] == {"0ebed3": "Q"}


@pytest.mark.parametrize(
    ("pieces", "says"),
    [
        (
            {"[0ebed3]": "N"},
            "'[0ebed3]' is neither a picture's id, 6 hex digits, nor one character",
        ),
        ({"0ebed": "N"}, "'0ebed' is neither"),
        ({"": "N"}, "'' is neither"),
        ({"0ebed3": "P"}, "the letter for '0ebed3' is 'P', not one of K, Q, R, B or N"),
        ({"♗": "b"}, "the letter for '♗' is 'b'"),
        ({"♗": "BB"}, "the letter for '♗' is 'BB'"),
        (
            {"n": "N"},
            "pieces: no key may be 'n', since prose or the book's marks may use it: give that "
            "figurine's letter by a fix",
        ),
        ({"7": "R"}, "no key may be '7'"),
        ({"+": "K"}, "no key may be '+'"),
        ({"$": "K"}, "no key may be '$'"),
        ({"é": "B"}, "no key may be 'é'"),
        ({"\u2013": "K"}, "no key may be '\u2013'"),
        ({"†": "K"}, "no key may be '†'"),
        ({"±": "K"}, "no key may be '±'"),
        ({"\u00ad": "K"}, "no key may be '\\xad'"),
        ({"\u0301": "K"}, "no key may be '\u0301'"),
    ],
)
def test_pieces_that_are_not_ids_or_characters_with_piece_letters_are_refused(
    pieces: dict[str, str], says: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(says)):
        build_pgn({"moves": [{"san": "e4"}]}, pieces=pieces)


@pytest.mark.parametrize("key", ["\u2657", "¢", "£", "¥", "¤", "¦", "\uf035"])
def test_pieces_accept_a_figurine_a_chess_fonts_code_or_a_private_use_code(key: str) -> None:
    result = build_pgn({"comment": f"The {key}b5 move."}, pieces={key: "B"})
    assert result["ok"] is True and "The Bb5 move." in result["pgn"]


def test_a_copy_across_a_page_that_ends_with_a_picture_is_refused(
    lone_picture: Path,
) -> None:
    # What the layout does, which the test needs: page 1 ends with a picture alone on a line, and
    # page 2 prints the same picture again at its top.
    mark = _lines(lone_picture, 1)[-1].strip()
    (identifier,) = re.findall(r"^\[picture \d+: ([0-9a-f]{6})\]$", mark)
    assert f": {identifier}]" in _lines(lone_picture, 2)[0]
    across: Copy = {"page": 1, "from": "White might", "to": "c1 and"}
    with pytest.raises(ValueError) as error:
        _passage(lone_picture, across)
    assert str(error.value) == (
        f"page 1 ends with {mark}: in an EPUB's layout a picture at the end of a page is a "
        "diagram or another picture, a figurine whose move goes on at the top of page 2, or a "
        "figurine that page 2 prints again there. End this copy with its last words on page 1, "
        "and copy the rest from page 2 as a copy of its own; a fix can leave out a figurine "
        "printed twice"
    )
    # A copy that ends on the page does not run over its end.
    assert _copy(lone_picture, 1, "White might", "by 10").endswith(" by 10")
    # The build reports the refusal as a copy that cannot be made, and copies nothing.
    refused = _built(lone_picture, {"comment": [dict(across)]}, pieces={identifier: "R"})
    assert refused["ok"] is False and "copied" not in refused
    assert refused["errors"][0]["message"].startswith("comment part 1: page 1 ends with ")
    # The passage as two copies, each on its page, has the figurine once.
    top = _lines(lone_picture, 2)[0].split(" c1 ")[0]
    parts: CopyingGameSpec = {
        "comment": [
            {"page": 1, "from": "White might", "to": "by 10"},
            {"page": 2, "from": f"{top} c1", "to": "manoeuvre."},
        ]
    }
    result = _built(lone_picture, parts, pieces={identifier: "R"})
    assert result["ok"] is True, result["errors"]
    assert read_games(result["pgn"])[0].comment == (
        "White might continue developing R by 10 Rc1 and only then consider the knight manoeuvre."
    )


@pytest.mark.parametrize(
    ("last", "pictures"),
    [
        ("[picture 16: 01fd11]", "[picture 16: 01fd11]"),
        ("[picture 13: 0ebed3][picture 14: 0ebed3]", "[picture 13: 0ebed3][picture 14: 0ebed3]"),
        ("27 [picture 11: 0ebed3] d2[picture 12: 01fd11]", "[picture 12: 01fd11]"),
        ("33...[picture 9: b1a1cc] g8 34 [picture 10: f4c1b7]", "[picture 10: f4c1b7]"),
        ("by 10 [picture 1: 01fd11] c1 and", None),
        ("of a little advantage in keeping both his bishops.", None),
    ],
    ids=["alone", "two-together", "after-words", "move-goes-on", "inside-the-line", "words"],
)
def test_the_pictures_a_page_ends_with_are_found_in_the_shapes_the_layout_gives(
    last: str, pictures: str | None
) -> None:
    page = [("A line before.", True), (last, False), ("", False)]
    assert copies._ending_pictures(page) == pictures


def test_a_build_opens_the_book_once_however_many_copies_it_makes(
    primer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = []
    real = pymupdf.open
    monkeypatch.setattr(copies.pymupdf, "open", lambda *args: opened.append(args) or real(*args))
    spec: CopyingGameSpec = {
        "moves": [
            {"san": "e4", "comment": [{"page": 9, "from": "If Black", "to": "instead,"}]},
            {"san": "e5", "comment": [{"page": 9, "from": "The king", "to": "drawn."}]},
            {"san": "Nf3", "comment": [{"page": 8, "from": "Sometimes", "to": "of 1921."}]},
            {"san": "Nc6", "comment": [{"page": 10, "from": "CHAPTER 2", "to": "Defence"}]},
        ]
    }
    result = _built(primer, spec)
    assert result["ok"] is True, result["errors"]
    assert len(opened) == 1
