"""The MCP server: pgnify's tools over stdio, for the books and the output folder that
`pgnify serve --books PATH --out DIR` names, or, where either is left out, for those the person
allows when the assistant first names them.

Each tool is a plain function, served with its docstring as its description. With --books and --out
the server supplies every file path itself, and the model names books and files within those
folders: the document tools read only the books (books.py), and the output tools write only the
output folder (outputs.py). Without them the model gives full paths, and the person allows each
through the client (permission.py).
The model names no path but a picture's, as doc_images and doc_page return it and read_diagram
takes it.

The server runs calls one at a time, since PyMuPDF is not thread-safe and a model may send
several at once. It passes a tool's failure to the model with its reason, the server's folders
shown as <books> and <out>, and logs every call in its working folder, for `pgnify report`.
build_pgn copies the passages of the book that a comment names (copies.py)."""

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
from mcp.server.elicitation import AcceptedElicitation
from mcp.server.mcpserver import Context, Elicit, ElicitationResult, Image, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from pydantic import BeforeValidator, Field

from pgnify import __version__, chess_tools, copies, diagram_tools, doc_tools, instructions
from pgnify.books import AskedBooks, Book, BooksLocation
from pgnify.outputs import Outputs, own_folder
from pgnify.permission import CANNOT_ASK, Allow, CannotAsk, can_ask

_ONE_AT_A_TIME = threading.Lock()
# The longest argument text, and the longest error, the log keeps whole.
_LOGGED_TEXT = 200
_LOGGED_ERROR = 300

FENCED_BOOK = (
    "The book, by its name as the server's instructions and error messages list it; optional "
    "when there is only one book."
)
ASKED_BOOK = (
    "The book, by its full path to a PDF or EPUB, absolute or starting with ~. The person is "
    "asked to allow it the first time; optional when exactly one book has been allowed."
)

_WRITE_PGN = """Write the games converted so far to a PGN file {where}, with a
blank line between games, and check it as check_pgn does. Give either names, the names of games
saved from this book with build_pgn, in the order to write them; or games, one PGN game per
item. An empty list, an empty game or a name not saved is refused, and nothing is written.

file, {need}: {what} A file that this session did not write from this book, such as one
there before, is not replaced unless replace is true.{asks}

notes, optional: your account of the conversion, kept beside the PGN file as <name>.notes.md.
Writing the file again without notes deletes the notes written with it before.

Returns file, the PGN file's path{path}; saved, how many games it now holds; replaced, how many
it held before; notes, whether notes were kept; unwritten, the names of the games saved from this
book that no file written from it holds; and check_pgn's ok, dropped and games."""

FENCED_WRITE_PGN = _WRITE_PGN.format(
    where="in pgnify's output folder",
    need="optional",
    asks="",
    what="""where to save the PGN file in the output folder: a file name, or folders and a
file name, separated by /, such as Silman/Part Four. Missing folders are made. Each name has
letters, digits, spaces and . _ - ( ) ', starting with a letter or digit. If left out, the file
takes the book's name, at the top of the folder.""",
    path=" in the output folder",
)
ASKED_WRITE_PGN = _WRITE_PGN.format(
    where="in a folder the person allows",
    need="needed without an output folder",
    asks=""" With replace true, the person is also asked before a file this session did not write
from this book is replaced.""",
    what="""the PGN file's full path, absolute or starting with ~, such as
~/Chess/Silman/Part Four.pgn; .pgn is added if left out. The person is asked to allow its folder
the first time, and pgnify makes the folder if it is missing. The file name has letters,
digits, spaces and . _ - ( ) ', starting with a letter or digit; folder names may hold any
character except control and formatting characters.""",
    path="",
)


def build_server(books: Path | None, out: Path | None, state: Path | None = None) -> MCPServer:
    """The server for the books at books, or those the person allows when it is None, and the
    output folder out, or the folders the person allows. Without out it keeps its records in
    state, pgnify's own folder by default."""
    location = BooksLocation(books) if books is not None else AskedBooks()
    outputs = Outputs(out, state or own_folder())
    calls = _Calls(location, outputs)
    book_type = Annotated[
        str | None, Field(description=FENCED_BOOK if books is not None else ASKED_BOOK)
    ]

    def ask_to_read(book: str | None, ctx: Context) -> Allow | Elicit[Allow] | CannotAsk | None:
        return _question(location.question(book), ctx)

    def ask_to_read_a_page(
        book: str | None, page: int | None, ctx: Context
    ) -> Allow | Elicit[Allow] | CannotAsk | None:
        # A picture is read without its book.
        return ask_to_read(book, ctx) if page is not None else None

    def ask_to_write(file: str | None, ctx: Context) -> Allow | Elicit[Allow] | CannotAsk | None:
        return _question(outputs.question(file), ctx)

    # What the person answered about the book, and the folder: for the tool to settle.
    read_type = Annotated[ElicitationResult[Allow], Resolve(ask_to_read)]
    page_read_type = Annotated[ElicitationResult[Allow], Resolve(ask_to_read_a_page)]
    write_type = Annotated[ElicitationResult[Allow], Resolve(ask_to_write)]

    def ask_to_replace(
        file: str | None, book: str | None, replace: bool, ctx: Context, folder: write_type
    ) -> Allow | Elicit[Allow] | CannotAsk | None:
        # Asked only once the folder is allowed, before this call or by the answer given in it:
        # the SDK resolves the folder's question first and passes its outcome here.
        data = folder.data if isinstance(folder, AcceptedElicitation) else None
        allowed = isinstance(data, Allow) and data.allow
        question = outputs.replace_question(file, location.book_path(book), replace, allowed)
        return _question(question, ctx)

    replace_type = Annotated[ElicitationResult[Allow], Resolve(ask_to_replace)]

    def doc_info(book: book_type = None, *, readable: read_type) -> dict[str, Any]:
        return calls.run(
            "doc_info", {}, book, lambda b: doc_tools.doc_info(str(b.path)), readable=readable
        )

    def doc_text(
        first_page: int,
        last_page: int | None = None,
        ocr: bool = False,
        book: book_type = None,
        *,
        readable: read_type,
    ) -> dict[str, Any]:
        def extract(b: Book) -> dict[str, Any]:
            images = str(outputs.book_folder(b).images)
            return doc_tools.doc_text(str(b.path), first_page, last_page, ocr=ocr, out_dir=images)

        arguments = {"first_page": first_page, "last_page": last_page, "ocr": ocr}
        return calls.run("doc_text", arguments, book, extract, readable=readable)

    def doc_search(query: str, book: book_type = None, *, readable: read_type) -> dict[str, Any]:
        return calls.run(
            "doc_search",
            {"query": query},
            book,
            lambda b: doc_tools.doc_search(str(b.path), query),
            readable=readable,
        )

    def doc_images(page: int, book: book_type = None, *, readable: read_type) -> dict[str, Any]:
        def extract(b: Book) -> dict[str, Any]:
            result = doc_tools.doc_images(str(b.path), page, str(outputs.book_folder(b).images))
            for image in result["images"]:
                image["path"] = Path(image["path"]).relative_to(outputs.folder).as_posix()
            return result

        return calls.run("doc_images", {"page": page}, book, extract, readable=readable)

    def doc_page(
        page: int, box: list[float] | None = None, book: book_type = None, *, readable: read_type
    ) -> CallToolResult:
        def show(b: Book) -> dict[str, Any]:
            images = str(outputs.book_folder(b).images)
            result = doc_tools.doc_page(str(b.path), page, images, box)
            result["path"] = Path(result["path"]).relative_to(outputs.folder).as_posix()
            return result

        result = calls.run("doc_page", {"page": page, "box": box}, book, show, readable=readable)
        picture = Image(path=outputs.folder / result["path"]).to_image_content()
        text = TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))
        # Content blocks alone: a client may give its model a result's structured content in
        # place of its blocks, and the picture is the point.
        return CallToolResult(content=[text, picture])

    def read_diagram(
        image_path: str | None = None,
        box: list[float] | None = None,
        page: int | None = None,
        book: book_type = None,
        *,
        readable: page_read_type,
    ) -> dict[str, Any]:
        if page is not None:

            def find(b: Book) -> dict[str, Any]:
                if image_path is not None or box is not None:
                    raise ValueError(
                        "give page alone, to find the page's boards, or image_path and box, "
                        "to read a picture"
                    )
                images = str(outputs.book_folder(b).images)
                result = diagram_tools.read_page(str(b.path), page, images)
                result["path"] = Path(result["path"]).relative_to(outputs.folder).as_posix()
                return result

            return calls.run("read_diagram", {"page": page}, book, find, readable=readable)

        def read(_: Book | None) -> dict[str, Any]:
            if image_path is None:
                raise ValueError(
                    "give image_path, a picture doc_images or doc_page returned, or page, a "
                    "page whose boards to find"
                )
            picture = outputs.picture(image_path)
            # pgnify reads a picture extracted in an earlier session only if the person allowed
            # its book in this one.
            if not location.owns(picture.relative_to(outputs.work / "books").parts[0]):
                raise ValueError(
                    "image_path is a picture of a book the person has not allowed in this "
                    "session: call doc_info with the book's full path, so that the person is "
                    "asked, then read the picture again"
                )
            result = diagram_tools.read_diagram(str(picture), box)
            if "reason" in result:
                result["reason"] = calls.safe(result["reason"])
            return result

        arguments = {"image_path": image_path, "box": box}
        return calls.run("read_diagram", arguments, None, read, needs_book=False)

    def validate_moves(
        start: str, moves: list[str] | str, book: book_type = None, *, readable: read_type
    ) -> dict[str, Any]:
        return calls.run(
            "validate_moves",
            {"start": start, "moves": moves},
            book,
            lambda b: chess_tools.validate_moves(start, moves, calls.figurines(b)),
            needs_book=False,
            readable=readable,
        )

    def check_fen(fen: str) -> dict[str, Any]:
        return calls.run(
            "check_fen", {"fen": fen}, None, lambda _: chess_tools.check_fen(fen), needs_book=False
        )

    def check_pgn(pgn: str) -> dict[str, Any]:
        return calls.run(
            "check_pgn", {}, None, lambda _: chess_tools.check_pgn(pgn), needs_book=False
        )

    def build_pgn(
        spec: Any,
        name: str | None = None,
        book: book_type = None,
        pieces: dict[str, str] | None = None,
        *,
        readable: read_type,
    ) -> dict[str, Any]:
        def build(b: Book | None) -> dict[str, Any]:
            if name is not None and b is None:
                raise ValueError(
                    f"name the book this game is from, to save it: {location.listing()}"
                )
            store = str(outputs.book_folder(b).games) if b else None
            figurines = calls.figurines(b)
            path = str(b.path) if b else None
            return copies.build_pgn(spec, name, store, figurines, book=path, pieces=pieces)

        arguments = {"name": name, "spec": _spec_summary(spec), "pieces": pieces}
        return calls.run("build_pgn", arguments, book, build, needs_book=False, readable=readable)

    def write_pgn(
        names: list[str] | None = None,
        games: list[str] | None = None,
        file: str | None = None,
        replace: bool = False,
        notes: str | None = None,
        book: book_type = None,
        *,
        readable: read_type,
        writable: write_type,
        replacing: replace_type,
    ) -> dict[str, Any]:
        given = (notes or "").strip()
        # The answer about the folder is kept even when the book is refused, as the person gave it.
        refusal = outputs.settle(file, writable)
        refusal = refusal or outputs.settle_replace(
            file, location.book_path(book), replace, replacing
        )

        def write(b: Book) -> dict[str, Any]:
            if refusal:
                raise ValueError(refusal)
            target = outputs.target(file, b, replace)
            store = str(outputs.book_folder(b).games)
            unsaved = (
                ""
                if outputs.fenced
                else ". If it was saved in an earlier session, build it again with build_pgn: each "
                "session keeps its own games"
            )
            result = chess_tools.write_pgn(
                path=str(target), games=games, names=names, store=store, unsaved=unsaved
            )
            outputs.wrote(target, b, names or len(games or []), given)
            del result["path"]
            unwritten = outputs.unwritten(b)
            return {
                "file": outputs.named(target),
                **result,
                "notes": bool(given),
                "unwritten": unwritten,
            }

        arguments = {
            "names": names,
            "games": len(games) if games is not None else None,
            "file": file,
            "replace": replace,
            "notes": len(given),
        }
        return calls.run("write_pgn", arguments, book, write, readable=readable)

    reads = (
        f"It reads only the books it was given; {location.listing()}."
        if books is not None
        else "It reads only the books the person allows: name each by its full path, to a PDF or "
        "EPUB, and the person is asked the first time."
    )
    writes = (
        "It writes only into its output folder, in the files and folders write_pgn names."
        if out is not None
        else "It writes PGN files only in the folders the person allows: give write_pgn the "
        "file's full path, and the person is asked the first time for each folder."
    )
    server = MCPServer(
        "pgnify",
        instructions=(
            f"pgnify reads chess books and writes PGN. {reads} {writes} Its prompt convert "
            "gives the conversion instructions, as pgnify's skill does."
        ),
        version=__version__,
    )
    for tool, description in (
        (validate_moves, chess_tools.validate_moves.__doc__),
        (check_fen, chess_tools.check_fen.__doc__),
        (check_pgn, chess_tools.check_pgn.__doc__),
        (write_pgn, FENCED_WRITE_PGN if out is not None else ASKED_WRITE_PGN),
        (read_diagram, diagram_tools.READ_DIAGRAM),
        (doc_info, doc_tools.doc_info.__doc__),
        (doc_text, doc_tools.doc_text.__doc__),
        (doc_search, doc_tools.doc_search.__doc__),
        (doc_images, doc_tools.doc_images.__doc__),
        (doc_page, doc_tools.doc_page.__doc__),
    ):
        server.add_tool(calls.served(tool), description=description)
    _add_build_pgn(
        server,
        calls.served(_spec_as_object(build_pgn, copies.CopyingGameSpec)),
        copies.build_pgn_description(),
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

    def __init__(self, location: BooksLocation | AskedBooks, outputs: Outputs):
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
        readable: Any = None,
    ) -> dict[str, Any]:
        """Call call with the book named, else the only book, else None for a tool that can do
        without one, and log the call with its arguments and its result or error. readable is
        what the person answered about reading the book named, when a question was asked."""
        entry: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "tool": name,
            "arguments": _shortened(arguments),
        }
        started = time.monotonic()
        try:
            chosen = None
            if refusal := self.location.settle(book, readable):
                raise ValueError(refusal)
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
        folder as ~, whether a path is written as it is or escaped as repr and JSON escape it.
        A folder the person allowed is shown in full, with ~ for the home folder."""
        folders: list[tuple[Path, str]] = []
        if self.outputs.fenced:
            folders.append((self.outputs.folder, "<out>"))
        if isinstance(self.location, BooksLocation):
            folders.append((self.location.root, "<books>"))
        home = Path.home()
        if home != Path(home.anchor):  # a home folder at the root would hide every slash
            folders.append((home, "~"))
        places = {form: name for folder, name in folders for form in _path_forms(str(folder))}
        # Longest first, so that a folder inside another is shown as itself.
        for form in sorted(places, key=len, reverse=True):
            message = message.replace(form, places[form])
        return message


def _question(question: str | None, ctx: Context) -> Allow | Elicit[Allow] | CannotAsk | None:
    """What a resolver gives the SDK for a question: the form to ask the person, or, when the
    client has not declared form elicitation, a plain value the tool reports; None when there is
    nothing to ask."""
    if question is None:
        return None
    return Elicit(question, Allow) if can_ask(ctx.client_capabilities) else CANNOT_ASK


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
    if name == "doc_page":
        keep = ("page", "box", "path", "width", "height")
        return {key: result[key] for key in keep}
    if name == "read_diagram" and "boards" in result:
        # No page text: the boards and their readings, without the lines beside them.
        boards = [{k: v for k, v in board.items() if k != "text"} for board in result["boards"]]
        keep = ("page", "path", "width", "height", "unread")
        return {**{key: result[key] for key in keep}, "boards": boards}
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
