"""Tests of the MCP server through the SDK's in-process client."""

import base64
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult, GetPromptResult, ImageContent, TextContent, Tool
from support import (
    EXERCISES_PAGE,
    ONE_GAME,
    PRIMER_PAGES,
    RUY,
    call_tools,
    exercises,
    in_session,
    move_specs,
)

from pgnify import instructions
from pgnify.outputs import NOTES_HEADING


def _call(books: Path, out: Path, name: str, arguments: dict[str, Any]) -> CallToolResult:
    (result,) = call_tools(books, out, [(name, arguments)])
    return result


def _tools(books: Path, out: Path) -> dict[str, Tool]:
    async def listed(client: Client) -> dict[str, Tool]:
        return {tool.name: tool for tool in (await client.list_tools()).tools}

    return in_session(books, out, listed)


def _text(result: CallToolResult) -> str:
    """The text a result gives first, such as a tool's error."""
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


@pytest.fixture
def books_folder(primer: Path, two_pages: Path, tmp_path: Path) -> Path:
    """A folder of two books, one in a subfolder."""
    books = tmp_path / "books"
    (books / "endgames").mkdir(parents=True)
    shutil.copyfile(primer, books / "endgames" / "Pocket Primer.epub")
    shutil.copyfile(two_pages, books / "two-pages.pdf")
    return books


def test_the_server_serves_the_document_diagram_chess_and_output_tools(
    primer: Path, tmp_path: Path
) -> None:
    assert sorted(_tools(primer, tmp_path)) == [
        "build_pgn",
        "check_fen",
        "check_pgn",
        "doc_images",
        "doc_info",
        "doc_page",
        "doc_search",
        "doc_text",
        "read_diagram",
        "validate_moves",
        "write_pgn",
    ]


def test_no_tool_takes_a_parameter_the_server_fills_itself(primer: Path, tmp_path: Path) -> None:
    for tool in _tools(primer, tmp_path).values():
        parameters = set(tool.input_schema["properties"])
        assert not {"path", "out_dir", "store", "figurines"} & parameters, tool.name


def test_build_pgn_describes_its_spec_in_place_not_by_reference(
    primer: Path, tmp_path: Path
) -> None:
    # Ollama's tool format has no $ref: see server._add_build_pgn.
    schema = _tools(primer, tmp_path)["build_pgn"].input_schema
    spec = schema["properties"]["spec"]
    assert "$ref" not in spec
    assert spec["type"] == "object"
    assert sorted(spec["properties"]) == ["comment", "headers", "moves", "result", "start_fen"]
    assert spec["properties"]["moves"]["items"] == {"$ref": "#/$defs/CopyingMoveSpec"}
    assert "CopyingMoveSpec" in schema["$defs"]


def test_the_server_copies_a_passage_of_the_book_into_a_comment(
    primer: Path, tmp_path: Path
) -> None:
    copy = {"page": 9, "from": "If Black", "to": "instead,"}
    spec = {"moves": [{"san": "e4", "comment": [copy]}]}
    built = _call(primer, tmp_path, "build_pgn", {"spec": spec, "name": "g"})
    assert not built.is_error, built.content
    assert built.structured_content["saved"] == "g"
    assert built.structured_content["copied"][0]["words"] == 6
    (saved,) = (tmp_path / ".pgnify" / "books").glob("*/games/g.pgn")
    assert "If Black pushes with 2...h3 instead," in saved.read_text()


def test_build_pgn_says_how_a_comment_copies_from_the_book(primer: Path, tmp_path: Path) -> None:
    description = _tools(primer, tmp_path)["build_pgn"].description or ""
    assert "a copy of the book's words" in description
    assert "fix, optional" in description
    said = " ".join(description.split())
    assert "in a PDF, or where an EPUB's page ends with a picture, a copy stays on its page" in said
    assert "pieces, optional" in description


def test_build_pgn_takes_pieces_and_applies_them_to_the_comments_it_copies(
    primer: Path, tmp_path: Path
) -> None:
    schema = _tools(primer, tmp_path)["build_pgn"].input_schema
    assert schema["properties"]["pieces"]["anyOf"][0]["type"] == "object"
    copy = {"page": 10, "from": "1.d6", "to": "2.\u2654e6"}
    spec = {"moves": [{"san": "e4", "comment": [copy]}]}
    built = _call(
        primer, tmp_path, "build_pgn", {"spec": spec, "pieces": {"\u2656": "R", "\u2654": "K"}}
    )
    assert not built.is_error, built.content
    assert "1.d6 Rb1! 2.Ke6" in built.structured_content["pgn"]
    refused = _call(primer, tmp_path, "build_pgn", {"spec": spec, "pieces": {"\u2656": "X"}})
    assert refused.is_error
    assert "not one of K, Q, R, B or N" in str(refused.content)


def test_validate_moves_replays_a_line_through_the_server(primer: Path, tmp_path: Path) -> None:
    moves = "1. e4 e5 2. Nf3 Nc6 3. Bb5"
    result = _call(primer, tmp_path, "validate_moves", {"start": "start", "moves": moves})
    assert not result.is_error
    assert result.structured_content["final_fen"] == RUY


def test_doc_info_needs_no_book_name_when_there_is_one_book(primer: Path, tmp_path: Path) -> None:
    result = _call(primer, tmp_path, "doc_info", {})
    assert result.structured_content["page_count"] == PRIMER_PAGES


def test_doc_images_extracts_into_the_book_folder_by_relative_paths(
    primer: Path, tmp_path: Path
) -> None:
    result = _call(primer, tmp_path, "doc_images", {"page": EXERCISES_PAGE})
    paths = [image["path"] for image in result.structured_content["images"]]
    assert len(paths) == 2
    assert all(path.startswith(".pgnify/books/pocket-primer-") for path in paths)
    assert all((tmp_path / path).is_file() for path in paths)


def test_build_pgn_takes_a_spec_as_json_text_through_the_server(
    primer: Path, tmp_path: Path
) -> None:
    spec = '{"moves": [{"san": "e4", "variations": [[{"san": "d4"}]]}]}'
    result = _call(primer, tmp_path, "build_pgn", {"spec": spec})
    assert not result.is_error
    assert "1. e4 ( 1. d4 ) *" in result.structured_content["pgn"]


def test_build_pgn_refuses_malformed_json_text_through_the_server(
    primer: Path, tmp_path: Path
) -> None:
    result = _call(primer, tmp_path, "build_pgn", {"spec": '{"moves": [{"san": "e4"}'})
    assert result.is_error
    assert "the spec's JSON is malformed: Expecting" in _text(result)


def test_build_pgn_reports_a_wrong_spec_object_without_suggesting_text(
    primer: Path, tmp_path: Path
) -> None:
    # A second line written inside the first, as a list among its moves.
    moves = [{"san": "e4", "variations": [[{"san": "d4"}, [{"san": "c4"}]]]}]
    result = _call(primer, tmp_path, "build_pgn", {"spec": {"moves": moves}})
    assert result.is_error
    message = _text(result)
    assert "moves.0.variations.0.1" in message
    assert "valid string" not in message


def test_games_saved_by_name_are_written_through_the_server(primer: Path, tmp_path: Path) -> None:
    built, written = call_tools(
        primer,
        tmp_path,
        [
            ("build_pgn", {"spec": {"moves": move_specs("e4")}, "name": "a"}),
            ("write_pgn", {"names": ["a"], "file": "games"}),
        ],
    )
    assert built.structured_content["saved"] == "a"
    assert written.structured_content["saved"] == 1
    assert "1. e4 *" in (tmp_path / "games.pgn").read_text()


def test_with_one_book_figurine_readings_carry_over_without_the_book_named(
    primer: Path, tmp_path: Path
) -> None:
    moves = move_specs("e4", "e5", "[aaaaaa]f3", "[aaaaaa]c6")
    _, later = call_tools(
        primer,
        tmp_path,
        [
            ("build_pgn", {"spec": {"moves": moves}, "name": "a"}),
            ("validate_moves", {"start": "start", "moves": "1 e4 e5 2 [aaaaaa]e2"}),
        ],
    )
    assert later.structured_content["plies"][-1]["san"] == "Ne2"


@pytest.mark.parametrize("picture", ["/elsewhere.png", "../elsewhere.png", "report.md"])
def test_read_diagram_refuses_a_picture_doc_images_did_not_extract(
    primer: Path, tmp_path: Path, picture: str
) -> None:
    result = _call(primer, tmp_path, "read_diagram", {"image_path": picture})
    assert result.is_error
    assert "image_path must be a picture doc_images extracted or doc_page showed" in _text(result)


def test_read_diagram_asks_for_a_picture_or_a_page(primer: Path, tmp_path: Path) -> None:
    result = _call(primer, tmp_path, "read_diagram", {})
    assert result.is_error
    assert "give image_path" in _text(result) and "or page" in _text(result)


def test_read_diagram_takes_a_page_alone(primer: Path, tmp_path: Path) -> None:
    arguments = {"page": EXERCISES_PAGE, "image_path": "page12-1.png"}
    result = _call(primer, tmp_path, "read_diagram", arguments)
    assert result.is_error
    assert "give page alone" in _text(result)


def test_the_log_keeps_whether_doc_text_read_by_ocr(primer: Path, tmp_path: Path) -> None:
    call_tools(primer, tmp_path, [("doc_text", {"first_page": 9})])
    (entry,) = [json.loads(line) for line in (tmp_path / ".pgnify" / "log.jsonl").open()]
    assert entry["arguments"] == {"first_page": 9, "last_page": None, "ocr": False}


def test_the_log_keeps_the_pieces_build_pgn_was_given(primer: Path, tmp_path: Path) -> None:
    pieces = {"\u2656": "R"}
    call_tools(
        primer, tmp_path, [("build_pgn", {"spec": {"comment": "A \u2656."}, "pieces": pieces})]
    )
    (entry,) = [json.loads(line) for line in (tmp_path / ".pgnify" / "log.jsonl").open()]
    assert entry["arguments"] == {
        "name": None,
        "spec": {"start_fen": None, "moves": 0},
        "pieces": pieces,
    }


@pytest.mark.live
@pytest.mark.usefixtures("checkout_weights")
def test_the_log_keeps_the_boards_found_on_a_page_without_the_text_beside_them(
    tmp_path: Path,
) -> None:
    book = exercises(tmp_path)
    result = _call(book, tmp_path / "out", "read_diagram", {"page": 1})
    assert not result.is_error
    found = result.structured_content
    assert found["path"].startswith(".pgnify/books/") and len(found["boards"]) == 2
    assert found["boards"][0]["text"]["left"]
    (entry,) = [json.loads(line) for line in (tmp_path / "out" / ".pgnify" / "log.jsonl").open()]
    assert entry["arguments"] == {"page": 1}
    assert [board["placement"] for board in entry["result"]["boards"]] == [
        board["placement"] for board in found["boards"]
    ]
    assert all("text" not in board for board in entry["result"]["boards"])


def test_doc_page_gives_the_model_the_picture_itself_and_its_path_in_the_book_folder(
    primer: Path, tmp_path: Path
) -> None:
    result = _call(primer, tmp_path, "doc_page", {"page": EXERCISES_PAGE})
    assert not result.is_error
    # The blocks alone, which every client passes on: no structured content to stand in for them.
    assert result.structured_content is None
    text, picture = result.content
    assert isinstance(text, TextContent) and isinstance(picture, ImageContent)
    shown = json.loads(text.text)
    assert shown["page"] == EXERCISES_PAGE
    assert shown["path"].startswith(".pgnify/books/pocket-primer-")
    assert picture.mime_type == "image/png"
    assert base64.b64decode(picture.data) == (tmp_path / shown["path"]).read_bytes()


def test_doc_page_logs_the_part_shown_and_the_picture_path_not_the_picture(
    primer: Path, tmp_path: Path
) -> None:
    box = [40, 60, 360, 300]
    result = _call(primer, tmp_path, "doc_page", {"page": EXERCISES_PAGE, "box": box})
    text, _ = result.content
    assert isinstance(text, TextContent)
    (entry,) = [
        json.loads(line)
        for line in (tmp_path / ".pgnify" / "log.jsonl").read_text().splitlines()
        if json.loads(line)["tool"] == "doc_page"
    ]
    assert entry["arguments"] == {"page": EXERCISES_PAGE, "box": box}
    shown = json.loads(text.text)
    kept = ("page", "box", "path", "width", "height")
    assert entry["result"] == {key: shown[key] for key in kept}


def test_doc_page_refuses_a_page_the_book_lacks_with_its_reason(
    primer: Path, tmp_path: Path
) -> None:
    result = _call(primer, tmp_path, "doc_page", {"page": PRIMER_PAGES + 1})
    assert result.is_error
    assert f"no page {PRIMER_PAGES + 1}: pages run from 1 to {PRIMER_PAGES}" in _text(result)


def test_a_tool_error_reaches_the_model_with_its_reason(primer: Path, tmp_path: Path) -> None:
    result = _call(primer, tmp_path, "doc_text", {"first_page": 99})
    assert result.is_error
    assert f"no page 99: pages run from 1 to {PRIMER_PAGES}" in _text(result)


def test_with_several_books_a_tool_needs_the_books_name_in_any_letter_case(
    books_folder: Path, tmp_path: Path
) -> None:
    unnamed, named, folded = call_tools(
        books_folder,
        tmp_path / "out",
        [
            ("doc_info", {}),
            ("doc_info", {"book": "two-pages.pdf"}),
            ("doc_info", {"book": "ENDGAMES/POCKET PRIMER.EPUB"}),
        ],
    )
    assert unnamed.is_error
    assert "'endgames/Pocket Primer.epub', 'two-pages.pdf'" in _text(unnamed)
    assert named.structured_content["page_count"] == 2
    assert folded.structured_content["page_count"] == PRIMER_PAGES


@pytest.mark.parametrize("name", ["../secret.pdf", "linked.pdf", "endgames/../../secret.pdf"])
def test_no_book_name_opens_a_file_outside_the_books_location(
    books_folder: Path, tmp_path: Path, name: str
) -> None:
    secret = tmp_path / "secret.pdf"
    shutil.copyfile(books_folder / "two-pages.pdf", secret)
    (books_folder / "linked.pdf").symlink_to(secret)
    result = _call(books_folder, tmp_path / "out", "doc_info", {"book": name})
    assert result.is_error
    assert "there is no book" in _text(result)


def test_saved_games_and_figurine_readings_are_kept_per_book(
    books_folder: Path, tmp_path: Path
) -> None:
    moves = move_specs("e4", "e5", "[aaaaaa]f3", "[aaaaaa]c6")
    line = "1 e4 e5 2 [aaaaaa]e2"
    saved, known, unknown, elsewhere, unnamed = call_tools(
        books_folder,
        tmp_path / "out",
        [
            ("build_pgn", {"spec": {"moves": moves}, "name": "a", "book": "two-pages.pdf"}),
            ("validate_moves", {"start": "start", "moves": line, "book": "two-pages.pdf"}),
            (
                "validate_moves",
                {"start": "start", "moves": line, "book": "endgames/Pocket Primer.epub"},
            ),
            ("write_pgn", {"names": ["a"], "book": "endgames/Pocket Primer.epub"}),
            ("build_pgn", {"spec": {"moves": moves}, "name": "b"}),
        ],
    )
    assert saved.structured_content["saved"] == "a"
    assert known.structured_content["plies"][-1]["san"] == "Ne2"
    assert unknown.structured_content["ok"] is False
    assert elsewhere.is_error and "no game saved as 'a'" in _text(elsewhere)
    assert unnamed.is_error and "name the book this game is from" in _text(unnamed)
    (folder,) = (tmp_path / "out" / ".pgnify" / "books").iterdir()
    assert folder.name.startswith("two-pages-")
    assert [game.name for game in (folder / "games").iterdir()] == ["a.pgn"]


def test_write_pgn_writes_a_file_named_for_the_book_by_default(
    primer: Path, tmp_path: Path
) -> None:
    result = _call(primer, tmp_path, "write_pgn", {"games": [ONE_GAME]})
    assert result.structured_content["ok"] is True
    assert result.structured_content["file"] == "pocket-primer.pgn"
    assert (tmp_path / "pocket-primer.pgn").read_text() == ONE_GAME + "\n"


def test_write_pgn_replaces_a_file_from_the_same_book_but_never_one_from_another(
    books_folder: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    first, again, other = call_tools(
        books_folder,
        out,
        [
            ("write_pgn", {"games": [ONE_GAME], "file": "same", "book": "two-pages.pdf"}),
            ("write_pgn", {"games": [ONE_GAME], "file": "same", "book": "two-pages.pdf"}),
            (
                "write_pgn",
                {"games": [ONE_GAME], "file": "SAME.pgn", "book": "endgames/Pocket Primer.epub"},
            ),
        ],
    )
    assert not first.is_error and not again.is_error
    assert other.is_error and "from another book" in _text(other)
    assert [path.name for path in out.glob("*.pgn")] == ["same.pgn"]


def test_write_pgn_replaces_a_file_from_before_the_session_only_when_asked(
    primer: Path, tmp_path: Path
) -> None:
    (tmp_path / "kept.pgn").write_text(ONE_GAME + "\n")
    before, replaced = call_tools(
        primer,
        tmp_path,
        [
            ("write_pgn", {"games": [ONE_GAME], "file": "kept"}),
            ("write_pgn", {"games": [ONE_GAME], "file": "kept", "replace": True}),
        ],
    )
    assert before.is_error and "was there before this session" in _text(before)
    assert not replaced.is_error


def test_write_pgn_keeps_the_models_notes_beside_the_file_and_records_its_book(
    primer: Path, tmp_path: Path
) -> None:
    notes = "Exercise 4's solution is not in the book."
    written, rewritten = call_tools(
        primer,
        tmp_path,
        [
            ("write_pgn", {"games": [ONE_GAME], "notes": notes}),
            ("write_pgn", {"games": [ONE_GAME]}),
        ],
    )
    assert written.structured_content["notes"] is True
    assert rewritten.structured_content["notes"] is False
    assert not (tmp_path / "pocket-primer.notes.md").exists()
    record = json.loads((tmp_path / ".pgnify" / "outputs.json").read_text())["pocket-primer.pgn"]
    assert record["book"] == "pocket-primer.epub"
    # A new session must ask to replace the file.
    call_tools(
        primer, tmp_path, [("write_pgn", {"games": [ONE_GAME], "notes": notes, "replace": True})]
    )
    kept = (tmp_path / "pocket-primer.notes.md").read_text()
    assert kept.startswith(NOTES_HEADING) and notes in kept


def test_every_call_is_logged_with_its_book(primer: Path, tmp_path: Path) -> None:
    call_tools(
        primer,
        tmp_path,
        [("doc_text", {"first_page": 9}), ("doc_text", {"first_page": 99})],
    )
    entries = [
        json.loads(line) for line in (tmp_path / ".pgnify" / "log.jsonl").read_text().splitlines()
    ]
    assert [entry["tool"] for entry in entries] == ["doc_text", "doc_text"]
    assert entries[0]["book"]["name"] == "pocket-primer.epub"
    assert entries[0]["result"]["pages"] == [9]
    assert "no page 99" in entries[1]["error"]


def test_errors_name_the_servers_folders_not_their_paths(
    books_folder: Path, tmp_path: Path
) -> None:
    async def unlink_then_call(client: Client) -> CallToolResult:
        (books_folder / "two-pages.pdf").unlink()
        return await client.call_tool("doc_info", {"book": "two-pages.pdf"})

    result = in_session(books_folder, tmp_path / "out", unlink_then_call)
    assert result.is_error
    assert "'<books>/two-pages.pdf'" in _text(result)
    assert str(tmp_path) not in _text(result)


def test_errors_name_the_servers_folders_where_a_message_escapes_their_paths(
    primer: Path, tmp_path: Path
) -> None:
    books = tmp_path / "Chess\u00a0Books 'and' \"more\""
    books.mkdir()
    shutil.copyfile(primer, books / "primer.epub")
    (books / "empty.pdf").write_bytes(b"")
    result = _call(books, tmp_path / "out", "doc_info", {"book": "empty.pdf"})
    assert result.is_error
    assert "<books>/empty.pdf" in _text(result)
    assert "Chess" not in _text(result)


def test_the_prompt_convert_gives_the_request_then_the_instructions(
    primer: Path, tmp_path: Path
) -> None:
    async def convert(client: Client) -> tuple[list[str], GetPromptResult]:
        names = [prompt.name for prompt in (await client.list_prompts()).prompts]
        arguments = {"book": "pocket-primer.epub", "part": "Chapter 1"}
        return names, await client.get_prompt("convert", arguments)

    names, prompt = in_session(primer, tmp_path, convert)
    assert names == ["convert"]
    (message,) = prompt.messages
    assert isinstance(message.content, TextContent)
    text = message.content.text
    assert text.startswith("Convert Chapter 1 of the book pocket-primer.epub into PGN")
    assert text.endswith(instructions.text())


def test_the_servers_instructions_list_its_books(primer: Path, tmp_path: Path) -> None:
    async def read_instructions(client: Client) -> str | None:
        return client.instructions

    server_instructions = in_session(primer, tmp_path, read_instructions)
    assert server_instructions is not None and "'pocket-primer.epub'" in server_instructions


def test_write_pgn_saves_in_a_new_folder_with_its_notes_beside_it(
    books_folder: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    name = "Silman/Part Four/Games"
    *_, written = call_tools(
        books_folder,
        out,
        [
            _saved("a"),
            (
                "write_pgn",
                {"names": ["a"], "file": name, "notes": "Left out p. 9.", "book": "two-pages.pdf"},
            ),
        ],
    )
    assert not written.is_error
    assert written.structured_content is not None
    assert written.structured_content["file"] == "Silman/Part Four/Games.pgn"
    assert written.structured_content["unwritten"] == []
    folder = out / "Silman" / "Part Four"
    assert "1. e4 e5" in (folder / "Games.pgn").read_text()
    assert (folder / "Games.notes.md").read_text().startswith(NOTES_HEADING)
    assert sorted(path.name for path in out.iterdir()) == [".pgnify", "Silman"]
    records = json.loads((out / ".pgnify" / "outputs.json").read_text())
    assert list(records) == ["Silman/Part Four/Games.pgn"]
    assert records["Silman/Part Four/Games.pgn"]["book"] == "two-pages.pdf"
    assert records["Silman/Part Four/Games.pgn"]["notes"] is True


def test_write_pgn_makes_no_folder_when_it_refuses_to_write(
    books_folder: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    (tmp_path / "elsewhere").mkdir()
    out.mkdir()
    (out / "linked").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    empty, escaping, linked = call_tools(
        books_folder,
        out,
        [
            ("write_pgn", {"games": [], "file": "New/Games", "book": "two-pages.pdf"}),
            ("write_pgn", {"games": [ONE_GAME], "file": "../Up/Games", "book": "two-pages.pdf"}),
            ("write_pgn", {"games": [ONE_GAME], "file": "linked/Games", "book": "two-pages.pdf"}),
        ],
    )
    assert empty.is_error and "no games given" in _text(empty)
    assert escaping.is_error and "not a plain file name" in _text(escaping)
    assert linked.is_error and "is a link, so nothing was written" in _text(linked)
    assert sorted(path.name for path in out.iterdir()) == [".pgnify", "linked"]
    assert list((tmp_path / "elsewhere").iterdir()) == []
    assert not (tmp_path / "Up").exists()


def _saved(
    name: str, book: str = "two-pages.pdf", moves: tuple[str, ...] = ("e4", "e5")
) -> tuple[str, dict[str, Any]]:
    return ("build_pgn", {"spec": {"moves": move_specs(*moves)}, "name": name, "book": book})


def _saved_text(out: Path, book: str, name: str) -> str:
    (folder,) = (
        path for path in (out / ".pgnify" / "books").iterdir() if path.name.startswith(book)
    )
    return (folder / "games" / f"{name}.pgn").read_text()


def test_write_pgn_names_the_saved_games_no_file_holds(books_folder: Path, tmp_path: Path) -> None:
    *_, one, rest = call_tools(
        books_folder,
        tmp_path / "out",
        [
            _saved("a"),
            _saved("b", moves=("d4", "d5")),
            ("write_pgn", {"names": ["a"], "file": "one", "book": "two-pages.pdf"}),
            ("write_pgn", {"names": ["b"], "file": "rest", "book": "two-pages.pdf"}),
        ],
    )
    assert one.structured_content["unwritten"] == ["b"]
    assert rest.structured_content["unwritten"] == []


def test_write_pgn_counts_a_saved_game_given_as_text_exactly_as_written(
    books_folder: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    call_tools(books_folder, out, [_saved("a"), _saved("b", moves=("d4", "d5"))])
    text = _saved_text(out, "two-pages", "a")
    other = text.replace("1. e4 e5", "1. c4 c5")
    assert other != text
    (kept,) = call_tools(
        books_folder,
        out,
        [("write_pgn", {"games": [f"\n{text}\n", other], "book": "two-pages.pdf"})],
    )
    assert kept.structured_content["unwritten"] == ["b"]


def test_write_pgn_never_names_a_game_saved_from_another_book(
    books_folder: Path, tmp_path: Path
) -> None:
    *_, written = call_tools(
        books_folder,
        tmp_path / "out",
        [
            _saved("a", "endgames/Pocket Primer.epub"),
            ("write_pgn", {"games": [ONE_GAME], "book": "two-pages.pdf"}),
        ],
    )
    assert written.structured_content["unwritten"] == []


def test_write_pgn_names_a_game_a_file_written_again_dropped(
    books_folder: Path, tmp_path: Path
) -> None:
    *_, whole, fewer = call_tools(
        books_folder,
        tmp_path / "out",
        [
            _saved("a"),
            _saved("b", moves=("d4", "d5")),
            ("write_pgn", {"names": ["a", "b"], "file": "f", "book": "two-pages.pdf"}),
            ("write_pgn", {"names": ["a"], "file": "f", "book": "two-pages.pdf"}),
        ],
    )
    assert whole.structured_content["unwritten"] == []
    assert fewer.structured_content["unwritten"] == ["b"]


def test_write_pgn_names_a_game_saved_again_after_its_file_was_written(
    books_folder: Path, tmp_path: Path
) -> None:
    *_, again = call_tools(
        books_folder,
        tmp_path / "out",
        [
            _saved("a"),
            ("write_pgn", {"names": ["a"], "file": "f", "book": "two-pages.pdf"}),
            _saved("a", moves=("d4", "d5")),
            ("write_pgn", {"games": [ONE_GAME], "file": "g", "book": "two-pages.pdf"}),
        ],
    )
    assert again.structured_content["unwritten"] == ["a"]


def test_write_pgn_checks_the_book_a_file_was_written_from(
    books_folder: Path, tmp_path: Path
) -> None:
    *_, other = call_tools(
        books_folder,
        tmp_path / "out",
        [
            _saved("a"),
            _saved("a", "endgames/Pocket Primer.epub"),
            ("write_pgn", {"names": ["a"], "file": "one", "book": "two-pages.pdf"}),
            (
                "write_pgn",
                {"games": [ONE_GAME], "file": "two", "book": "endgames/Pocket Primer.epub"},
            ),
        ],
    )
    assert other.structured_content["unwritten"] == ["a"]


@pytest.mark.parametrize(
    "quoted",
    ["1. d4 {{ quoted:\n\n{text} }} 1-0", "1. d4 {{ quoted: {text}\n\n}} 1-0"],
    ids=["followed", "preceded"],
)
def test_write_pgn_does_not_count_a_game_quoted_in_a_comment(
    books_folder: Path, tmp_path: Path, quoted: str
) -> None:
    out = tmp_path / "out"
    call_tools(books_folder, out, [_saved("a")])
    text = _saved_text(out, "two-pages", "a")
    (result,) = call_tools(
        books_folder,
        out,
        [("write_pgn", {"games": [quoted.format(text=text)], "book": "two-pages.pdf"})],
    )
    assert result.structured_content["unwritten"] == ["a"]


def test_write_pgn_ignores_whitespace_around_a_game_and_its_lines(
    books_folder: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    call_tools(books_folder, out, [_saved("a"), _saved("b", moves=("d4", "d5"))])
    a, b = (_saved_text(out, "two-pages", name) for name in "ab")

    def spaced(text: str) -> str:
        return text.replace("\n", " \n ")

    (result,) = call_tools(
        books_folder,
        out,
        [
            (
                "write_pgn",
                {"games": [spaced(a) + " ", " " + spaced(b) + "\n \n"], "book": "two-pages.pdf"},
            )
        ],
    )
    assert result.structured_content["unwritten"] == []


def test_write_pgn_sorts_the_names(books_folder: Path, tmp_path: Path) -> None:
    *_, result = call_tools(
        books_folder,
        tmp_path / "out",
        [
            _saved("game"),
            _saved("game-1", moves=("d4", "d5")),
            _saved("game_2", moves=("c4", "c5")),
            ("write_pgn", {"games": [ONE_GAME], "book": "two-pages.pdf"}),
        ],
    )
    assert result.structured_content["unwritten"] == ["game", "game-1", "game_2"]
