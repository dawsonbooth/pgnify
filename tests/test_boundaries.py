"""Tests of what the server may read (books.py) and write (outputs.py)."""

import shutil
from pathlib import Path

import pytest
from support import move_specs

from pgnify.books import Book, BooksLocation
from pgnify.chess_tools import build_pgn, read_figurines
from pgnify.outputs import Outputs


@pytest.fixture
def books_folder(primer: Path, two_pages: Path, tmp_path: Path) -> Path:
    """A folder of two books, one in a subfolder, a hidden book and a text file."""
    books = tmp_path / "books"
    (books / "openings").mkdir(parents=True)
    (books / ".hidden").mkdir()
    shutil.copyfile(primer, books / "primer.epub")
    shutil.copyfile(two_pages, books / "openings" / "Two Pages.PDF")
    shutil.copyfile(two_pages, books / ".hidden" / "secret.pdf")
    (books / "notes.txt").write_text("not a book")
    return books


@pytest.fixture
def book(books_folder: Path) -> Book:
    return BooksLocation(books_folder).get("primer.epub")


@pytest.fixture
def outputs(tmp_path: Path) -> Outputs:
    return Outputs(tmp_path / "out")


def test_a_folder_lists_its_visible_pdfs_and_epubs_by_relative_path(books_folder: Path) -> None:
    assert sorted(BooksLocation(books_folder).books) == ["openings/Two Pages.PDF", "primer.epub"]


def test_a_location_that_is_one_book_lists_only_that_book(books_folder: Path) -> None:
    books = BooksLocation(books_folder / "primer.epub")
    assert sorted(books.books) == ["primer.epub"]
    assert books.get(None).name == "primer.epub"
    with pytest.raises(ValueError, match="there is no book"):
        books.get("openings/Two Pages.PDF")


@pytest.mark.parametrize(
    ("link", "target", "is_book"),
    [
        ("same.epub", "books/primer.epub", True),
        ("outside.pdf", "outside.pdf", False),
        ("gone.pdf", "missing.pdf", False),
        ("notes.pdf", "books/notes.txt", False),
        ("visible.pdf", "books/.hidden/secret.pdf", False),
    ],
)
def test_a_link_is_a_book_only_when_it_leads_to_a_visible_book_in_the_folder(
    books_folder: Path, tmp_path: Path, link: str, target: str, is_book: bool
) -> None:
    shutil.copyfile(books_folder / "openings" / "Two Pages.PDF", tmp_path / "outside.pdf")
    (books_folder / link).symlink_to(tmp_path / target)
    assert (link in BooksLocation(books_folder).books) is is_book


def test_a_book_added_since_the_server_started_is_found(books_folder: Path) -> None:
    books = BooksLocation(books_folder)
    shutil.copyfile(books_folder / "primer.epub", books_folder / "later.epub")
    assert books.get("later.epub").name == "later.epub"


@pytest.mark.parametrize(
    ("location", "error", "match"),
    [
        ("nowhere", FileNotFoundError, "there is nothing at"),
        ("empty", ValueError, "no PDF or EPUB"),
        ("notes.txt", ValueError, "not a PDF or an EPUB"),
    ],
)
def test_a_location_with_no_book_is_refused(
    tmp_path: Path, location: str, error: type[Exception], match: str
) -> None:
    (tmp_path / "empty").mkdir()
    (tmp_path / "notes.txt").write_text("")
    with pytest.raises(error, match=match):
        BooksLocation(tmp_path / location)


@pytest.mark.parametrize("name", ["~/primer.epub", "C:\\books\\primer.epub"])
def test_a_book_named_by_a_path_is_refused_without_the_path_repeated(
    books_folder: Path, name: str
) -> None:
    with pytest.raises(ValueError, match="not by a path") as refused:
        BooksLocation(books_folder).get(name)
    assert name not in str(refused.value)


def test_a_book_named_by_its_absolute_path_is_refused_without_the_path_repeated(
    books_folder: Path,
) -> None:
    path = str((books_folder / "primer.epub").resolve())
    with pytest.raises(ValueError, match="not by a path") as refused:
        BooksLocation(books_folder).get(path)
    assert path not in str(refused.value)


def test_two_books_a_copy_and_a_link_each_get_their_own_book_folder(
    books_folder: Path, outputs: Outputs
) -> None:
    (books_folder / "2024").mkdir()
    shutil.copyfile(books_folder / "primer.epub", books_folder / "2024" / "primer.epub")
    (books_folder / "linked.epub").symlink_to(books_folder / "primer.epub")
    books = BooksLocation(books_folder)
    names = ("primer.epub", "openings/Two Pages.PDF", "2024/primer.epub", "linked.epub")
    folders = {outputs.book_folder(books.get(name)).folder for name in names}
    assert len(folders) == len(names)


@pytest.mark.parametrize(
    "name",
    [
        "../up",
        "sub/inner",
        "/abs",
        ".hidden",
        "-dash",
        "",
        "tab\there",
        pytest.param("a" * 93, id="97-characters-with-.pgn"),
    ],
)
def test_an_output_name_that_is_not_a_plain_file_name_is_refused(
    outputs: Outputs, book: Book, name: str
) -> None:
    with pytest.raises(ValueError, match="not a plain file name"):
        outputs.target(name, book, replace=False)


@pytest.mark.parametrize(
    ("name", "file"),
    [("Chapter 5 (forks)", "Chapter 5 (forks).pgn"), ("Chapter 1.PGN", "Chapter 1.pgn")],
)
def test_an_output_name_ends_in_pgn_in_lower_case(
    outputs: Outputs, book: Book, name: str, file: str
) -> None:
    assert outputs.target(name, book, replace=False).name == file


def test_an_output_given_no_name_is_named_after_the_book_with_unsafe_characters_dashed(
    books_folder: Path, outputs: Outputs
) -> None:
    shutil.copyfile(
        books_folder / "primer.epub", books_folder / "openings" / "Tactics: Forks?.EPUB"
    )
    book = BooksLocation(books_folder).get("openings/Tactics: Forks?.EPUB")
    assert outputs.target(None, book, replace=False).name == "Tactics- Forks.pgn"


def test_an_output_that_is_a_link_is_never_written_through(
    outputs: Outputs, book: Book, tmp_path: Path
) -> None:
    (tmp_path / "elsewhere.pgn").write_text("")
    (outputs.folder / "linked.pgn").symlink_to(tmp_path / "elsewhere.pgn")
    with pytest.raises(ValueError, match="not a plain file"):
        outputs.target("linked", book, replace=True)


def test_a_notes_file_pgnify_did_not_write_is_never_written_over(
    outputs: Outputs, book: Book
) -> None:
    (outputs.folder / "forks.notes.md").write_text("My own notes on the forks.\n")
    with pytest.raises(ValueError, match=r"forks\.notes\.md is not pgnify's"):
        outputs.target("forks", book, replace=True)
    assert (outputs.folder / "forks.notes.md").read_text() == "My own notes on the forks.\n"


def test_replacing_a_file_by_its_name_in_another_case_keeps_the_files_name(
    outputs: Outputs, book: Book
) -> None:
    (outputs.folder / "games.pgn").write_text("")
    assert outputs.target("Games", book, replace=True).name == "games.pgn"


def test_a_picture_is_taken_only_by_the_path_doc_images_gave(
    books_folder: Path, outputs: Outputs
) -> None:
    books = BooksLocation(books_folder)
    keys: list[str] = []
    for name in ("primer.epub", "openings/Two Pages.PDF"):
        book = books.get(name)
        images = outputs.book_folder(book).images
        images.mkdir(parents=True)
        (images / "page1-1.png").write_bytes(b"picture")
        keys.append(book.key)
    first, second = keys
    given = f".pgnify/books/{second}/images/page1-1.png"
    assert outputs.picture(given) == outputs.folder / given
    for path in (
        f".pgnify/books/{first}/images/../../{second}/images/page1-1.png",
        str(outputs.folder / given),
        f"./{given}",
    ):
        with pytest.raises(ValueError, match="by the path it gave"):
            outputs.picture(path)


@pytest.mark.parametrize("link", [".pgnify", ".pgnify/log.jsonl"])
def test_an_output_folder_is_refused_when_its_pgnify_folder_is_or_holds_a_link(
    tmp_path: Path, link: str
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out = tmp_path / "out"
    (out / link).parent.mkdir(parents=True)
    (out / link).symlink_to(elsewhere)
    with pytest.raises(ValueError, match="is a link"):
        Outputs(out)
    assert list(elsewhere.iterdir()) == []


def test_a_book_folder_holding_a_link_is_refused(
    outputs: Outputs, book: Book, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    kept = outputs.book_folder(book)
    kept.folder.mkdir(parents=True)
    kept.games.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="is a link"):
        outputs.book_folder(book)
    assert list(elsewhere.iterdir()) == []


def test_figurine_readings_are_not_written_through_a_link_at_their_temporary_name(
    outputs: Outputs, book: Book, tmp_path: Path
) -> None:
    kept = outputs.book_folder(book)
    outside = tmp_path / "outside.json"
    outside.write_text("untouched")
    kept.folder.mkdir(parents=True)
    kept.figurines.with_name("pictures.json.new").symlink_to(outside)
    moves = move_specs("e4", "e5", "[aaaaaa]f3", "[aaaaaa]c6")
    build_pgn({"moves": moves}, name="g", store=str(kept.games), figurines=str(kept.figurines))
    assert outside.read_text() == "untouched"
    assert read_figurines(str(kept.figurines)) == {"aaaaaa": "N"}
