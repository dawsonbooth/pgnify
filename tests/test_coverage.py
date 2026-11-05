"""The book's text that no comment holds, within a scope of its pages: possible omissions, from
pgnify.coverage, and in `pgnify report --coverage`."""

import json
from pathlib import Path

import pymupdf
import pytest
from support import born_digital, call_tools, clearscan, png
from typer.testing import CliRunner

from pgnify.cli import app
from pgnify.coverage import coverage, scope_pages
from pgnify.report import OCR_DERIVED, report

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


def _with_a_scanned_page(path: Path) -> Path:
    """A PDF of two pages: the first a text, the second a page that is a picture alone, as a
    scan without a text layer is."""
    doc = pymupdf.open()
    page = doc.new_page(width=900, height=600)
    page.insert_textbox(pymupdf.Rect(30, 30, 870, 570), PLANS, fontsize=9)
    doc.new_page(width=900, height=600).insert_image(
        pymupdf.Rect(0, 0, 900, 600), stream=png(90, 60, 200)
    )
    doc.save(path)
    return path


def test_a_page_without_text_is_not_assessed_rather_than_held_or_missing(tmp_path: Path) -> None:
    book = _with_a_scanned_page(tmp_path / "scan.pdf")
    found = coverage(book, [PLANS])
    assert found.textless == [2]
    assert found.words == found.held == len(PLANS.split())
    assert found.omissions == []
    assert coverage(book, [], [1]).textless == []


def test_report_names_pages_without_text_as_not_assessed_apart_from_omissions(
    tmp_path: Path,
) -> None:
    book = _with_a_scanned_page(tmp_path / "scan.pdf")
    out = _converted(book, tmp_path / "out", [PLANS])
    runner = CliRunner()
    done = runner.invoke(app, ["report", str(out), "--coverage", "2"])
    assert done.exit_code == 0, done.output
    assert "scan.pdf: 0 possible omissions, 0 words; 1 page without text, not assessed" in (
        done.output
    )
    assert json.loads((out / "report.json").read_text())["books"][0]["coverage"]["textless"] == [2]
    text = (out / "report.md").read_text()
    assert "**Not assessed:** the pages without text, p. 2, 1 of the 1 asked for." in text
    assert "Not assessed: none of these pages has text." in text
    assert "None found" not in text.split("### Possible omissions")[1]
    assert "The comments hold 0 of the 0 words" not in text


def test_report_assesses_the_pages_with_text_beside_those_without(tmp_path: Path) -> None:
    book = _with_a_scanned_page(tmp_path / "scan.pdf")
    out = _converted(book, tmp_path / "out", [PLANS])
    report(out, coverage="1-2")
    text = (out / "report.md").read_text()
    assert f"The comments hold {len(PLANS.split())} of the {len(PLANS.split())} words" in text
    assert "**Not assessed:** the pages without text, p. 2, 1 of the 2 asked for." in text
    assert "None found on the pages with text." in text


def test_a_page_without_text_stays_unassessed_after_a_page_that_ends_with_a_hyphen(
    tmp_path: Path,
) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=900, height=600)
    page.insert_textbox(pymupdf.Rect(30, 30, 870, 570), f"{PLANS} over-", fontsize=9)
    doc.new_page(width=900, height=600).insert_image(
        pymupdf.Rect(0, 0, 900, 600), stream=png(90, 60, 200)
    )
    book = tmp_path / "hyphen-then-scan.pdf"
    doc.save(book)
    assert coverage(book, [PLANS]).textless == [2]


@pytest.mark.parametrize(
    ("make", "page", "basis"),
    [(clearscan, 2, OCR_DERIVED), (born_digital, 3, None)],
    ids=["clearscan", "born-digital"],
)
def test_possible_omissions_are_all_listed_and_labelled_by_where_their_pages_text_comes_from(
    tmp_path: Path, make, page: int, basis: str | None
) -> None:
    out = _converted(make(tmp_path, [PLANS, ROOKS]), tmp_path / "out", [PLANS])
    (omission,) = report(out, coverage="all")["books"][0]["coverage"]["omissions"]
    assert (omission["first_page"], omission["basis"]) == (page, basis)
    text = (out / "report.md").read_text()
    lower = "### Possible omissions on pages whose text appears OCR-derived: lower confidence"
    assert (lower in text) is (basis is not None)
    assert f"- p. {page}, " in text


def test_the_command_line_counts_lower_confidence_findings_apart(tmp_path: Path) -> None:
    book = clearscan(tmp_path, [PLANS, ROOKS])
    out = _converted(book, tmp_path / "out", [PLANS, KNIGHTS])
    done = CliRunner().invoke(app, ["report", str(out), "--coverage", "all"])
    assert done.exit_code == 0, done.output
    assert (
        "plans.pgn: 2 games, 0 verified problems, 0 possible issues, and 1 of lower confidence, "
        "comment text against a text layer that appears OCR-derived"
    ) in done.output
    assert (
        "clearscan.pdf: 1 possible omission (1 of lower confidence, on pages whose text appears "
        "OCR-derived), 23 words"
    ) in done.output


def test_on_a_mostly_scanned_book_only_the_scanned_pages_omissions_are_of_lower_confidence(
    tmp_path: Path,
) -> None:
    # Three pages of text over a picture of the page, then a typeset page, with no tool named.
    doc = pymupdf.open()
    for text in (PLANS, KNIGHTS, RULE):
        page = doc.new_page(width=400, height=600)
        page.insert_image(page.rect, stream=png(40, 60, 230))
        page.insert_textbox(pymupdf.Rect(40, 60, 360, 560), text, fontsize=11)
    doc.new_page(width=400, height=600).insert_textbox(
        pymupdf.Rect(40, 60, 360, 560), ROOKS, fontsize=11
    )
    book = tmp_path / "mostly-scanned.pdf"
    doc.save(book)
    # The comments hold pages 1 and 3, so pages 2 and 4 are each an omission of its own.
    out = _converted(book, tmp_path / "out", [PLANS, RULE])
    omissions = report(out, coverage="all")["books"][0]["coverage"]["omissions"]
    assert {(o["first_page"], o["basis"]) for o in omissions} == {(2, OCR_DERIVED), (4, None)}
