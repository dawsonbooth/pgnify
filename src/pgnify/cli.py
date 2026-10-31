"""pgnify's command line."""

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from pgnify import __version__

app = typer.Typer(
    help="Convert chess books into faithful PGN with your own AI assistant, through MCP.",
    no_args_is_help=True,
    add_completion=False,
    # Help text as plain paragraphs, rewrapped to the terminal's width.
    rich_markup_mode=None,
)
# What the commands report on stderr and exit 2 for: files that are missing or that no reader
# opens, including PyMuPDF's errors.
_FAILURES = (OSError, ValueError, RuntimeError)


def _version(requested: bool) -> None:
    if requested:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def _options(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version, is_eager=True, help="Print the version and exit."
        ),
    ] = False,
) -> None:
    pass


@app.command()
def serve(
    books: Annotated[
        Path,
        typer.Option(
            help="A book, or a folder of books (PDF and EPUB). The server reads nothing else."
        ),
    ],
    out: Annotated[
        Path,
        typer.Option(help="The folder for the PGN files. The server writes nothing elsewhere."),
    ],
    spans: Annotated[
        bool,
        typer.Option(help="Experimental: number doc_text's lines, and let build_pgn copy them."),
    ] = False,
) -> None:
    """Run the MCP server on stdin and stdout.

    An AI assistant's client starts it as a stdio server."""
    from pgnify.server import build_server

    try:
        server = build_server(books, out, spanned=spans)
    except _FAILURES as error:
        _fail("serve", error)
    server.run("stdio")


@app.command()
def check(
    pgn: Annotated[Path, typer.Argument(help="The PGN file to check.")],
    book: Annotated[
        Path | None,
        typer.Option(help="The book the PGN came from; its comments are checked against it."),
    ] = None,
) -> None:
    """Check a PGN file without a model.

    Lists verified problems, representation limitations, then possible issues for a person to
    judge, and exits 1 when there is a verified problem."""
    from pgnify import report

    try:
        checked = report.check_file(pgn, book)
    except _FAILURES as error:
        _fail("check", error)
    typer.echo(report.as_text(checked))
    if any(finding["kind"] == "problem" for finding in checked["findings"]):
        raise typer.Exit(1)


@app.command(name="report")
def report_(
    folder: Annotated[Path, typer.Argument(help="An output folder of pgnify serve.")],
    images: Annotated[
        bool,
        typer.Option(help="Also save each doubtful diagram beside its game's position."),
    ] = False,
    coverage: Annotated[
        str | None,
        typer.Option(
            help="Also list, as possible omissions, the book's text on these pages that no "
            "comment holds: all, or the pages the conversion was asked for, as doc_text numbers "
            "them, such as 46-103 or 175-227,876-883."
        ),
    ] = None,
) -> None:
    """Report on a conversion without a model.

    Checks each PGN file in the folder against its book, reports for each book what the
    server's log shows across its PGN files, and quotes the model's account separately. Writes
    report.md and report.json into the folder, and exits 1 when there is a verified problem."""
    from pgnify import report

    try:
        reported = report.report(folder, images=images, coverage=coverage)
    except _FAILURES as error:
        _fail("report", error)
    typer.echo(f"Wrote {folder / 'report.md'} and {folder / 'report.json'}.")
    problems = 0
    for output in reported["outputs"]:
        kinds = [finding["kind"] for finding in output["findings"]]
        problems += kinds.count("problem")
        limitations = kinds.count("limitation")
        limited = f"{report.counted(limitations, 'representation limitation')}, "
        typer.echo(
            f"{output['file']}: {report.counted(len(output['games']), 'game')}, "
            f"{report.counted(kinds.count('problem'), 'verified problem')}, "
            f"{limited if limitations else ''}"
            f"{report.counted(kinds.count('issue'), 'possible issue')}"
        )
    for book in reported["books"]:
        typer.echo(
            f"{book['book']}: {report.counted(len(book['findings']), 'possible issue')} in the "
            f"server's log, across {report.counted(len(book['files']), 'PGN file')}"
        )
        if book.get("coverage", {}).get("book_found"):
            omissions = book["coverage"]["omissions"]
            typer.echo(
                f"{book['book']}: {report.counted(len(omissions), 'possible omission')}, "
                f"{sum(omission['words'] for omission in omissions):,} words"
            )
    if reported["findings"]:
        typer.echo(f"{report.counted(len(reported['findings']), 'tool error')} outside these files")
    typer.echo(report.CAVEAT)
    if problems:
        raise typer.Exit(1)


@app.command()
def setup(
    source: Annotated[
        Path | None,
        typer.Option(
            "--from", help="Copy the weights from this folder instead of downloading them."
        ),
    ] = None,
) -> None:
    """Install the diagram reader's weights.

    Downloads them from Chess_diagram_to_FEN's release, or copies them from a folder, and checks
    each against the SHA-256 pinned in pgnify."""
    from pgnify import weights

    try:
        weights.install(source, say=typer.echo)
    except _FAILURES as error:
        _fail("setup", error)


@app.command()
def instructions() -> None:
    """Print the conversion instructions.

    For an assistant that takes neither MCP prompts nor skills."""
    from pgnify.instructions import text

    typer.echo(text(), nl=False)


def _fail(command: str, error: Exception) -> NoReturn:
    typer.echo(f"pgnify {command}: {error}", err=True)
    raise typer.Exit(2) from error
