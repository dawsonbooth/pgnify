from importlib.metadata import version
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pgnify import instructions
from pgnify.cli import app
from pgnify.report import CAVEAT

RUNNER = CliRunner()


def test_version_prints_the_installed_version() -> None:
    result = RUNNER.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == version("pgnify")


def test_serve_refuses_a_books_location_that_is_not_there(tmp_path: Path) -> None:
    result = RUNNER.invoke(app, ["serve", "--books", str(tmp_path / "no"), "--out", str(tmp_path)])
    assert result.exit_code == 2
    assert "there is nothing at" in result.output


def test_check_prints_its_findings_and_the_caveat_and_exits_1_only_on_a_verified_problem(
    tmp_path: Path,
) -> None:
    sound, broken = tmp_path / "sound.pgn", tmp_path / "broken.pgn"
    sound.write_text('[Result "*"]\n\n1. e4 e5 *\n')
    broken.write_text('[Result "*"]\n\n1. e4 e5 2. Ke3 *\n')
    passed = RUNNER.invoke(app, ["check", str(sound)])
    failed = RUNNER.invoke(app, ["check", str(broken)])
    assert passed.exit_code == 0 and "Verified problems:\n- none found" in passed.output
    assert failed.exit_code == 1 and "illegal" in failed.output
    assert CAVEAT in passed.output


def test_check_compares_comments_with_the_book_given(primer: Path, tmp_path: Path) -> None:
    pgn = tmp_path / "game.pgn"
    pgn.write_text('[Result "*"]\n\n1. e4 { Nobody wrote these words in that book. } *\n')
    result = RUNNER.invoke(app, ["check", str(pgn), "--book", str(primer)])
    assert result.exit_code == 0
    assert "not word for word in the book" in result.output


def test_report_writes_report_md_and_json_and_prints_a_line_per_pgn_file(tmp_path: Path) -> None:
    (tmp_path / "games.pgn").write_text('[Result "*"]\n\n1. e4 e5 *\n')
    result = RUNNER.invoke(app, ["report", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "report.md").is_file() and (tmp_path / "report.json").is_file()
    assert "games.pgn: 1 game, 0 verified problems, 0 possible issues" in result.output


@pytest.mark.parametrize(("command", "missing"), [("check", "game.pgn"), ("report", "out")])
def test_check_and_report_exit_2_naming_the_missing_file(
    tmp_path: Path, command: str, missing: str
) -> None:
    result = RUNNER.invoke(app, [command, str(tmp_path / missing)])
    assert result.exit_code == 2
    assert f"pgnify {command}:" in result.output
    assert missing in result.output


def test_pgnify_instructions_prints_the_instructions_word_for_word() -> None:
    printed = RUNNER.invoke(app, ["instructions"])
    assert printed.exit_code == 0
    assert printed.output == instructions.text()
