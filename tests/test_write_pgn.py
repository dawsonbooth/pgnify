from pathlib import Path

import pytest
from support import ONE_GAME, move_specs

from pgnify.chess_tools import build_pgn, read_games, write_pgn

TWO_GAMES = ['[Result "*"]\n\n1. e4 e5 *', '[Result "*"]\n\n1. d4 d5 *']


def test_write_pgn_saves_the_games_with_a_blank_line_between_them(tmp_path: Path) -> None:
    out = tmp_path / "out.pgn"
    result = write_pgn(path=str(out), games=TWO_GAMES)
    assert out.read_text() == '[Result "*"]\n\n1. e4 e5 *\n\n[Result "*"]\n\n1. d4 d5 *\n'
    assert result["ok"] is True
    assert len(result["games"]) == 2
    assert result["path"] == str(out.resolve())


def test_write_pgn_says_how_many_games_it_saved_and_how_many_it_replaced(tmp_path: Path) -> None:
    out = tmp_path / "out.pgn"
    first = write_pgn(path=str(out), games=TWO_GAMES)
    second = write_pgn(path=str(out), games=[ONE_GAME])
    assert (first["saved"], first["replaced"]) == (2, 0)
    assert (second["saved"], second["replaced"]) == (1, 2)


@pytest.mark.parametrize(("games", "held"), [([ONE_GAME], "1 game"), (TWO_GAMES, "2 games")])
def test_write_pgn_refuses_an_empty_list_and_keeps_what_was_saved(
    tmp_path: Path, games: list[str], held: str
) -> None:
    out = tmp_path / "out.pgn"
    write_pgn(path=str(out), games=games)
    saved = out.read_text()
    with pytest.raises(
        ValueError, match=rf"nothing was written; out\.pgn still holds the {held} saved before"
    ):
        write_pgn(path=str(out), games=[])
    assert out.read_text() == saved


def test_write_pgn_refuses_an_empty_list_before_anything_is_saved(tmp_path: Path) -> None:
    out = tmp_path / "out.pgn"
    with pytest.raises(ValueError, match="no games given, so nothing was written"):
        write_pgn(path=str(out), games=[])
    assert not out.exists()


def test_write_pgn_refuses_an_empty_game(tmp_path: Path) -> None:
    out = tmp_path / "out.pgn"
    with pytest.raises(ValueError, match="game 2 is empty, so nothing was written"):
        write_pgn(path=str(out), games=[ONE_GAME, " \n"])
    assert not out.exists()


def test_write_pgn_writes_saved_games_in_the_order_named(tmp_path: Path) -> None:
    store, out = tmp_path / "games", tmp_path / "out.pgn"
    build_pgn({"moves": move_specs("d4")}, name="b", store=str(store))
    build_pgn({"moves": move_specs("e4")}, name="a", store=str(store))
    result = write_pgn(names=["a", "b"], path=str(out), store=str(store))
    text = out.read_text()
    assert text.index("1. e4") < text.index("1. d4")
    assert (result["saved"], result["ok"]) == (2, True)


def test_a_text_only_game_keeps_its_comment_character_for_character_and_line_by_line(
    tmp_path: Path,
) -> None:
    store, out = tmp_path / "games", tmp_path / "out.pgn"
    # A heading and a list item on lines of their own; a curly apostrophe and quotes, dashes
    # and an ellipsis.
    text = (
        "What Is a Plan?\nIt\u2019s a goal \u2013 \u201csimple\u201d \u2013 and the moves that "
        "reach it\u2026\n\u2022 More space"
    )
    build_pgn({"headers": {"Event": "A Book"}, "comment": text}, name="intro", store=str(store))
    write_pgn(names=["intro"], path=str(out), store=str(store))
    (game,) = read_games(out.read_text(encoding="utf-8"))
    assert game.comment == text
    assert game.next() is None
    assert "FEN" not in game.headers


def test_write_pgn_refuses_a_name_not_saved_and_names_those_that_are(tmp_path: Path) -> None:
    store, out = tmp_path / "games", tmp_path / "out.pgn"
    build_pgn({"moves": move_specs("e4")}, name="a", store=str(store))
    with pytest.raises(ValueError, match=r"no game saved as 'c'; the games saved are: a"):
        write_pgn(names=["a", "c"], path=str(out), store=str(store))
    assert not out.exists()


def test_write_pgn_refuses_both_games_and_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="games or names, not both"):
        write_pgn(
            path=str(tmp_path / "out.pgn"), games=[ONE_GAME], names=["a"], store=str(tmp_path)
        )
