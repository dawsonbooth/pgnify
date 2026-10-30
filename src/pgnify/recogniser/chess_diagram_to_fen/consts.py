from pathlib import Path

BBOX_IMAGE_SIZE = 512

DEFAULT_TILE_SIZE = 32

BOARD_PIXEL_WIDTH = DEFAULT_TILE_SIZE * 8
SQUARE_SIZE = DEFAULT_TILE_SIZE

DEFAULT_PGN_DIR = "resources/pychess_games"

# These files are reserved for evaluation and must never be included in training.
RESERVED_PGN_FILES = (
    "pychess_db_2024-01.pgn",
    "pychess_db_2024-02.pgn",
    "pychess_db_2024-03.pgn",
)


def get_training_pgn_files(pgn_dir: str | Path = DEFAULT_PGN_DIR) -> list[Path]:
    """Returns all PGN files in pgn_dir, excluding reserved evaluation files."""
    reserved = set(RESERVED_PGN_FILES)
    return [f for f in sorted(Path(pgn_dir).glob("*.pgn")) if f.name not in reserved]


def get_reserved_pgn_files(pgn_dir: str | Path = DEFAULT_PGN_DIR) -> list[Path]:
    """Returns only the reserved PGN files from pgn_dir."""
    reserved = set(RESERVED_PGN_FILES)
    return [f for f in sorted(Path(pgn_dir).glob("*.pgn")) if f.name in reserved]


def board_pixel_size(game, tile_size=DEFAULT_TILE_SIZE):
    return (game.board_rows * tile_size, game.board_cols * tile_size)
