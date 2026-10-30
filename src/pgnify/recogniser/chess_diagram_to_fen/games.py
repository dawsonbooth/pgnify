from dataclasses import dataclass


@dataclass(frozen=True)
class GameSpec:
    key: str
    board_rows: int
    board_cols: int
    piece_symbols: tuple[str, ...]
    color_pairs: tuple[tuple[str, str], ...]
    # Piece color distinguishes sides (e.g. white/black in chess, red/black in xiangqi).
    # When True, inverting the board image requires also flipping piece colors in the position.
    # When False (e.g. shogi), pieces are distinguished by orientation rather than color,
    # so image inversion is a valid standalone augmentation.
    color_encodes_piece_side: bool = True
    # In some games/styles, opposite sides can use different glyphs/shapes for the same
    # nominal piece type (e.g. distinct ideographs). For those games, image inversion can
    # corrupt labels because color-flipping the target does not change the glyph itself.
    sides_may_use_distinct_piece_glyphs: bool = False
    # In real games, opponent pieces may appear physically rotated 180° on the board
    # (e.g. xiangqi discs placed upside-down). When True, each piece is randomly rotated
    # 180° during image generation as a data augmentation.
    opponent_pieces_may_be_rotated: bool = False

    @property
    def num_squares(self) -> int:
        return self.board_rows * self.board_cols

    @property
    def piece_set(self) -> set[str]:
        return set(self.piece_symbols)

    @property
    def color_swap_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for a, b in self.color_pairs:
            mapping[a] = b
            mapping[b] = a
        return mapping


CHESS = GameSpec(
    key="chess",
    board_rows=8,
    board_cols=8,
    piece_symbols=("P", "N", "B", "R", "Q", "K", "p", "n", "b", "r", "q", "k"),
    color_pairs=(
        ("P", "p"),
        ("N", "n"),
        ("B", "b"),
        ("R", "r"),
        ("Q", "q"),
        ("K", "k"),
    ),
)


XIANGQI = GameSpec(
    key="xiangqi",
    board_rows=10,
    board_cols=9,
    piece_symbols=(
        "R",
        "N",
        "B",
        "A",
        "K",
        "C",
        "P",
        "r",
        "n",
        "b",
        "a",
        "k",
        "c",
        "p",
    ),
    color_pairs=(
        ("R", "r"),
        ("N", "n"),
        ("B", "b"),
        ("A", "a"),
        ("K", "k"),
        ("C", "c"),
        ("P", "p"),
    ),
    sides_may_use_distinct_piece_glyphs=True,
    opponent_pieces_may_be_rotated=True,
)


SHOGI = GameSpec(
    key="shogi",
    board_rows=9,
    board_cols=9,
    piece_symbols=(
        "K",
        "R",
        "B",
        "G",
        "S",
        "N",
        "L",
        "P",
        "+R",
        "+B",
        "+S",
        "+N",
        "+L",
        "+P",
        "k",
        "r",
        "b",
        "g",
        "s",
        "n",
        "l",
        "p",
        "+r",
        "+b",
        "+s",
        "+n",
        "+l",
        "+p",
    ),
    color_pairs=(
        ("K", "k"),
        ("R", "r"),
        ("B", "b"),
        ("G", "g"),
        ("S", "s"),
        ("N", "n"),
        ("L", "l"),
        ("P", "p"),
        ("+R", "+r"),
        ("+B", "+b"),
        ("+S", "+s"),
        ("+N", "+n"),
        ("+L", "+l"),
        ("+P", "+p"),
    ),
    color_encodes_piece_side=False,
)


GAMES: dict[str, GameSpec] = {
    CHESS.key: CHESS,
    XIANGQI.key: XIANGQI,
    SHOGI.key: SHOGI,
}


def get_game(game: str | GameSpec) -> GameSpec:
    if isinstance(game, GameSpec):
        return game

    key = game.strip().lower()
    if key in {"chinese_chess", "chinese-chess", "xq"}:
        key = "xiangqi"

    if key not in GAMES:
        raise ValueError(
            f"Unsupported game: {game}. Supported games: {list(GAMES.keys())}"
        )
    return GAMES[key]
