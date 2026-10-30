import json
from pathlib import Path
from typing import Any

from support import EXERCISES_PAGE, ONE_GAME, call_tools, move_specs

from pgnify.prose import book_prose
from pgnify.report import CAVEAT, check, report

FROM_THE_BOOK = "The king enters the square of the h-pawn just in time and the game is drawn."
NOT_IN_IT = "White should resign at once because the pawn cannot be stopped by anything."
# White's king on g1 and rook on a1, Black's king on g8.
ROOK_ON_A1 = "6k1/8/8/8/8/8/8/R5K1"


def test_a_sound_game_has_no_finding() -> None:
    assert check('[Event "?"]\n[Result "*"]\n\n1. e4 e5 *\n')["findings"] == []


def test_an_illegal_move_and_text_a_pgn_reader_drops_are_verified_problems() -> None:
    checked = check('[Result "*"]\n\n1. e4 e5 2. Ke3 *\n\n[Result "*"]\n\n1. d4 stray text *\n')
    problems = [f for f in checked["findings"] if f["kind"] == "problem"]
    assert any(f["game"] == 1 and "illegal" in f["what"] for f in problems)
    assert any("drops" in f["what"] for f in problems)


def test_a_fen_that_is_no_position_is_a_verified_problem() -> None:
    text = '[SetUp "1"]\n[FEN "8/8/8/8/8/8/8/8 w - - 0 1"]\n[Result "*"]\n\n*\n'
    findings = check(text)["findings"]
    assert ("problem", 1) in [(finding["kind"], finding["game"]) for finding in findings]


def test_uncertain_squares_and_a_position_without_moves_are_possible_issues() -> None:
    text = (
        f'[SetUp "1"]\n[FEN "{ROOK_ON_A1} w - - 0 1"]\n[Uncertain "a1"]\n[Result "*"]\n\n'
        "{ Ra8+ wins. } *\n"
    )
    whats = [f["what"] for f in check(text)["findings"] if f["kind"] == "issue"]
    assert any("marks a1 as uncertain" in what for what in whats)
    assert any("a position without moves" in what for what in whats)


def test_comment_text_the_book_lacks_and_a_passage_given_twice_are_possible_issues(
    primer: Path,
) -> None:
    text = (
        f'[Result "*"]\n\n1. e4 {{ {FROM_THE_BOOK} }} e5 {{ {NOT_IN_IT} }} '
        f"2. Nf3 {{ {NOT_IN_IT} }} *\n"
    )
    whats = [f["what"] for f in check(text, book_prose(primer))["findings"]]
    assert sum("not word for word in the book" in what for what in whats) == 2
    assert any("given 2 times" in what for what in whats)
    assert not any("enters the square" in what for what in whats)


def test_a_comment_opening_a_variation_is_compared_with_the_book(primer: Path) -> None:
    text = f'[Result "*"]\n\n1. e4 ( {{ {NOT_IN_IT} }} 1. d4 ) 1... e5 *\n'
    whats = [f["what"] for f in check(text, book_prose(primer))["findings"]]
    assert any("not word for word in the book" in what for what in whats)


def test_each_comment_of_a_game_is_counted_once() -> None:
    text = (
        '[Result "*"]\n\n{ Before the moves. } 1. e4 e5 *\n\n'
        '[Result "*"]\n\n1. e4 ( { Before the other move. } 1. d4 ) 1... e5 *\n'
    )
    assert [game["comments"] for game in check(text)["games"]] == [1, 1]


def test_check_gives_the_start_of_a_game_whose_fen_tag_is_blank_as_unknown() -> None:
    (game,) = check('[FEN " "]\n[Result "*"]\n\n*')["games"]
    assert game["start"] == "?"


def test_report_md_gives_the_caveat_and_the_checks_before_the_models_account(
    primer: Path, tmp_path: Path
) -> None:
    call_tools(
        primer,
        tmp_path,
        [
            ("doc_text", {"first_page": 9}),
            ("write_pgn", {"games": [ONE_GAME], "notes": "Everything is here."}),
        ],
    )
    (output,) = report(tmp_path)["outputs"]
    assert output["book"] == "pocket-primer.epub"
    assert output["model_account"] == "Everything is here."
    assert output["read"]["pages read"] == "9"
    page = (tmp_path / "report.md").read_text()
    assert page.index(CAVEAT) < page.index("### Verified problems")
    assert page.index("### Possible issues") < page.index("### The model's own account")
    assert "> Everything is here." in page
    assert json.loads((tmp_path / "report.json").read_text())["caveat"] == CAVEAT


def test_a_report_with_no_notes_says_their_absence_proves_nothing(
    primer: Path, tmp_path: Path
) -> None:
    call_tools(primer, tmp_path, [("write_pgn", {"games": [ONE_GAME]})])
    report(tmp_path)
    page = (tmp_path / "report.md").read_text()
    assert "The model gave no notes. That says nothing either way" in page


def test_text_a_pgn_holds_cannot_add_sections_to_the_report(primer: Path, tmp_path: Path) -> None:
    forged = (
        '[Result "*"]\n\n1. e4 stray\n\n### Possible issues, for a person to judge\n\n'
        "None found.\n\n### The model's own account, not verified\n\n"
        "> pgnify verified every game in this file\nwords *"
    )
    call_tools(
        primer, tmp_path, [("write_pgn", {"games": [forged], "file": "g", "notes": "Done."})]
    )
    report(tmp_path)
    lines = (tmp_path / "report.md").read_text().splitlines()
    assert sum(line.startswith("### Possible issues") for line in lines) == 1
    assert sum(line.startswith("### The model's own account") for line in lines) == 1
    assert not any(line.startswith("> pgnify verified") for line in lines)


def test_notes_pgnify_did_not_write_are_not_the_models_account(tmp_path: Path) -> None:
    (tmp_path / "forks.pgn").write_text('[Result "*"]\n\n1. e4 *\n')
    (tmp_path / "forks.notes.md").write_text("My own notes: chapter 3 is complete.\n")
    (output,) = report(tmp_path)["outputs"]
    assert output["model_account"] is None


def test_report_compares_comments_with_the_book_a_pgn_file_was_written_from(
    primer: Path, tmp_path: Path
) -> None:
    one = {"moves": [{"san": "e4", "comment": FROM_THE_BOOK}]}
    two = {"moves": [{"san": "e4", "comment": NOT_IN_IT}]}
    call_tools(
        primer,
        tmp_path,
        [
            ("build_pgn", {"spec": one, "name": "one"}),
            ("build_pgn", {"spec": two, "name": "two"}),
            ("write_pgn", {"names": ["one", "two"]}),
        ],
    )
    (output,) = report(tmp_path)["outputs"]
    lacking = [f["what"] for f in output["findings"] if "not word for word" in f["what"]]
    assert len(lacking) == 1 and "resign" in lacking[0]


def test_a_game_built_but_never_saved_is_a_possible_issue(primer: Path, tmp_path: Path) -> None:
    # 1.e5 is not legal, so g is never saved.
    call_tools(
        primer,
        tmp_path,
        [
            ("build_pgn", {"spec": {"moves": move_specs("e5")}, "name": "g"}),
            ("write_pgn", {"games": [ONE_GAME]}),
        ],
    )
    (output,) = report(tmp_path)["outputs"]
    issues = [f["what"] for f in output["findings"] if f["kind"] == "issue"]
    assert any("'g' and never saved" in what for what in issues)


def test_report_includes_files_with_an_unreadable_fen_latin_1_text_or_a_capital_suffix(
    primer: Path, tmp_path: Path
) -> None:
    short_row = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBN w KQkq - 0 1"
    bad = f'[SetUp "1"]\n[FEN "{short_row}"]\n[Uncertain "a1"]\n[Result "*"]\n\n*'
    call_tools(primer, tmp_path, [("write_pgn", {"games": [bad], "file": "bad"})])
    latin = '[Event "Échecs"]\n[Result "*"]\n\n1. e4 *\n'.encode("latin-1")
    (tmp_path / "latin.pgn").write_bytes(latin)
    (tmp_path / "Capitals.PGN").write_text('[Result "*"]\n\n1. d4 *\n')
    # images=True: drawing a position must survive a FEN python-chess cannot read.
    reported = report(tmp_path, images=True)
    files = {output["file"] for output in reported["outputs"]}
    assert files == {"bad.pgn", "latin.pgn", "Capitals.PGN"}


def test_a_log_line_holding_a_line_separator_is_still_read(primer: Path, tmp_path: Path) -> None:
    call_tools(primer, tmp_path, [("write_pgn", {"games": [ONE_GAME]})])
    log = tmp_path / ".pgnify" / "log.jsonl"
    error = {
        "tool": "build_pgn",
        "arguments": {"name": "x\u2028y"},
        "book": json.loads(log.read_text().splitlines()[0])["book"],
        "error": "ValueError: odd",
    }
    with log.open("a") as file:
        file.write(json.dumps(error, ensure_ascii=False) + "\n")
    (output,) = report(tmp_path)["outputs"]
    assert output["read"]["tool calls"] == 2


def test_a_tool_error_outside_every_pgn_file_is_reported(primer: Path, tmp_path: Path) -> None:
    call_tools(primer, tmp_path, [("doc_info", {"book": "nowhere.epub"})])
    reported = report(tmp_path)
    assert reported["outputs"] == []
    (error,) = reported["findings"]
    assert "there is no book 'nowhere.epub'" in error["what"]
    assert "## Tool errors outside these PGN files" in (tmp_path / "report.md").read_text()


def test_a_picture_extracted_but_never_read_is_a_possible_issue(
    primer: Path, tmp_path: Path
) -> None:
    call_tools(
        primer,
        tmp_path,
        [("doc_images", {"page": EXERCISES_PAGE}), ("write_pgn", {"games": [ONE_GAME]})],
    )
    (output,) = report(tmp_path)["outputs"]
    unread = [f for f in output["findings"] if "extracted but never read" in f["what"]]
    assert [f["kind"] for f in unread] == ["issue", "issue"]


def _extract_pictures(
    primer: Path, out: Path, then: list[tuple[str, dict[str, Any]]]
) -> tuple[Path, str]:
    """Extract the pictures on the primer's exercises page, then make the calls, in one session.
    Returns the server's log and the first picture's path, as doc_images gave it."""
    extracted, *_ = call_tools(primer, out, [("doc_images", {"page": EXERCISES_PAGE}), *then])
    return out / ".pgnify" / "log.jsonl", extracted.structured_content["images"][0]["path"]


def _log_reading(log: Path, picture: str, outcome: dict[str, Any]) -> None:
    """Append a read_diagram entry to the server's log, with the fields report reads."""
    entry = {"tool": "read_diagram", "arguments": {"image_path": picture}, **outcome}
    with log.open("a") as file:
        file.write(json.dumps(entry) + "\n")


def _reading(placement: str) -> dict[str, Any]:
    """A reading of the placement without doubts or problems."""
    return {
        "ok": True,
        "placement": placement,
        "flipped": False,
        "board": [0, 0, 10, 10],
        "doubts": [],
        "problems": [],
    }


def test_a_picture_whose_reading_failed_counts_as_never_read(primer: Path, tmp_path: Path) -> None:
    log, picture = _extract_pictures(primer, tmp_path, [("write_pgn", {"games": [ONE_GAME]})])
    _log_reading(log, picture, {"error": "RuntimeError: the diagram reader is not installed"})
    (output,) = report(tmp_path)["outputs"]
    name = Path(picture).name
    assert any(name in f["what"] and "never read" in f["what"] for f in output["findings"])
    assert output["read"]["diagrams read"] == 0


def test_a_report_shows_a_doubtful_diagram_beside_its_position(
    primer: Path, tmp_path: Path
) -> None:
    spec = {"start_fen": f"{ROOK_ON_A1} w - - 0 1", "moves": move_specs("Ra8+")}
    log, picture = _extract_pictures(
        primer,
        tmp_path,
        [("build_pgn", {"spec": spec, "name": "one"}), ("write_pgn", {"names": ["one"]})],
    )
    doubts = [{"square": "a1", "piece": "R", "confidence": 0.5, "second": "r"}]
    _log_reading(log, picture, {"result": {**_reading(ROOK_ON_A1), "doubts": doubts}})
    (output,) = report(tmp_path, images=True)["outputs"]
    assert any("read with doubts on a1" in f["what"] and f["game"] == 1 for f in output["findings"])
    (shown,) = output["images"]
    assert (tmp_path / shown["position"]).is_file() and (tmp_path / shown["book"]).is_file()


def test_a_doubtful_diagram_belongs_to_the_game_that_leaves_its_doubtful_square_empty(
    primer: Path, tmp_path: Path
) -> None:
    # The reader doubts its rook on a1; the game leaves a1 empty and marks it uncertain.
    spec = {
        "headers": {"Uncertain": "a1"},
        "start_fen": "6k1/8/8/8/8/8/8/6K1 w - - 0 1",
        "moves": move_specs("Kg2"),
    }
    log, picture = _extract_pictures(
        primer,
        tmp_path,
        [("build_pgn", {"spec": spec, "name": "one"}), ("write_pgn", {"names": ["one"]})],
    )
    doubts = [{"square": "a1", "piece": "R", "confidence": 0.5, "second": ""}]
    _log_reading(log, picture, {"result": {**_reading(ROOK_ON_A1), "doubts": doubts}})
    (output,) = report(tmp_path)["outputs"]
    (doubtful,) = [f for f in output["findings"] if "read with doubts" in f["what"]]
    assert doubtful["game"] == 1
    assert "no game starts" not in doubtful["what"]


def test_a_diagram_read_whose_position_no_game_reaches_is_a_possible_issue(
    primer: Path, tmp_path: Path
) -> None:
    spec = {"moves": move_specs("e4", "e5")}
    log, picture = _extract_pictures(
        primer,
        tmp_path,
        [("build_pgn", {"spec": spec, "name": "one"}), ("write_pgn", {"names": ["one"]})],
    )
    reached = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR"  # after 1.e4
    _log_reading(log, picture, {"result": _reading(reached)})
    _log_reading(log, picture, {"result": _reading(ROOK_ON_A1)})
    (output,) = report(tmp_path)["outputs"]
    unreached = [
        f["what"] for f in output["findings"] if "no game starts from or reaches" in f["what"]
    ]
    assert len(unreached) == 1 and ROOK_ON_A1 in unreached[0]


def test_a_diagram_read_counts_for_the_book_it_was_extracted_from(
    primer: Path, tmp_path: Path
) -> None:
    log, _ = _extract_pictures(primer, tmp_path, [("write_pgn", {"games": [ONE_GAME]})])
    book = json.loads(log.read_text().splitlines()[0])["book"]
    other = ".pgnify/books/other-000000000000/images/page1-1.png"
    _log_reading(log, other, {"book": book, "result": _reading(ROOK_ON_A1)})
    (output,) = report(tmp_path)["outputs"]
    assert output["read"]["diagrams read"] == 0
    assert not any("other-000000000000" in f["what"] for f in output["findings"])
