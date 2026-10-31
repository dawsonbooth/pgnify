"""The book's text that no comment holds, within a scope of its pages: possible omissions, from
pgnify.coverage, and in `pgnify report --coverage`."""

import json
from pathlib import Path

import pymupdf
import pytest
from support import call_tools
from typer.testing import CliRunner

from pgnify.cli import app
from pgnify.coverage import coverage, scope_pages
from pgnify.report import report

PLANS = (
    "Strategy is the purposeful pursuit of a simple goal, to gain an advantage of some sort "
    "over your opponent before the middlegame begins."
)
KNIGHTS = (
    "A knight needs an advanced support point that no enemy pawn can ever attack, or it will "
    "be chased back to its own camp."
)
ROOKS = (
    "Rooks belong on open files, where they can reach the seventh rank and eat the enemy pawns "
    "that cannot run away from them."
)
RULE = (
    "Never trade your good bishop for a bad one unless the trade wins material or stops an "
    "attack that would otherwise decide the game outright."
)
LABEL = "Show the solution or go back to the exercise list and the chapter menu"


def write_pdf(path: Path, pages: list[str]) -> Path:
    """A PDF of the pages given, each a text set in a wide box, a line for each line given."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=900, height=600)
        page.insert_textbox(pymupdf.Rect(30, 30, 870, 570), text, fontsize=9)
    doc.save(path)
    return path


@pytest.fixture
def book(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "plans.pdf", [PLANS, KNIGHTS, ROOKS])


def test_a_passage_no_comment_holds_is_a_possible_omission_with_its_page(book: Path) -> None:
    found = coverage(book, [PLANS, ROOKS])
    (omission,) = found.omissions
    assert (omission.first_page, omission.last_page, omission.repeated) == (2, 2, False)
    assert omission.excerpt.startswith("A knight needs an advanced support point")
    assert found.held + omission.words == found.words


def test_only_the_pages_of_the_scope_are_checked(book: Path) -> None:
    assert coverage(book, [PLANS, ROOKS], [1, 3]).omissions == []
    # Words no comment holds on consecutive pages make one run, across the page break.
    found = coverage(book, [PLANS], [2, 3]).omissions
    assert [(o.first_page, o.last_page) for o in found] == [(2, 3)]


def test_a_comment_too_short_for_a_stretch_holds_its_words_where_the_book_gives_them(
    tmp_path: Path,
) -> None:
    # Seven words of a caption no comment holds, then four a comment holds alone: eleven words
    # unheld but for the short comment.
    caption = "Alekhine Nimzovich San Remo Kasparov Karpov Linares"
    text = f"{PLANS}\n{caption}\nWhite prepares to double.\n{ROOKS}"
    book = write_pdf(tmp_path / "short.pdf", [text])
    found = coverage(book, [PLANS, "White prepares to double.", ROOKS])
    assert found.omissions == []
    assert (found.shorter_runs, found.shorter_words) == (1, 7)


def test_a_hyphen_compares_the_same_with_the_next_word_on_its_line_or_the_next(
    tmp_path: Path,
) -> None:
    text = (
        "The long-\nrange bishop rakes the half-open file, while the knight blockades the d-\n"
        "and f-pawns from the safety of its outpost."
    )
    book = write_pdf(tmp_path / "hyphens.pdf", [text])
    comment = (
        "The long-range bishop rakes the half-open file, while the knight blockades the d- and "
        "f-pawns from the safety of its outpost."
    )
    found = coverage(book, [comment])
    assert found.held == found.words


def test_a_passage_the_book_gives_twice_and_the_comments_once_is_repeated_at_both(
    tmp_path: Path,
) -> None:
    book = write_pdf(tmp_path / "twice.pdf", [f"{PLANS}\n{RULE}", KNIGHTS, f"{ROOKS}\n{RULE}"])
    found = coverage(book, [PLANS, KNIGHTS, ROOKS, RULE])
    assert [(o.first_page, o.repeated) for o in found.omissions] == [(1, True), (3, True)]
    assert all("bishop" in o.excerpt for o in found.omissions)


def test_text_the_book_repeats_and_no_comment_holds_is_furniture_not_an_omission(
    tmp_path: Path,
) -> None:
    pages = [f"{PLANS}\n{LABEL}", f"{KNIGHTS}\n{LABEL}", f"{ROOKS}\n{LABEL}"]
    found = coverage(write_pdf(tmp_path / "labels.pdf", pages), [PLANS, KNIGHTS, ROOKS])
    assert found.omissions == []
    assert found.furniture >= 3 * (len(LABEL.split()) - 4)


@pytest.mark.parametrize("scope", ["", "3-1", "1-2;5", "pages 2"])
def test_a_scope_is_all_or_pages(scope: str) -> None:
    with pytest.raises(ValueError):
        scope_pages(scope)
    assert scope_pages("all") is None
    assert scope_pages("2,4-6") == [2, 4, 5, 6]


def _converted(book: Path, out: Path, comments: list[str]) -> Path:
    """An output folder holding one PGN file of text-only games, one for each comment, written
    from the book through pgnify's server."""
    calls = [
        ("build_pgn", {"spec": {"comment": comment}, "name": f"text-{number}"})
        for number, comment in enumerate(comments, start=1)
    ]
    names = [f"text-{number}" for number in range(1, len(comments) + 1)]
    call_tools(book, out, [*calls, ("write_pgn", {"names": names, "file": "plans"})])
    return out


def test_report_gives_possible_omissions_only_when_asked(book: Path, tmp_path: Path) -> None:
    out = _converted(book, tmp_path / "out", [PLANS, ROOKS])
    assert "coverage" not in report(out)["books"][0]
    asked = report(out, coverage="1-2")["books"][0]["coverage"]
    assert asked["pages"] == [1, 2]
    assert [o["first_page"] for o in asked["omissions"]] == [2]
    text = (out / "report.md").read_text()
    assert "its text that no comment holds" in text
    assert "- p. 2, " in text and "possible omissions, for a person to judge" in text


def test_report_coverage_of_pages_needs_one_book(book: Path, tmp_path: Path) -> None:
    out = _converted(book, tmp_path / "out", [PLANS])
    other = write_pdf(tmp_path / "other.pdf", [ROOKS])
    call_tools(
        other,
        out,
        [
            ("build_pgn", {"spec": {"comment": ROOKS}, "name": "rooks"}),
            ("write_pgn", {"names": ["rooks"], "file": "rooks"}),
        ],
    )
    with pytest.raises(ValueError, match="2 books"):
        report(out, coverage="1")
    assert len(report(out, coverage="all")["books"]) == 2


def test_the_command_line_names_the_scope_and_refuses_pages_the_book_lacks(
    book: Path, tmp_path: Path
) -> None:
    out = _converted(book, tmp_path / "out", [PLANS, ROOKS])
    runner = CliRunner()
    done = runner.invoke(app, ["report", str(out), "--coverage", "2"])
    assert done.exit_code == 0, done.output
    assert "plans.pdf: 1 possible omission" in done.output
    assert json.loads((out / "report.json").read_text())["books"][0]["coverage"]["scope"] == "2"
    refused = runner.invoke(app, ["report", str(out), "--coverage", "2-9"])
    assert refused.exit_code == 2
    assert "within 1 to 3" in refused.output
