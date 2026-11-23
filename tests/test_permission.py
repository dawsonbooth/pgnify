"""Tests of what the person allows when `pgnify serve` is given no --books or no --out. The tool
calls that ask run through the SDK's client with both protocol versions, up to 2025-11-25 and from
2026-07-28. Also the records that sessions share, and the report."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server.elicitation import DeclinedElicitation
from mcp.types import CallToolResult, TextContent
from support import ERAS, ONE_GAME, Person, call_tools, in_session, move_specs
from typer.testing import CliRunner

from pgnify import outputs as outputs_module
from pgnify.books import Book
from pgnify.cli import app
from pgnify.outputs import NOTES_HEADING, Outputs, locked, read_records, replace_json
from pgnify.permission import Allowances, can_ask, shown
from pgnify.report import report

READ_QUESTION = "Allow pgnify to read this book until this session ends?"
WRITE_QUESTION = (
    "Allow pgnify to write PGN files in this folder, and in folders inside it, until this "
    "session ends?"
)


@pytest.fixture(params=ERAS)
def era(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home folder of the test's own, with no link in its path."""
    home = tmp_path.resolve() / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def library(primer: Path, two_pages: Path, home: Path) -> Path:
    """A folder of two books in the home folder."""
    folder = home / "Library"
    folder.mkdir()
    shutil.copyfile(primer, folder / "primer.epub")
    shutil.copyfile(two_pages, folder / "two-pages.pdf")
    return folder


@pytest.fixture
def state(home: Path) -> Path:
    return home / "state"


def _text(result: CallToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


def _calls(
    state: Path,
    calls: list[tuple[str, dict[str, Any]]],
    *,
    books: Path | None = None,
    out: Path | None = None,
    person: Person | None = None,
    era: str = "legacy",
) -> list[CallToolResult]:
    return call_tools(books, out, calls, state=state, person=person, era=era)


def _write(file: str, **arguments: Any) -> tuple[str, dict[str, Any]]:
    return "write_pgn", {"games": [ONE_GAME], "file": file, **arguments}


# Asking about a book or a folder


def test_the_first_time_a_book_is_named_the_person_is_asked_and_the_yes_is_kept(
    library: Path, state: Path, tmp_path: Path, era: str
) -> None:
    book = library / "primer.epub"
    link = tmp_path / "link.epub"
    link.symlink_to(book)
    person = Person()
    named = {"book": "~/Library/primer.epub"}
    first, second, other_path, left_out = _calls(
        state,
        [
            ("doc_info", named),
            ("doc_info", named),
            ("doc_info", {"book": str(link)}),
            ("doc_info", {}),
        ],
        out=tmp_path / "out",
        person=person,
        era=era,
    )
    for result in (first, second, other_path, left_out):
        assert not result.is_error, result.content
        assert result.structured_content["page_count"] == 14
    # One question, about the book with ~ for the home folder, in a form of one box ticked.
    assert person.asked == [f"{READ_QUESTION}\n\n~/Library/primer.epub"]
    (form,) = person.forms
    assert form["properties"] == {"allow": {"type": "boolean", "default": True, "title": "Allow"}}


def test_a_path_that_leads_elsewhere_is_shown_with_where_it_leads(
    library: Path, state: Path, tmp_path: Path, era: str
) -> None:
    link = tmp_path.resolve() / "link.epub"
    link.symlink_to(library / "primer.epub")
    person = Person()
    _calls(state, [("doc_info", {"book": str(link)})], out=tmp_path / "out", person=person, era=era)
    assert person.asked == [
        f"{READ_QUESTION}\n\n~/Library/primer.epub\n\nThe assistant gave the path as {link}."
    ]


def test_a_book_may_be_left_out_only_when_one_book_has_been_allowed(
    library: Path, state: Path, tmp_path: Path, era: str
) -> None:
    person = Person()
    none, one, two, both = _calls(
        state,
        [
            ("doc_info", {}),
            ("doc_info", {"book": "~/Library/primer.epub"}),
            ("doc_info", {"book": "~/Library/two-pages.pdf"}),
            ("doc_info", {}),
        ],
        out=tmp_path / "out",
        person=person,
        era=era,
    )
    assert none.is_error and "the person has allowed no book yet" in _text(none)
    assert not one.is_error and not two.is_error
    assert both.is_error and "name the book by its full path" in _text(both)
    assert "'~/Library/primer.epub', '~/Library/two-pages.pdf'" in _text(both)
    assert len(person.asked) == 2


def test_the_first_time_a_folder_is_named_the_person_is_asked_and_a_yes_covers_what_is_inside(
    library: Path, state: Path, home: Path, era: str
) -> None:
    person = Person()
    results = _calls(
        state,
        [
            _write("~/Chess/Silman/Part Four", names=None),
            _write("~/Chess/Silman/Part Five.pgn"),
            _write("~/Chess/Silman/Deeper/Still/Part Six"),
            _write("~/Chess/Top"),
        ],
        books=library / "primer.epub",
        person=person,
        era=era,
    )
    assert [result.is_error for result in results] == [False] * 4, [r.content for r in results]
    assert [result.structured_content["file"] for result in results] == [
        "~/Chess/Silman/Part Four.pgn",
        "~/Chess/Silman/Part Five.pgn",
        "~/Chess/Silman/Deeper/Still/Part Six.pgn",
        "~/Chess/Top.pgn",
    ]
    tail = "pgnify will create the folder if it is missing."
    # Silman, then Silman/Deeper/Still, which is inside it, is covered; Chess is asked on its own.
    assert person.asked == [
        f"{WRITE_QUESTION}\n\n~/Chess/Silman\n\n{tail}",
        f"{WRITE_QUESTION}\n\n~/Chess\n\n{tail}",
    ]
    assert (home / "Chess" / "Silman" / "Deeper" / "Still" / "Part Six.pgn").read_text() == (
        '[Result "*"]\n\n1. e4 *\n'
    )


def test_a_yes_covers_the_folder_and_the_ones_inside_it_and_not_a_name_that_begins_like_it(
    library: Path, state: Path, home: Path, era: str
) -> None:
    person = Person()
    results = _calls(
        state,
        [_write("~/Chess/One"), _write("~/Chess/Sub/Two"), _write("~/Chess2/Three")],
        books=library / "primer.epub",
        person=person,
        era=era,
    )
    assert not any(result.is_error for result in results)
    assert [question.splitlines()[2] for question in person.asked] == ["~/Chess", "~/Chess2"]


def test_a_folder_is_made_only_after_a_yes_and_the_other_checks(
    library: Path, state: Path, home: Path, era: str
) -> None:
    person = Person(folder="decline")
    book = library / "primer.epub"
    declined, again = _calls(
        state,
        [_write("~/New/Deep/One"), _write("~/New/Deep/Two")],
        books=book,
        person=person,
        era=era,
    )
    assert declined.is_error and again.is_error
    assert "the person did not allow pgnify to write PGN files in ~/New/Deep" in _text(declined)
    assert len(person.asked) == 1
    assert not (home / "New").exists()
    # A yes, but games that fail the checks: still no folder.
    (empty,) = _calls(
        state,
        [("write_pgn", {"games": [""], "file": "~/Other/One"})],
        books=book,
        person=Person(),
        era=era,
    )
    assert empty.is_error and "is empty" in _text(empty)
    assert not (home / "Other").exists()


def test_a_link_in_an_allowed_folder_is_asked_about_by_its_real_folder(
    library: Path, state: Path, home: Path, era: str
) -> None:
    (home / "Allowed").mkdir()
    (home / "Elsewhere").mkdir()
    (home / "Allowed" / "link").symlink_to(home / "Elsewhere")
    person = Person()
    book = library / "primer.epub"
    inside, through = _calls(
        state,
        [_write("~/Allowed/First"), _write("~/Allowed/link/Second")],
        books=book,
        person=person,
        era=era,
    )
    assert not inside.is_error and not through.is_error
    tail = "pgnify will create the folder if it is missing."
    assert person.asked == [
        f"{WRITE_QUESTION}\n\n~/Allowed\n\n{tail}",
        f"{WRITE_QUESTION}\n\n~/Elsewhere\n\n"
        f"The assistant gave the path as ~/Allowed/link.\n\n{tail}",
    ]
    assert (home / "Elsewhere" / "Second.pgn").is_file()
    # Declined, nothing reaches the folder the link leads to.
    (home / "Elsewhere" / "Second.pgn").unlink()
    refusing = Person(folder="decline")
    (refused,) = _calls(
        state, [_write("~/Allowed/link/Third")], books=book, person=refusing, era=era
    )
    assert refused.is_error and not list((home / "Elsewhere").iterdir())


@pytest.mark.parametrize("answer", ["decline", "unticked"])
def test_a_no_refuses_the_book_for_the_rest_of_the_session_and_reads_nothing(
    library: Path, state: Path, tmp_path: Path, era: str, answer: str
) -> None:
    person = Person(book=answer)
    book = {"book": "~/Library/primer.epub"}
    first, again = _calls(
        state,
        [("doc_text", {"first_page": 1, **book}), ("doc_info", book)],
        out=tmp_path / "out",
        person=person,
        era=era,
    )
    for result in (first, again):
        assert result.is_error
        assert "the person did not allow pgnify to read ~/Library/primer.epub" in _text(result)
        assert "Ask the person which book or folder to use instead" in _text(result)
    assert len(person.asked) == 1
    assert not (state / "books").exists()
    # Another book is another question.
    person.answers["read"] = "yes"
    (other,) = _calls(
        state,
        [("doc_info", {"book": "~/Library/two-pages.pdf"})],
        out=tmp_path / "out",
        person=person,
        era=era,
    )
    assert not other.is_error and len(person.asked) == 2


def test_a_cancel_refuses_the_call_alone(
    library: Path, state: Path, tmp_path: Path, era: str
) -> None:
    person = Person(book="cancel")
    book = {"book": "~/Library/primer.epub"}

    async def act(client: Client) -> list[CallToolResult]:
        first = await client.call_tool("doc_info", book)
        person.answers["read"] = "yes"
        return [first, await client.call_tool("doc_info", book)]

    first, second = in_session(None, tmp_path / "out", act, state=state, person=person, era=era)
    assert first.is_error and "the person did not allow pgnify to read" in _text(first)
    assert "Ask the person whether to try again" in _text(first)
    assert not second.is_error
    assert len(person.asked) == 2


def test_a_cancel_asks_about_the_folder_again_and_a_no_does_not(
    library: Path, state: Path, home: Path, era: str
) -> None:
    person = Person(folder="cancel")
    book = library / "primer.epub"

    async def act(client: Client) -> list[CallToolResult]:
        first = await client.call_tool(*_write("~/Chess/One"))
        person.answers["write"] = "unticked"
        second = await client.call_tool(*_write("~/Chess/Two"))
        third = await client.call_tool(*_write("~/Chess/Three"))
        return [first, second, third]

    results = in_session(book, None, act, state=state, person=person, era=era)
    assert all(result.is_error for result in results)
    assert "cancelled the question" in _text(results[0])
    assert "left the box unticked" in _text(results[1])
    assert len(person.asked) == 2
    assert not (home / "Chess").exists()


def test_a_refused_book_leaves_the_answer_about_the_folder_kept(
    state: Path, library: Path, home: Path, era: str
) -> None:
    person = Person(book="cancel")

    async def act(client: Client) -> list[CallToolResult]:
        first = await client.call_tool(*_write("~/Chess/One", book="~/Library/primer.epub"))
        person.answers["read"] = "yes"
        second = await client.call_tool(*_write("~/Chess/Two", book="~/Library/primer.epub"))
        return [first, second]

    first, second = in_session(None, None, act, state=state, person=person, era=era)
    assert first.is_error and not second.is_error
    # The folder was asked once, in the first call, and the book in both.
    assert sum(READ_QUESTION in question for question in person.asked) == 2
    assert sum(WRITE_QUESTION in question for question in person.asked) == 1
    assert (home / "Chess" / "Two.pgn").is_file() and not (home / "Chess" / "One.pgn").exists()


def test_a_refusal_wins_over_a_later_yes_for_a_folder_above_it(
    library: Path, state: Path, home: Path, era: str
) -> None:
    class Choosy(Person):
        async def __call__(self, context: Any, params: Any) -> Any:
            self.answers["write"] = "decline" if "~/Chess/sub\n" in params.message else "yes"
            return await super().__call__(context, params)

    person = Choosy()
    results = _calls(
        state,
        [
            _write("~/Chess/sub/x"),
            _write("~/top"),
            _write("~/Chess/sub/y"),
            _write("~/Chess/sub/d/z"),
        ],
        books=library / "primer.epub",
        person=person,
        era=era,
    )
    assert [result.is_error for result in results] == [True, False, True, True]
    assert not (home / "Chess").exists()
    assert len(person.asked) == 2  # ~/Chess/sub, then ~


def test_a_refusal_holds_for_another_spelling_and_for_the_folders_inside() -> None:
    import unicodedata

    permits = Allowances("write PGN files in", "--out", nested=True)
    folder = Path("/chess/" + unicodedata.normalize("NFC", "café"))
    assert permits.asks_about(folder)
    permits.settle(folder, DeclinedElicitation())
    spelled = Path("/chess/" + unicodedata.normalize("NFD", "café"))
    assert not permits.asks_about(spelled)
    assert not permits.asks_about(folder / "inside" / "deeper")
    assert permits.asks_about(Path("/chess/café2"))
    assert permits.asks_about(Path("/chess"))


@pytest.mark.skipif(
    sys.platform not in ("darwin", "win32"), reason="the file system tells the case apart"
)
def test_a_refusal_holds_for_another_case_where_the_file_system_ignores_it(
    library: Path, state: Path, home: Path, era: str
) -> None:
    person = Person(book="decline", folder="decline")
    book = "~/Library/primer.epub"
    (home / "Chess").mkdir()
    shouted = {"book": "~/LIBRARY/PRIMER.EPUB"}
    results = _calls(
        state,
        [
            _write("~/Chess/One", book=book),
            _write("~/CHESS/Two", book=book),
            ("doc_info", {"book": book}),
            ("doc_info", shouted),
        ],
        person=person,
        era=era,
    )
    assert all(result.is_error for result in results)
    assert len(person.asked) == 2  # the book once, the folder once


def test_a_client_that_cannot_ask_is_told_to_start_the_server_with_books(
    library: Path, state: Path, tmp_path: Path, era: str
) -> None:
    (result,) = _calls(
        state, [("doc_info", {"book": "~/Library/primer.epub"})], out=tmp_path / "out", era=era
    )
    assert result.is_error
    assert "start `pgnify serve` with --books" in _text(result)
    assert "has not declared form elicitation" in _text(result)


def test_a_client_that_cannot_ask_is_told_to_start_the_server_with_out(
    library: Path, state: Path, home: Path, era: str
) -> None:
    (result,) = _calls(state, [_write("~/Chess/One")], books=library / "primer.epub", era=era)
    assert result.is_error and "start `pgnify serve` with --out" in _text(result)
    assert not (home / "Chess").exists()


def test_a_declaration_of_form_elicitation_is_what_lets_the_server_ask() -> None:
    from mcp.types import ClientCapabilities, ElicitationCapability, UrlElicitationCapability

    assert not can_ask(None)
    assert not can_ask(ClientCapabilities())
    assert can_ask(ClientCapabilities(elicitation=ElicitationCapability()))
    only_urls = ElicitationCapability(url=UrlElicitationCapability())
    assert not can_ask(ClientCapabilities(elicitation=only_urls))


def test_shown_writes_the_home_folder_as_a_tilde(home: Path) -> None:
    assert (
        shown(home / "a" / "b")
        == f"~{'/' if '/' in str(home) else chr(92)}a{'/' if '/' in str(home) else chr(92)}b"
    )
    assert shown(home) == "~"
    assert shown(home.parent) == str(home.parent)


# Refusing without asking


@pytest.mark.parametrize(
    ("book", "reason"),
    [
        ("Library/primer.epub", "give the book as a full path"),
        ("~/Library/missing.pdf", "there is no file at ~/Library/missing.pdf"),
        ("~/Library", "is not a file"),
        ("~/Library/notes.txt", "is not a PDF or an EPUB"),
        ("~nobody-has-this-name/book.pdf", "give the book as a full path"),
        ("", "give the book as a full path"),
    ],
)
def test_a_path_that_is_not_a_full_path_to_a_book_is_refused_without_asking(
    library: Path, state: Path, tmp_path: Path, era: str, book: str, reason: str
) -> None:
    (library / "notes.txt").write_text("not a book")
    person = Person()
    (result,) = _calls(
        state, [("doc_info", {"book": book})], out=tmp_path / "out", person=person, era=era
    )
    assert result.is_error and reason in _text(result)
    assert person.asked == []


@pytest.mark.parametrize(
    ("file", "reason"),
    [
        (None, "give file, the full path of the PGN file"),
        ("Chess/One", "give file as a full path"),
        ("~/Chess/", "not a folder's"),
        ("~/Chess/.hidden", "is not a plain file name"),
        ("~/Chess/a:b", "is not a plain file name"),
        ("~/Chess/" + "x" * 97, "is not a plain file name"),
    ],
)
def test_a_file_that_is_not_a_full_path_with_a_plain_name_is_refused_without_asking(
    library: Path, state: Path, home: Path, era: str, file: str | None, reason: str
) -> None:
    person = Person()
    arguments: dict[str, Any] = {"games": [ONE_GAME]}
    if file is not None:
        arguments["file"] = file
    (result,) = _calls(
        state,
        [("write_pgn", arguments)],
        books=library / "primer.epub",
        person=person,
        era=era,
    )
    assert result.is_error and reason in _text(result)
    assert person.asked == [] and not (home / "Chess").exists()


@pytest.mark.parametrize("file", ["~/Chess/.", "~/Chess/..", "~/Chess/", "~/Chess/x/.."])
def test_a_file_that_ends_in_a_separator_or_a_dot_part_is_refused_without_asking(
    library: Path, state: Path, home: Path, era: str, file: str
) -> None:
    person = Person()
    (result,) = _calls(state, [_write(file)], books=library / "primer.epub", person=person, era=era)
    assert result.is_error and "not a folder's" in _text(result)
    assert person.asked == [] and not list(home.glob("*.pgn"))


@pytest.mark.parametrize(
    "char", [chr(code) for code in (10, 13, 0, 0x202E, 0x2028, 0x2029, 0x200B)]
)
def test_a_path_with_control_or_formatting_characters_is_refused_without_asking(
    library: Path, state: Path, home: Path, era: str, char: str
) -> None:
    person = Person()
    book = f"~/Library/primer{char}.epub"
    file = f"~/Chess{char}/One"
    results = _calls(
        state,
        [("doc_info", {"book": book}), _write(file, book="~/Library/primer.epub")],
        person=person,
        era=era,
    )
    assert "plain path, without control, formatting or line-separator" in _text(results[0])
    assert "plain path" in _text(results[1])
    # The book of the second call was asked about; the folder was not.
    assert [READ_QUESTION in question for question in person.asked] == [True]
    assert not (home / "Chess").exists()


def test_a_file_in_what_is_not_a_folder_is_refused_without_asking(
    library: Path, state: Path, home: Path, era: str
) -> None:
    (home / "plain").write_text("a file")
    person = Person()
    (result,) = _calls(
        state, [_write("~/plain/One")], books=library / "primer.epub", person=person, era=era
    )
    assert result.is_error and "~/plain is not a folder" in _text(result)
    assert person.asked == []


def test_a_file_in_pgnifys_own_folder_is_refused_without_asking(
    library: Path, state: Path, era: str
) -> None:
    person = Person()
    results = _calls(
        state,
        [_write(str(state / "books" / "evil")), _write(str(state / "evil"))],
        books=library / "primer.epub",
        person=person,
        era=era,
    )
    assert all(result.is_error and "pgnify's own folder" in _text(result) for result in results)
    assert person.asked == [] and not list(state.rglob("*.pgn"))


@pytest.mark.skipif(
    sys.platform not in ("darwin", "win32"), reason="the file system tells the case apart"
)
def test_pgnifys_own_folder_is_guarded_in_any_case(library: Path, state: Path, era: str) -> None:
    person = Person()
    (result,) = _calls(
        state,
        [_write(str(state).replace("state", "STATE") + "/books/evil")],
        books=library / "primer.epub",
        person=person,
        era=era,
    )
    assert result.is_error and "pgnify's own folder" in _text(result)
    assert person.asked == [] and not list(state.rglob("evil*"))


def test_a_link_loop_and_a_folder_the_system_will_not_read_give_plain_messages(
    library: Path, state: Path, home: Path, monkeypatch: pytest.MonkeyPatch, era: str
) -> None:
    (home / "Chess").mkdir()
    (home / "Chess" / "loop").symlink_to(home / "Chess" / "loop")
    book = library / "primer.epub"
    (loop,) = _calls(state, [_write("~/Chess/loop/One")], books=book, person=Person(), era=era)
    assert loop.is_error and "FileExistsError" not in _text(loop)
    assert "link" in _text(loop)
    real = Path.exists

    def exists(self: Path, **kwargs: Any) -> bool:
        if self.name == "Locked":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, **kwargs)

    monkeypatch.setattr(Path, "exists", exists)
    (locked_folder,) = _calls(state, [_write("~/Locked/One")], books=book, person=Person(), era=era)
    assert locked_folder.is_error and "cannot be read as a folder" in _text(locked_folder)
    assert "PermissionError" not in _text(locked_folder)


def test_a_folder_the_system_will_not_list_gives_a_plain_message_after_a_yes(
    library: Path, state: Path, home: Path, monkeypatch: pytest.MonkeyPatch, era: str
) -> None:
    (home / "Locked").mkdir()
    real = Path.iterdir

    def iterdir(self: Path) -> Any:
        if self.name == "Locked":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    (result,) = _calls(
        state, [_write("~/Locked/One")], books=library / "primer.epub", person=Person(), era=era
    )
    assert result.is_error and "cannot be read, so nothing was written" in _text(result)
    assert "PermissionError" not in _text(result)


def test_a_picture_of_a_book_is_read_only_where_the_book_is_allowed_in_the_session(
    library: Path, state: Path, era: str
) -> None:
    book = {"book": "~/Library/primer.epub"}
    (first,) = _calls(state, [("doc_images", {"page": 12, **book})], person=Person(), era=era)
    path = first.structured_content["images"][0]["path"]
    person = Person(book="decline")
    (refused,) = _calls(
        state, [("read_diagram", {"image_path": path, "box": [0, 0, 1, 1]})], person=person, era=era
    )
    assert refused.is_error and "has not allowed in this session" in _text(refused)
    assert person.asked == []
    (allowed,) = _calls(
        state,
        [
            ("doc_info", book),
            ("read_diagram", {"image_path": path, "box": [0, 0, 1, 1]}),
        ],
        person=Person(),
        era=era,
    )[1:]
    assert "has not allowed in this session" not in _text(allowed)


# Replacing a file


def test_a_file_replaces_and_keeps_notes_by_the_rules_of_the_output_folder(
    library: Path, state: Path, home: Path, era: str
) -> None:
    (home / "Chess").mkdir()
    (home / "Chess" / "There.pgn").write_text("before")
    (home / "Chess" / "Foreign.notes.md").write_text("somebody else's")
    (home / "Chess" / "Linked.pgn").symlink_to(home / "Chess" / "There.pgn")
    person = Person()
    results = _calls(
        state,
        [
            _write("~/Chess/There"),
            _write("~/Chess/There", replace=True),
            _write("~/Chess/Fresh", notes="Seen."),
            _write("~/Chess/Fresh", notes="Seen again."),
            _write("~/Chess/Foreign"),
            _write("~/Chess/Linked", replace=True),
        ],
        books=library / "primer.epub",
        person=person,
        era=era,
    )
    errors = [_text(result) if result.is_error else "" for result in results]
    assert "~/Chess/There.pgn was there before this session" in errors[0]
    assert not (errors[1] or errors[2] or errors[3])
    assert "~/Chess/Foreign.notes.md is not pgnify's notes" in errors[4]
    assert "~/Chess/Linked.pgn is not a plain file" in errors[5]
    assert (home / "Chess" / "There.pgn").read_text() == '[Result "*"]\n\n1. e4 *\n'
    notes = (home / "Chess" / "Fresh.notes.md").read_text()
    assert notes.startswith(NOTES_HEADING) and "Seen again." in notes
    assert "wrote ~/Chess/Fresh.pgn from primer.epub" in notes
    # The folder once, and the file there before, though replace was true.
    assert [question.split("\n\n")[0][:30] for question in person.asked] == [
        WRITE_QUESTION[:30],
        "Allow pgnify to replace this f",
    ]
    assert person.asked[1].endswith("\n\n~/Chess/There.pgn")


def test_a_file_written_in_this_session_from_another_book_is_not_replaced(
    library: Path, state: Path, home: Path, era: str
) -> None:
    person = Person()
    first, second = _calls(
        state,
        [
            _write("~/Chess/One", book="~/Library/primer.epub"),
            _write("~/Chess/One", book="~/Library/two-pages.pdf"),
        ],
        person=person,
        era=era,
    )
    assert not first.is_error
    assert second.is_error and "was written in this session from another book" in _text(second)


def _foreign(home: Path) -> Path:
    chess = home / "Chess"
    chess.mkdir()
    (chess / "Course.pgn").write_text("somebody else's")
    return chess


def test_a_file_pgnify_did_not_write_is_replaced_only_after_a_yes_for_the_file(
    library: Path, state: Path, home: Path, era: str
) -> None:
    chess = _foreign(home)
    person = Person()
    book = library / "primer.epub"
    first, second, third = _calls(
        state,
        [
            _write("~/Chess/Course", replace=True),
            _write("~/Chess/Course", replace=True),
            _write("~/Chess/Other", replace=True),
        ],
        books=book,
        person=person,
        era=era,
    )
    assert not (first.is_error or second.is_error or third.is_error)
    assert (chess / "Course.pgn").read_text() == '[Result "*"]\n\n1. e4 *\n'
    assert [question.split("\n\n")[0] for question in person.asked] == [
        WRITE_QUESTION,
        "Allow pgnify to replace this file?",
    ]
    assert person.asked[1].endswith(f"\n\n{shown(chess)}/Course.pgn")


@pytest.mark.parametrize("answer", ["decline", "unticked"])
def test_a_file_whose_replacing_the_person_refused_is_left_as_it_was_and_not_asked_about_again(
    library: Path, state: Path, home: Path, era: str, answer: str
) -> None:
    chess = _foreign(home)
    person = Person(replace=answer)
    first, again = _calls(
        state,
        [_write("~/Chess/Course", replace=True), _write("~/Chess/Course", replace=True)],
        books=library / "primer.epub",
        person=person,
        era=era,
    )
    for result in (first, again):
        assert result.is_error
        assert "the person did not allow pgnify to replace ~/Chess/Course.pgn" in _text(result)
    assert (chess / "Course.pgn").read_text() == "somebody else's"
    assert len(person.asked) == 2


def test_a_cancel_of_the_replace_question_refuses_the_call_alone(
    library: Path, state: Path, home: Path, era: str
) -> None:
    chess = _foreign(home)
    person = Person(replace="cancel")
    arguments = _write("~/Chess/Course", replace=True)

    async def act(client: Client) -> list[CallToolResult]:
        first = await client.call_tool(*arguments)
        person.answers["replace"] = "yes"
        return [first, await client.call_tool(*arguments)]

    first, second = in_session(
        library / "primer.epub", None, act, state=state, person=person, era=era
    )
    assert first.is_error and "cancelled the question" in _text(first)
    assert not second.is_error and (chess / "Course.pgn").read_text() != "somebody else's"


def test_the_replace_question_needs_replace_and_skips_a_file_this_session_wrote(
    library: Path, state: Path, home: Path, era: str
) -> None:
    _foreign(home)
    person = Person()
    primer, pdf = "~/Library/primer.epub", "~/Library/two-pages.pdf"
    refused, mine, again, other = _calls(
        state,
        [
            _write("~/Chess/Course", book=primer),
            _write("~/Chess/Mine", book=primer),
            _write("~/Chess/Mine", replace=True, book=primer),
            _write("~/Chess/Mine", replace=True, book=pdf),
        ],
        person=person,
        era=era,
    )
    assert refused.is_error and "was there before this session" in _text(refused)
    assert not mine.is_error and not again.is_error
    # Written from the other book, it is asked about, once.
    assert not other.is_error
    assert sum("replace this file" in question for question in person.asked) == 1


def test_the_replace_question_is_asked_after_the_folders_and_never_for_a_refused_folder(
    library: Path, state: Path, home: Path, era: str
) -> None:
    chess = _foreign(home)
    person = Person()
    (allowed,) = _calls(
        state,
        [_write("~/Chess/Course", replace=True)],
        books=library / "primer.epub",
        person=person,
        era=era,
    )
    assert not allowed.is_error
    kinds = ["replace" if "replace this file" in question else "write" for question in person.asked]
    assert kinds == ["write", "replace"]
    # A folder that is declined: the file is not asked about, then or later.
    (chess / "Course.pgn").write_text("somebody else's")
    person = Person(folder="decline")
    first, later = _calls(
        state,
        [_write("~/Chess/Course", replace=True), _write("~/Chess/Course", replace=True)],
        books=library / "primer.epub",
        person=person,
        era=era,
    )
    assert first.is_error and later.is_error
    assert not any("replace this file" in question for question in person.asked)
    assert len(person.asked) == 1
    assert (chess / "Course.pgn").read_text() == "somebody else's"


def test_given_out_a_file_is_replaced_on_replace_alone_without_a_question(
    primer: Path, tmp_path: Path, state: Path, era: str
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "There.pgn").write_text("before")
    person = Person()
    (result,) = _calls(
        state,
        [("write_pgn", {"games": [ONE_GAME], "file": "There", "replace": True})],
        books=primer,
        out=out,
        person=person,
        era=era,
    )
    assert not result.is_error and person.asked == []


def test_a_file_pgnify_wrote_is_known_by_its_identity_not_its_folded_path(
    library: Path, state: Path, home: Path, era: str
) -> None:
    # "ß" folds to "ss": two folders on any file system, as "Sub" and "sub" are on a case-sensitive
    # one, though their paths fold to one.
    (home / "Chess" / "ss").mkdir(parents=True)
    if (home / "Chess" / "ß").exists():
        pytest.skip("this file system folds ß into ss, as it folds case")
    theirs = home / "Chess" / "ss" / "x.pgn"
    theirs.write_text("somebody else's")
    person = Person()
    book = library / "primer.epub"
    mine, collides, replacing = _calls(
        state,
        [
            _write("~/Chess/ß/x"),
            _write("~/Chess/ss/x"),
            _write("~/Chess/ss/x", replace=True),
        ],
        books=book,
        person=person,
        era=era,
    )
    assert not mine.is_error
    assert collides.is_error and "was there before this session" in _text(collides)
    assert not replacing.is_error  # after the question about the file
    assert sum("replace this file" in question for question in person.asked) == 1
    assert theirs.read_text() != "somebody else's"


def test_a_file_someone_else_put_where_pgnify_wrote_is_not_pgnifys(
    library: Path, state: Path, home: Path
) -> None:
    outputs = Outputs(None, state)
    folder = home / "Chess"
    folder.mkdir()
    outputs.allowances.allowed.add(folder)
    book = Book("primer.epub", library / "primer.epub")
    file = str(folder / "x.pgn")
    mine = outputs.target(file, book, replace=False)
    mine.write_text("mine")
    outputs.wrote(mine, book, 1, "")
    assert outputs.target(file, book, replace=False) == mine
    # Another file moved over it: the same path, and not the file pgnify wrote.
    theirs = home / "theirs.pgn"
    theirs.write_text("somebody else's")
    os.replace(theirs, mine)
    with pytest.raises(ValueError, match="was there before this session"):
        outputs.target(file, book, replace=False)
    with pytest.raises(ValueError, match="has not allowed pgnify to replace"):
        outputs.target(file, book, replace=True)


# With --books and --out


def test_no_question_is_ever_asked_with_books_and_out(
    primer: Path, tmp_path: Path, state: Path, era: str
) -> None:
    person = Person()
    results = _calls(
        state,
        [
            ("doc_info", {}),
            ("doc_info", {"book": "pocket-primer.epub"}),
            ("doc_search", {"query": "king"}),
            ("validate_moves", {"start": "start", "moves": "1. e4"}),
            ("write_pgn", {"games": [ONE_GAME], "file": "Silman/One"}),
            # A full path, which --books refuses as it always did, and which asks nothing.
            ("doc_info", {"book": str(primer)}),
            ("write_pgn", {"games": [ONE_GAME], "file": str(tmp_path / "elsewhere")}),
        ],
        books=primer,
        out=tmp_path / "out",
        person=person,
        era=era,
    )
    assert [result.is_error for result in results] == [False] * 4 + [False, True, True]
    assert person.asked == []
    assert not state.exists()


def test_with_books_alone_the_folders_are_asked_and_with_out_alone_the_books(
    primer: Path, tmp_path: Path, state: Path, era: str
) -> None:
    person = Person()
    results = _calls(
        state,
        [
            ("doc_info", {}),
            _write("~/never", book=None),
        ],
        books=primer,
        person=person,
        era=era,
    )
    assert not results[0].is_error
    assert [WRITE_QUESTION in question for question in person.asked] == [True]
    person = Person()
    (result,) = _calls(
        state, [("doc_info", {"book": str(primer)})], out=tmp_path / "out", person=person, era=era
    )
    assert not result.is_error and len(person.asked) == 1


def test_the_tools_describe_the_rule_that_applies_in_each_mode(
    primer: Path, tmp_path: Path, state: Path
) -> None:
    async def read(client: Client) -> tuple[str, dict[str, Any], str]:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert not {"readable", "writable", "replacing"} & {
            name for tool in tools.values() for name in tool.input_schema["properties"]
        }
        schema = tools["doc_info"].input_schema["properties"]["book"]
        return (
            tools["write_pgn"].description or "",
            schema,
            client.instructions or "",
        )

    fenced = in_session(primer, tmp_path / "out", read, state=state)
    asked = in_session(None, None, read, state=state)
    assert "in pgnify's output folder" in fenced[0] and "the PGN file's full path" not in fenced[0]
    assert "file, needed without an output folder: the PGN file's full path" in asked[0]
    assert "the person is also asked before a file this session did not write" in " ".join(
        asked[0].split()
    )
    assert "by its name as the server's instructions" in fenced[1]["description"]
    assert "by its full path to a PDF or EPUB" in asked[1]["description"]
    assert "the books are 'pocket-primer.epub'" in fenced[2]
    assert "writes only into its output folder" in fenced[2]
    assert "the person allows: name each by its full path" in asked[2]
    assert "the folders the person allows" in asked[2]


def test_serve_says_in_its_help_what_happens_when_a_folder_is_left_out() -> None:
    help_text = " ".join(CliRunner().invoke(app, ["serve", "--help"]).output.split())
    assert "Without it, the assistant gives each book's full path" in help_text
    assert "Without it, the assistant gives each file's full path" in help_text
    assert "~/.local/state/pgnify/" in help_text


# Sessions that share pgnify's own folder


def test_the_records_and_the_log_are_in_pgnifys_own_folder_by_real_path(
    library: Path, state: Path, home: Path, era: str
) -> None:
    link = home / "Link"
    (home / "Real").mkdir()
    link.symlink_to(home / "Real")
    _calls(
        state,
        [
            ("build_pgn", {"spec": {"moves": move_specs("e4")}, "name": "g"}),
            _write("~/Link/One", names=None),
            ("check_fen", {"fen": "8/8/8/8/8/8/8/8 w - - 0 1"}),
        ],
        books=library / "primer.epub",
        person=Person(),
        era=era,
    )
    (record,) = json.loads((state / "outputs.json").read_text()).items()
    assert record[0] == str(home / "Real" / "One.pgn")
    assert record[1]["book"] == "primer.epub" and record[1]["games"] == 1
    tools = [json.loads(line)["tool"] for line in (state / "log.jsonl").read_text().splitlines()]
    assert tools == ["build_pgn", "write_pgn", "check_fen"]
    assert next(path.name for path in (state / "books").iterdir()).startswith("primer-")


def test_the_results_name_pictures_by_paths_from_pgnifys_own_folder(
    library: Path, state: Path, era: str
) -> None:
    (result,) = _calls(
        state,
        [("doc_images", {"page": 12, "book": "~/Library/primer.epub"})],
        person=Person(),
        era=era,
    )
    paths = [image["path"] for image in result.structured_content["images"]]
    assert paths and all(path.startswith("books/primer-") for path in paths)
    assert all((state / path).is_file() for path in paths)


def test_two_copies_of_a_book_get_folders_of_their_own_and_a_link_shares_its_targets(
    library: Path, state: Path, home: Path, era: str
) -> None:
    (home / "Copy").mkdir()
    shutil.copyfile(library / "primer.epub", home / "Copy" / "primer.epub")
    (home / "link.epub").symlink_to(library / "primer.epub")
    _calls(
        state,
        [
            ("doc_images", {"page": 12, "book": "~/Library/primer.epub"}),
            ("doc_images", {"page": 12, "book": "~/Copy/primer.epub"}),
            ("doc_images", {"page": 12, "book": "~/link.epub"}),
        ],
        person=Person(),
        out=None,
        era=era,
    )
    assert len(list((state / "books").iterdir())) == 2


def test_sessions_without_out_keep_the_games_they_save_apart(
    library: Path, state: Path, home: Path, era: str
) -> None:
    from pgnify.server import build_server

    book = "~/Library/primer.epub"

    async def run() -> tuple[CallToolResult, CallToolResult]:
        first, second = Person(), Person()
        async with (
            Client(
                build_server(None, None, state=state), mode=era, elicitation_callback=first
            ) as a,
            Client(
                build_server(None, None, state=state), mode=era, elicitation_callback=second
            ) as b,
        ):
            built = {"name": "g", "book": book}
            await a.call_tool("build_pgn", {"spec": {"moves": move_specs("e4")}, **built})
            await b.call_tool("build_pgn", {"spec": {"moves": move_specs("d4")}, **built})
            mine = await a.call_tool(
                "write_pgn", {"names": ["g"], "file": "~/Chess/A", "book": book}
            )
            theirs = await b.call_tool(
                "write_pgn", {"games": [ONE_GAME], "file": "~/Chess/B", "book": book}
            )
            return mine, theirs

    mine, theirs = asyncio.run(run())
    assert not mine.is_error, mine.content
    assert "1. e4" in (home / "Chess" / "A.pgn").read_text()
    assert mine.structured_content["unwritten"] == []
    # What the other session saved and never wrote is its own.
    assert theirs.structured_content["unwritten"] == ["g"]


def test_games_saved_in_an_earlier_session_are_said_to_need_building_again(
    library: Path, state: Path, home: Path, era: str
) -> None:
    book = "~/Library/primer.epub"
    _calls(
        state,
        [("build_pgn", {"spec": {"moves": move_specs("e4")}, "name": "g1", "book": book})],
        person=Person(),
        era=era,
    )
    (result,) = _calls(
        state,
        [("write_pgn", {"names": ["g1"], "file": "~/Chess/One", "book": book})],
        person=Person(),
        era=era,
    )
    assert result.is_error
    assert "no game saved as 'g1'" in _text(result)
    assert "If it was saved in an earlier session" in _text(result)
    assert "build it again with build_pgn" in _text(result)


def test_two_sessions_on_a_book_keep_each_others_figurine_readings(
    library: Path, state: Path
) -> None:
    code = """
import sys
from pathlib import Path
from pgnify.chess_tools import _record_figurines

file, number = sys.argv[1], sys.argv[2]
for index in range(80):
    _record_figurines(file, f"{number}-{index}", {f"p{point}": "N" for point in range(60)})
"""
    file = state / "books" / "b" / "pictures.json"
    processes = [
        subprocess.Popen([sys.executable, "-c", code, str(file), str(number)])
        for number in range(6)
    ]
    for process in processes:
        assert process.wait(timeout=120) == 0
    assert len(json.loads(file.read_text())) == 6 * 80


def test_two_sessions_recording_at_once_lose_no_record_and_no_log_line(
    state: Path, tmp_path: Path
) -> None:
    code = """
import sys
from pathlib import Path
from pgnify.books import Book
from pgnify.outputs import Outputs

state, number, count, source = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
outputs = Outputs(None, state)
book = Book(source.name, source)
for index in range(count):
    outputs.wrote(Path(f"/pgn/{number}/{index}.pgn"), book, 1, "")
    outputs.log({"tool": "check_fen", "arguments": {"fen": "x" * 20000}, "who": number})
"""
    book = tmp_path / "book.pdf"
    book.write_bytes(b"%PDF")
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(state), str(number), "25", str(book)],
            stderr=subprocess.PIPE,
        )
        for number in range(4)
    ]
    # The writing has the same folder open in this process too, in threads.
    folder = Outputs(None, state)
    source = Path(book)
    threads = [
        threading.Thread(target=_record, args=(folder, number, source)) for number in range(4, 8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for process in processes:
        assert process.wait(timeout=60) == 0, process.stderr and process.stderr.read()
    records = json.loads((state / "outputs.json").read_text())
    assert len(records) == 8 * 25
    lines = (state / "log.jsonl").read_text().splitlines()
    assert len(lines) == 8 * 25
    assert all(json.loads(line)["tool"] == "check_fen" for line in lines)


def _record(outputs: Outputs, number: int, book_path: Path) -> None:
    from pgnify.books import Book

    book = Book(book_path.name, book_path)
    for index in range(25):
        outputs.wrote(Path(f"/pgn/{number}/{index}.pgn"), book, 1, "")
        outputs.log({"tool": "check_fen", "arguments": {"fen": "x" * 20000}, "who": number})


def test_records_are_read_under_the_lock_and_a_replace_that_is_refused_is_tried_again(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = Outputs(None, state)
    book = state / "book.pdf"
    book.write_bytes(b"%PDF")
    folder.wrote(Path("/pgn/a.pgn"), Book("book.pdf", book), 1, "")
    finished = threading.Event()
    with locked(state / "lock"):
        reader = threading.Thread(target=lambda: (read_records(state), finished.set()), daemon=True)
        reader.start()
        assert not finished.wait(0.3)
    assert finished.wait(5)
    refused = iter([PermissionError, PermissionError, None])
    real = os.replace

    def replace(source: Path, target: Path) -> None:
        error = next(refused)
        if error:
            raise error("in use")
        real(source, target)

    monkeypatch.setattr(os, "replace", replace)
    replace_json(state / "x.json", {"a": 1})
    assert json.loads((state / "x.json").read_text()) == {"a": 1}
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(PermissionError("in use")))
    with pytest.raises(PermissionError):
        replace_json(state / "y.json", {})


# The report


def _written_in_a_chess_folder(library: Path, state: Path, home: Path, era: str = "legacy") -> Path:
    chess = home / "Chess"
    _calls(
        state,
        [
            _write("~/Chess/Silman/Part Four", notes="Seen."),
            _write("~/Chess/Top"),
            _write("~/Other/Elsewhere"),
        ],
        books=library / "primer.epub",
        person=Person(),
        era=era,
    )
    # A PGN file pgnify did not write, at the top of the folder and in a subfolder.
    (chess / "Unrecorded.pgn").write_text('[Result "*"]\n\n1. d4 *\n')
    (chess / "Silman" / "AlsoUnrecorded.pgn").write_text('[Result "*"]\n\n1. c4 *\n')
    return chess


def test_the_report_on_a_folder_without_pgnify_checks_its_top_and_the_recorded_files_below(
    library: Path, state: Path, home: Path, era: str
) -> None:
    chess = _written_in_a_chess_folder(library, state, home, era)
    reported = report(chess, state=state)
    # The file at the top that pgnify did not write is checked too, without a record.
    assert [output["file"] for output in reported["outputs"]] == [
        "Silman/Part Four.pgn",
        "Top.pgn",
        "Unrecorded.pgn",
    ]
    first, _, unrecorded = reported["outputs"]
    assert first["book"] == "primer.epub" and first["book_found"] is True
    assert first["model_account"] == "Seen."
    assert unrecorded["book"] is None
    (book,) = reported["books"]
    assert book["files"] == ["Silman/Part Four.pgn", "Top.pgn"]
    text = (chess / "report.md").read_text()
    assert "## Silman/Part Four.pgn" in text and "AlsoUnrecorded" not in text
    assert (chess / "report.json").is_file()
    assert not (home / "Other" / "report.md").exists()


def test_a_folder_without_records_reports_its_top_level_pgn_files(state: Path, home: Path) -> None:
    (home / "Empty").mkdir()
    assert report(home / "Empty", state=state)["outputs"] == []
    (home / "Empty" / "Mine.pgn").write_text('[Result "*"]\n\n1. e4 *\n')
    (output,) = report(home / "Empty", state=state)["outputs"]
    assert output["file"] == "Mine.pgn" and output["book"] is None


def test_a_folder_with_pgnify_records_leaves_pgnifys_own_folder_uncreated(
    primer: Path, tmp_path: Path, state: Path
) -> None:
    call_tools(primer, tmp_path, [("write_pgn", {"games": [ONE_GAME], "file": "Top"})])
    (tmp_path / "Unrecorded.pgn").write_text('[Result "*"]\n\n1. d4 *\n')
    reported = report(tmp_path, state=state)
    assert [output["file"] for output in reported["outputs"]] == ["Top.pgn", "Unrecorded.pgn"]
    assert not state.exists()


def test_the_report_reads_the_folders_records_and_pgnifys_own_and_the_later_wins(
    library: Path, state: Path, home: Path
) -> None:
    chess = home / "Chess"
    call_tools(library / "primer.epub", chess, [_write("Old")])
    _calls(
        state,
        [
            # 1.e5 is not legal, so bad is never saved: a possible issue of the book's.
            ("build_pgn", {"spec": {"moves": move_specs("e5")}, "name": "bad"}),
            _write("~/Chess/New"),
            _write("~/Chess/Sub/Deep"),
            _write("~/Chess/Old", replace=True),
        ],
        books=library / "two-pages.pdf",
        person=Person(),
    )
    reported = report(chess, state=state)
    books = {output["file"]: output["book"] for output in reported["outputs"]}
    # A file the two sources record is the later one's: Old was written again from the PDF.
    assert books == {
        "New.pgn": "two-pages.pdf",
        "Old.pgn": "two-pages.pdf",
        "Sub/Deep.pgn": "two-pages.pdf",
    }
    assert all(output["book_found"] for output in reported["outputs"])
    # Each source's log is reported under its book.
    (book,) = reported["books"]
    assert book["book"] == "two-pages.pdf"
    assert any("bad" in finding["what"] for finding in book["findings"])


def test_the_report_tool_errors_come_from_the_folders_log_alone(
    library: Path, state: Path, home: Path
) -> None:
    chess = home / "Chess"
    call_tools(
        library / "primer.epub",
        chess,
        [_write("Old"), ("doc_info", {"book": "no-such-book.epub"})],
    )
    _calls(
        state,
        [
            ("doc_info", {"book": "~/Library/missing.pdf"}),
            _write("~/Chess/New", book="~/Library/primer.epub"),
        ],
        person=Person(),
    )
    reported = report(chess, state=state)
    (finding,) = reported["findings"]
    assert "no-such-book.epub" in finding["what"] and "missing.pdf" not in finding["what"]


def test_the_report_leaves_out_a_record_that_leads_outside_the_folder(
    primer: Path, tmp_path: Path, state: Path
) -> None:
    out = tmp_path / "out"
    call_tools(primer, out, [_write("Top")])
    elsewhere = tmp_path / "elsewhere.pgn"
    elsewhere.write_text('[Result "*"]\n\n1. e4 *\n')
    records = out / ".pgnify" / "outputs.json"
    kept = json.loads(records.read_text())
    kept[str(elsewhere)] = {"book": "x"}
    kept["../elsewhere.pgn"] = {"book": "x"}
    records.write_text(json.dumps(kept))
    reported = report(out, state=state)
    assert [output["file"] for output in reported["outputs"]] == ["Top.pgn"]


def test_the_report_command_reads_pgnifys_own_folder(
    library: Path, state: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chess = _written_in_a_chess_folder(library, state, home)
    monkeypatch.setattr(outputs_module, "own_folder", lambda: state)
    monkeypatch.setattr("pgnify.report.own_folder", lambda: state)
    runner = CliRunner()
    found = runner.invoke(app, ["report", str(chess)])
    assert found.exit_code == 0, found.output
    assert "Top.pgn: 1 game" in found.output
    empty = runner.invoke(app, ["report", str(home / "Library")])
    assert empty.exit_code == 0 and (home / "Library" / "report.md").is_file()
