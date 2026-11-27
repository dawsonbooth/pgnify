"""Tests of what the server may read (books.py) and write (outputs.py)."""

import json
import shutil
from pathlib import Path

import pytest
from support import move_specs

from pgnify.books import Book, BooksLocation
from pgnify.chess_tools import build_pgn, read_figurines
from pgnify.doc_tools import doc_page
from pgnify.outputs import NOTES_HEADING, Outputs


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


def _only_pgnify(outputs: Outputs) -> bool:
    """Whether the output folder holds nothing but pgnify's working folder."""
    return [path.name for path in outputs.folder.iterdir()] == [".pgnify"]


@pytest.mark.parametrize(
    "name",
    [
        "a//b",
        "a/",
        "../x",
        "x/../y",
        "./x",
        "a/./b",
        "/abs",
        "a\\b",
        "C:/x",
        "C:\\x",
        ".hidden/x",
        "a/.pgnify/x",
        "a/.hidden",
        "-dash/x",
        pytest.param("/".join(["a" * 50] * 6), id="over-255-characters"),
        pytest.param("a" * 90 + "/" + "b" * 93, id="part-over-96-characters"),
    ],
)
def test_an_output_path_that_is_not_a_relative_path_of_plain_names_is_refused(
    outputs: Outputs, book: Book, name: str
) -> None:
    with pytest.raises(ValueError, match="not a plain file name or a path of folders"):
        outputs.target(name, book, replace=False)
    assert _only_pgnify(outputs)


def test_an_output_path_of_255_characters_is_allowed(outputs: Outputs, book: Book) -> None:
    name = "/".join(["a" * 50] * 4 + ["b" * 47])
    assert len(name) + len(".pgn") == 255
    assert outputs.target(name, book, replace=False) == outputs.folder / f"{name}.pgn"
    with pytest.raises(ValueError, match="not a plain file name or a path of folders"):
        outputs.target(name + "b", book, replace=False)


def test_an_output_in_new_folders_is_planned_without_making_them(
    outputs: Outputs, book: Book
) -> None:
    path = outputs.target("Silman/Part Four/Games.PGN", book, replace=False)
    assert path == outputs.folder / "Silman" / "Part Four" / "Games.pgn"
    assert _only_pgnify(outputs)


def test_an_output_given_no_name_stays_at_the_top_of_the_folder(
    outputs: Outputs, book: Book
) -> None:
    assert outputs.target(None, book, replace=False).parent == outputs.folder


def test_a_folder_there_in_another_case_keeps_its_name(outputs: Outputs, book: Book) -> None:
    (outputs.folder / "Silman" / "Part Four").mkdir(parents=True)
    (outputs.folder / "Silman" / "Part Four" / "Games.pgn").write_text("")
    path = outputs.target("silman/PART FOUR/games", book, replace=True)
    assert path == outputs.folder / "Silman" / "Part Four" / "Games.pgn"
    # Where a folder is missing, the names after it are as given.
    new = outputs.target("SILMAN/part five/Games", book, replace=False)
    assert new == outputs.folder / "Silman" / "part five" / "Games.pgn"


def test_a_folder_there_under_the_exact_name_is_preferred_to_another_case(
    outputs: Outputs, book: Book
) -> None:
    (outputs.folder / "a").mkdir()
    try:
        (outputs.folder / "A").mkdir()
    except FileExistsError:
        pytest.skip("the file system ignores case")
    assert outputs.target("A/x", book, replace=False).parent.name == "A"


@pytest.mark.parametrize("where", ["inside", "outside"])
@pytest.mark.parametrize("depth", ["first", "second"])
def test_a_link_on_the_way_to_an_output_is_never_written_through(
    outputs: Outputs, book: Book, tmp_path: Path, where: str, depth: str
) -> None:
    (outputs.folder / "real").mkdir()
    target = outputs.folder / "real" if where == "inside" else tmp_path / "elsewhere"
    target.mkdir(exist_ok=True)
    planted = outputs.folder / "linked" if depth == "first" else outputs.folder / "real" / "linked"
    planted.symlink_to(target, target_is_directory=True)
    name = "linked/x" if depth == "first" else "real/linked/x"
    with pytest.raises(ValueError, match="is a link, so nothing was written"):
        outputs.target(name, book, replace=True)
    assert sorted(path.name for path in target.iterdir()) == (
        ["linked"] if where == "inside" and depth == "second" else []
    )
    assert not (outputs.folder / "real" / "x.pgn").exists()


def test_a_link_to_nowhere_on_the_way_to_an_output_is_refused(
    outputs: Outputs, book: Book, tmp_path: Path
) -> None:
    (outputs.folder / "gone").symlink_to(tmp_path / "missing", target_is_directory=True)
    with pytest.raises(ValueError, match="is a link, so nothing was written"):
        outputs.target("gone/x", book, replace=False)
    assert not (tmp_path / "missing").exists()


def test_a_file_where_an_output_needs_a_folder_is_refused(outputs: Outputs, book: Book) -> None:
    (outputs.folder / "Silman").write_text("a file")
    (outputs.folder / "real").mkdir()
    (outputs.folder / "real" / "Part").write_text("a file")
    for name in ("Silman/x", "silman/y/z", "real/Part/x"):
        with pytest.raises(ValueError, match="is not a folder, so nothing was written"):
            outputs.target(name, book, replace=True)
    assert sorted(path.name for path in outputs.folder.iterdir()) == [".pgnify", "Silman", "real"]
    assert [path.name for path in (outputs.folder / "real").iterdir()] == ["Part"]


def test_an_output_that_is_a_link_in_a_folder_is_never_written_through(
    outputs: Outputs, book: Book, tmp_path: Path
) -> None:
    (tmp_path / "elsewhere.pgn").write_text("")
    (outputs.folder / "Silman").mkdir()
    (outputs.folder / "Silman" / "linked.pgn").symlink_to(tmp_path / "elsewhere.pgn")
    with pytest.raises(ValueError, match=r"Silman/linked\.pgn is not a plain file"):
        outputs.target("Silman/linked", book, replace=True)


def _written(outputs: Outputs, book: Book, name: str) -> Path:
    """The file at name, as write_pgn would leave it, with its record."""
    path = outputs.target(name, book, replace=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[Result "*"]\n\n1. e4 *\n')
    outputs.wrote(path, book, 1, "")
    return path


def test_a_file_name_in_two_folders_is_two_files(
    books_folder: Path, outputs: Outputs, book: Book
) -> None:
    other = BooksLocation(books_folder).get("openings/Two Pages.PDF")
    first = _written(outputs, book, "Silman/Games")
    second = _written(outputs, other, "Rey/games")
    assert first != second and first.name == "Games.pgn" and second.name == "games.pgn"
    assert sorted(json.loads((outputs.work / "outputs.json").read_text())) == [
        "Rey/games.pgn",
        "Silman/Games.pgn",
    ]
    # Neither is the top-level file of the name.
    assert outputs.target("Games", other, replace=False) == outputs.folder / "Games.pgn"


def test_a_path_written_from_another_book_is_replaced_only_when_asked(
    books_folder: Path, outputs: Outputs, book: Book
) -> None:
    other = BooksLocation(books_folder).get("openings/Two Pages.PDF")
    path = _written(outputs, book, "Silman/Games")
    assert outputs.target("silman/games", book, replace=False) == path
    with pytest.raises(
        ValueError, match=r"Silman/Games\.pgn was written in this session from another book"
    ):
        outputs.target("Silman/Games", other, replace=False)
    assert outputs.target("Silman/Games", other, replace=True) == path


def test_a_path_there_before_the_session_is_replaced_only_when_asked(
    outputs: Outputs, book: Book
) -> None:
    (outputs.folder / "Silman").mkdir()
    (outputs.folder / "Silman" / "Games.pgn").write_text("")
    with pytest.raises(ValueError, match=r"Silman/Games\.pgn was there before this session"):
        outputs.target("Silman/Games", book, replace=False)
    assert outputs.target("Silman/Games", book, replace=True).name == "Games.pgn"


def test_a_notes_file_beside_an_output_in_a_folder_is_pgnifys_to_write_over_only(
    outputs: Outputs, book: Book
) -> None:
    (outputs.folder / "Silman").mkdir()
    (outputs.folder / "Silman" / "forks.notes.md").write_text("My own notes.\n")
    with pytest.raises(ValueError, match=r"Silman/forks\.notes\.md is not pgnify's"):
        outputs.target("Silman/forks", book, replace=True)


def test_a_notes_file_is_kept_beside_its_pgn_file_in_the_folder(
    outputs: Outputs, book: Book
) -> None:
    path = outputs.target("Silman/Games", book, replace=False)
    path.parent.mkdir()
    path.write_text("")
    outputs.wrote(path, book, 1, "What was left out.")
    notes = outputs.folder / "Silman" / "Games.notes.md"
    assert notes.read_text().startswith(NOTES_HEADING)
    assert "when it wrote Silman/Games.pgn from" in notes.read_text()
    assert list(outputs.folder.glob("*.md")) == []


def test_a_game_saved_from_a_book_is_unwritten_until_a_file_in_a_folder_holds_it(
    outputs: Outputs, book: Book
) -> None:
    games = outputs.book_folder(book).games
    games.mkdir(parents=True)
    build_pgn({"moves": move_specs("e4")}, name="a", store=str(games))
    assert outputs.unwritten(book) == ["a"]
    path = outputs.target("Silman/Games", book, replace=False)
    path.parent.mkdir()
    path.write_text((games / "a.pgn").read_text())
    outputs.wrote(path, book, ["a"], "")
    assert outputs.unwritten(book) == []


def test_unwritten_lists_a_game_built_in_parts_by_its_name_alone(
    outputs: Outputs, book: Book
) -> None:
    games = outputs.book_folder(book).games
    games.mkdir(parents=True)
    build_pgn({"moves": move_specs("e4")}, name="a", store=str(games))
    build_pgn({"moves": move_specs("e5")}, name="a", store=str(games), after="1w")
    assert sorted(path.name for path in games.iterdir()) == ["a.pgn", "a.spec.json"]
    assert outputs.unwritten(book) == ["a"]
    path = outputs.target("Silman/Games", book, replace=False)
    path.parent.mkdir()
    path.write_text((games / "a.pgn").read_text())
    outputs.wrote(path, book, ["a"], "")
    assert outputs.unwritten(book) == []


def test_a_link_where_a_games_record_belongs_is_refused(
    outputs: Outputs, book: Book, tmp_path: Path
) -> None:
    games = outputs.book_folder(book).games
    games.mkdir(parents=True)
    build_pgn({"moves": move_specs("e4")}, name="a", store=str(games))
    (games / "a.spec.json").unlink()
    (games / "a.spec.json").symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(ValueError, match=r"a\.spec\.json in pgnify's output folder is a link"):
        outputs.book_folder(book)


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


def test_a_page_doc_page_showed_is_taken_only_by_the_path_it_gave(
    books_folder: Path, outputs: Outputs
) -> None:
    books = BooksLocation(books_folder)
    shown = {}
    for name in ("primer.epub", "openings/Two Pages.PDF"):
        book = books.get(name)
        picture = doc_page(str(book.path), 1, str(outputs.book_folder(book).images))["path"]
        shown[name] = Path(picture).relative_to(outputs.folder).as_posix()
    for given in shown.values():
        assert outputs.picture(given) == outputs.folder / given
    first, second = shown.values()
    # A copy of a shown page where saved games go: refused for where it is, not for being missing.
    elsewhere = second.replace("/images/", "/games/")
    (outputs.folder / elsewhere).parent.mkdir(parents=True, exist_ok=True)
    (outputs.folder / elsewhere).write_bytes((outputs.folder / second).read_bytes())
    climbing = first.replace(
        "/images/", "/images/../../" + Path(second).parts[2] + "/images/"
    ).replace(Path(first).name, Path(second).name)
    for path in (str(outputs.folder / second), f"./{second}", elsewhere, climbing):
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
