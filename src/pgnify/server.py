"""The MCP server: pgnify's tools over stdio, for the books at one location and output in one
folder (`pgnify serve --books PATH --out DIR`).

Each tool is a plain function, served with its docstring as its description, except that the
server supplies every file path itself: the document tools read only the books (books.py), by
name, and the output tools write only the output folder (outputs.py), under plain file names.
The model names no path but a picture's, as doc_images returns it and read_diagram takes it.

The server runs calls one at a time, since PyMuPDF is not thread-safe and a model may send
several at once. It passes a tool's failure to the model with its reason, the server's folders
shown as <books> and <out>, and logs every call in the output folder, for `pgnify report`.
With --spans, doc_text numbers its lines and build_pgn can copy spans of them (spans.py)."""

import functools
import inspect
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import pymupdf
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BeforeValidator, Field

from pgnify import __version__, chess_tools, diagram_tools, doc_tools, instructions, spans
from pgnify.books import Book, BooksLocation
from pgnify.outputs import Outputs

_ONE_AT_A_TIME = threading.Lock()
# The longest argument text, and the longest error, the log keeps whole.
_LOGGED_TEXT = 200
_LOGGED_ERROR = 300

BookName = Annotated[
    str | None,
    Field(
        description="The book, by its name as the server's instructions and error messages "
        "list it; optional when there is only one book."
    ),
]

WRITE_PGN = """Write the games converted so far to a PGN file in pgnify's output folder, with a
blank line between games, and check it as check_pgn does. Give either names, the names of games
saved from this book with build_pgn, in the order to write them; or games, one PGN game per
item. An empty list, an empty game or a name not saved is refused, and nothing is written.

file, optional: the PGN file's name, a plain file name without a folder; the book's name if
left out. A file that this session did not write from this book, such as one there before, is
not replaced unless replace is true.

notes, optional: your account of the conversion, kept beside the PGN file as <name>.notes.md.
Writing the file again without notes deletes the notes written with it before.

Returns file, the PGN file's name; saved, how many games it now holds; replaced, how many it
held before; notes, whether notes were kept; and check_pgn's ok, dropped and games."""


def build_server(books: Path, out: Path, spanned: bool = False) -> MCPServer:
    location, outputs = BooksLocation(books), Outputs(out)
    calls = _Calls(location, outputs)

    def doc_info(book: BookName = None) -> dict[str, Any]:
        return calls.run("doc_info", {}, book, lambda b: doc_tools.doc_info(str(b.path)))

    def doc_text(
        first_page: int, last_page: int | None = None, book: BookName = None
    ) -> dict[str, Any]:
        return calls.run(
            "doc_text",
            {"first_page": first_page, "last_page": last_page},
            book,
            lambda b: doc_tools.doc_text(str(b.path), first_page, last_page, numbered=spanned),
        )

    def doc_search(query: str, book: BookName = None) -> dict[str, Any]:
        return calls.run(
            "doc_search", {"query": query}, book, lambda b: doc_tools.doc_search(str(b.path), query)
        )

    def doc_images(page: int, book: BookName = None) -> dict[str, Any]:
        def extract(b: Book) -> dict[str, Any]:
            result = doc_tools.doc_images(str(b.path), page, str(outputs.book_folder(b).images))
            for image in result["images"]:
                image["path"] = Path(image["path"]).relative_to(outputs.folder).as_posix()
            return result

        return calls.run("doc_images", {"page": page}, book, extract)

    def read_diagram(image_path: str, box: list[float] | None = None) -> dict[str, Any]:
        def read(_: Book | None) -> dict[str, Any]:
            result = diagram_tools.read_diagram(str(outputs.picture(image_path)), box)
            if "reason" in result:
                result["reason"] = calls.safe(result["reason"])
            return result

        arguments = {"image_path": image_path, "box": box}
        return calls.run("read_diagram", arguments, None, read, needs_book=False)

    def validate_moves(start: str, moves: list[str] | str, book: BookName = None) -> dict[str, Any]:
        return calls.run(
            "validate_moves",
            {"start": start, "moves": moves},
            book,
            lambda b: chess_tools.validate_moves(start, moves, calls.figurines(b)),
            needs_book=False,
        )

    def check_fen(fen: str) -> dict[str, Any]:
        return calls.run(
            "check_fen", {"fen": fen}, None, lambda _: chess_tools.check_fen(fen), needs_book=False
        )

    def check_pgn(pgn: str) -> dict[str, Any]:
        return calls.run(
            "check_pgn", {}, None, lambda _: chess_tools.check_pgn(pgn), needs_book=False
        )

    def build_pgn(spec: Any, name: str | None = None, book: BookName = None) -> dict[str, Any]:
        def build(b: Book | None) -> dict[str, Any]:
            if name is not None and b is None:
                raise ValueError(
                    f"name the book this game is from, to save it: {location.listing()}"
                )
            store = str(outputs.book_folder(b).games) if b else None
            figurines = calls.figurines(b)
            if spanned:
                path = str(b.path) if b else None
                return spans.build_pgn(spec, name, store, figurines, book=path)
            return chess_tools.build_pgn(spec, name, store, figurines)

        arguments = {"name": name, "spec": _spec_summary(spec)}
        return calls.run("build_pgn", arguments, book, build, needs_book=False)

    def write_pgn(
        names: list[str] | None = None,
        games: list[str] | None = None,
        file: str | None = None,
        replace: bool = False,
        notes: str | None = None,
        book: BookName = None,
    ) -> dict[str, Any]:
        given = (notes or "").strip()

        def write(b: Book) -> dict[str, Any]:
            target = outputs.target(file, b, replace)
            store = str(outputs.book_folder(b).games)
            result = chess_tools.write_pgn(path=str(target), games=games, names=names, store=store)
            outputs.wrote(target, b, names or len(games or []), given)
            del result["path"]
            return {"file": target.name, **result, "notes": bool(given)}

        arguments = {
            "names": names,
            "games": len(games) if games is not None else None,
            "file": file,
            "replace": replace,
            "notes": len(given),
        }
        return calls.run("write_pgn", arguments, book, write)

    server = MCPServer(
        "pgnify",
        instructions=(
            "pgnify reads chess books and writes PGN. It reads only the books it was given; "
            f"{location.listing()}. It writes only into its output folder, under plain file "
            "names. Its prompt convert gives the conversion instructions, as pgnify's skill "
            "does."
        ),
        version=__version__,
    )
    for tool, description in (
        (validate_moves, chess_tools.validate_moves.__doc__),
        (check_fen, chess_tools.check_fen.__doc__),
        (check_pgn, chess_tools.check_pgn.__doc__),
        (write_pgn, WRITE_PGN),
        (read_diagram, diagram_tools.read_diagram.__doc__),
        (doc_info, doc_tools.doc_info.__doc__),
        (
            doc_text,
            spans.doc_text_description() if spanned else doc_tools.doc_text.__doc__,
        ),
        (doc_search, doc_tools.doc_search.__doc__),
        (doc_images, doc_tools.doc_images.__doc__),
    ):
        server.add_tool(calls.served(tool), description=description)
    spec_type = spans.SpannedGameSpec if spanned else chess_tools.GameSpec
    _add_build_pgn(
        server,
        calls.served(_spec_as_object(build_pgn, spec_type)),
        spans.build_pgn_description() if spanned else chess_tools.build_pgn.__doc__,
    )

    @server.prompt(
        name="convert",
        title="Convert part of a chess book to PGN",
        description="pgnify's conversion instructions, with a request to convert part of a book.",
    )
    def convert(book: str, part: str) -> str:
        return instructions.request(book, part)

    return server


class _Calls:
    """What the server does around each call: choosing its book, logging it, and cleaning the
    server's paths out of its errors."""

    def __init__(self, location: BooksLocation, outputs: Outputs):
        self.location, self.outputs = location, outputs

    def figurines(self, book: Book | None) -> str | None:
        """The file of the pieces the book's figurines were read as; None without a book."""
        return str(self.outputs.book_folder(book).figurines) if book else None

    def run(
        self,
        name: str,
        arguments: dict[str, Any],
        book: str | None,
        call: Callable[[Any], dict[str, Any]],
        needs_book: bool = True,
    ) -> dict[str, Any]:
        """Call call with the book named, else the only book, else None for a tool that can do
        without one, and log the call with its arguments and its result or error."""
        entry: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "tool": name,
            "arguments": _shortened(arguments),
        }
        started = time.monotonic()
        try:
            chosen = None
            if needs_book or book is not None or len(self.location.books) == 1:
                chosen = self.location.get(book)
            if chosen is not None:
                entry["book"] = {"name": chosen.name, "key": chosen.key}
            result = call(chosen)
        except Exception as error:
            entry["error"] = self.safe(_described(error))
            self.outputs.log(entry)
            raise
        entry["result"] = _summary(name, result, chosen)
        entry["seconds"] = round(time.monotonic() - started, 2)
        self.outputs.log(entry)
        return result

    def served(self, tool: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(tool)
        def call(*args: Any, **kwargs: Any) -> Any:
            with _ONE_AT_A_TIME:
                try:
                    return tool(*args, **kwargs)
                except Exception as error:
                    raise ToolError(self.safe(_described(error))) from error

        return call

    def safe(self, message: str) -> str:
        """The message with the server's folders shown as <out> and <books>, and the home
        folder as ~, whether a path is written as it is or escaped as repr and JSON escape it."""
        folders = [(self.outputs.folder, "<out>"), (self.location.root, "<books>")]
        home = Path.home()
        if home != Path(home.anchor):  # a home folder at the root would hide every slash
            folders.append((home, "~"))
        places = {form: name for folder, name in folders for form in _path_forms(str(folder))}
        # Longest first, so that a folder inside another is shown as itself.
        for form in sorted(places, key=len, reverse=True):
            message = message.replace(form, places[form])
        return message


def _described(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _path_forms(path: str) -> set[str]:
    """The ways a message may write path: as it is; as repr writes it alone; as repr writes it
    inside a text holding both kinds of quotes, single-quoted with ' escaped; and as JSON
    writes it."""
    # repr of the path followed by both quotes, less their escaped forms at the end.
    both = repr(path + "'\"")[1:-1][: -len("\\'\"")]
    return {path, repr(path)[1:-1], both, json.dumps(path)[1:-1]}


def _add_build_pgn(server: MCPServer, tool: Callable[..., Any], description: str | None) -> None:
    """Serve the tool as build_pgn, its spec described in place, not by a reference to its
    definition: Ollama's tool format has no $ref, so a spec given only by reference reached the
    model as {}. The definitions the spec's moves refer to stay among the schema's $defs, which
    Ollama keeps. MCPServer.add_tool returns no Tool, so the tool manager's is called for one."""
    schema = server._tool_manager.add_tool(
        tool, name="build_pgn", description=description
    ).parameters
    reference = schema["properties"]["spec"]["$ref"]
    schema["properties"]["spec"] = schema["$defs"].pop(reference.removeprefix("#/$defs/"))


def _spec_as_object(tool: Callable[..., Any], spec_type: Any) -> Callable[..., Any]:
    """The tool with its spec annotated as spec_type alone, read_spec turning JSON text into an
    object before pydantic checks it. Annotated as an object or text, a wrong object's errors
    also said that it should have been text, and a model took that for the fix."""
    signature = inspect.signature(tool)
    spec = Annotated[spec_type, BeforeValidator(chess_tools.read_spec)]
    tool.__signature__ = signature.replace(  # ty: ignore[unresolved-attribute]
        parameters=[
            parameter.replace(annotation=spec) if name == "spec" else parameter
            for name, parameter in signature.parameters.items()
        ]
    )
    return tool


def _spec_summary(spec: dict[str, Any]) -> dict[str, Any]:
    return {"start_fen": spec.get("start_fen"), "moves": len(spec.get("moves", []))}


def _shortened(arguments: dict[str, Any]) -> dict[str, Any]:
    """The arguments as logged: long texts cut short."""
    return {
        name: value[:_LOGGED_TEXT] + "…"
        if isinstance(value, str) and len(value) > _LOGGED_TEXT
        else value
        for name, value in arguments.items()
    }


def _summary(name: str, result: dict[str, Any], book: Book | None) -> dict[str, Any]:
    """The part of a tool's result the log keeps."""
    if name == "doc_text":
        return {"pages": [page["page"] for page in result["pages"]], "next": result["next_page"]}
    if name == "doc_search":
        return {
            "hits": len(result["hits"]),
            "pages": sorted({hit["page"] for hit in result["hits"]}),
        }
    if name == "doc_images":
        assert book is not None, "doc_images reads a book"
        with pymupdf.open(book.path) as doc:
            rect = doc[result["page"] - 1].rect
        size = [round(rect.width, 1), round(rect.height, 1)]
        return {"page": result["page"], "page_size": size, "images": result["images"]}
    if name == "read_diagram":
        return result
    if name == "validate_moves":
        return {"ok": result["ok"], "error": result["error"]}
    if name in ("build_pgn", "write_pgn"):
        keep = ("ok", "saved", "replaced", "file", "notes", "plies", "variations", "comments")
        summary = {key: result[key] for key in keep if key in result}
        summary["errors"] = [str(error)[:_LOGGED_ERROR] for error in result.get("errors", [])]
        return summary
    return {"ok": result["ok"]} if "ok" in result else {}
