"""Tests of the MCP server through the SDK's in-process client."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult, GetPromptResult, TextContent, Tool
from support import EXERCISES_PAGE, ONE_GAME, PRIMER_PAGES, RUY, call_tools, in_session, move_specs

from pgnify import instructions
from pgnify.outputs import NOTES_HEADING


def _call(
    books: Path, out: Path, name: str, arguments: dict[str, Any], spanned: bool = False
) -> CallToolResult:
    (result,) = call_tools(books, out, [(name, arguments)], spanned)
    return result


def _tools(books: Path, out: Path, spanned: bool = False) -> dict[str, Tool]:
    async def listed(client: Client) -> dict[str, Tool]:
        return {tool.name: tool for tool in (await client.list_tools()).tools}

    return in_session(books, out, listed, spanned)


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
        "doc_search",
        "doc_text",
        "read_diagram",
        "validate_moves",
        "write_pgn",
    ]


def test_no_tool_takes_a_parameter_the_server_fills_itself(primer: Path, tmp_path: Path) -> None:
    for tool in _tools(primer, tmp_path).values():
        parameters = set(tool.input_schema["properties"])
        assert not {"path", "out_dir", "store", "figurines", "numbered"} & parameters, tool.name


def test_build_pgn_describes_its_spec_in_place_not_by_reference(
    primer: Path, tmp_path: Path
) -> None:
    # Ollama's tool format has no $ref: see server._add_build_pgn.
    schema = _tools(primer, tmp_path)["build_pgn"].input_schema
    spec = schema["properties"]["spec"]
    assert "$ref" not in spec
    assert spec["type"] == "object"
    assert sorted(spec["properties"]) == ["comment", "headers", "moves", "result", "start_fen"]
    assert spec["properties"]["moves"]["items"] == {"$ref": "#/$defs/MoveSpec"}
    assert "MoveSpec" in schema["$defs"]


def test_with_spans_the_server_numbers_lines_and_copies_spans_into_comments(
    primer: Path, tmp_path: Path
) -> None:
    tools = _tools(primer, tmp_path, spanned=True)
    assert sorted(tools) == sorted(_tools(primer, tmp_path))
    text = _call(primer, tmp_path, "doc_text", {"first_page": 9}, spanned=True)
    assert "7| If Black pushes" in text.structured_content["pages"][0]["text"]
    span = {"page": 9, "line": 7, "first": "If Black", "last": "instead,"}
    spec = {"moves": [{"san": "e4", "comment": [span]}]}
    built = _call(primer, tmp_path, "build_pgn", {"spec": spec, "name": "g"}, spanned=True)
    assert not built.is_error, built.content
    assert built.structured_content["saved"] == "g"
    (saved,) = (tmp_path / ".pgnify" / "books").glob("*/games/g.pgn")
    assert "If Black pushes with 2...h3 instead," in saved.read_text()


def test_without_spans_doc_text_numbers_no_lines(primer: Path, tmp_path: Path) -> None:
    text = _call(primer, tmp_path, "doc_text", {"first_page": 9})
    assert "| If Black" not in text.structured_content["pages"][0]["text"]


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
    assert "image_path must be a picture doc_images extracted" in _text(result)


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
