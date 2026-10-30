"""Reads chess diagrams with Chess_diagram_to_FEN's models, for read_diagram.

Chess_diagram_to_FEN (github.com/tsoj/Chess_diagram_to_FEN, MIT) reads a picture in five
steps, each with a model of its own: whether the picture holds a board; the board's four
corners, from which it warps the board to a square; which of four quarter turns the warped
board is in; the piece on each of its 64 squares; and whether the board is printed from Black's
side. This calls upstream's get_fen, from the copy of its reading code in chess_diagram_to_fen/,
with its release 1.0 weights, and also returns what get_fen leaves out: how sure the position
model is of each square and its second choice, where the board is in the picture, and whether
the picture shows the board from Black's side.

It reads one request a line on stdin, {"picture": PATH}, or {"picture": PATH, "box": [x0, y0,
x1, y1]} to read only that part of the picture, in its pixels, cut to its edges. For each, in
order, it writes one line of JSON to stdout: the placement, from White's side; the board's box
in the whole picture; whether the board is printed from Black's side; and for each square from
a1 to h8, the piece it holds in FEN's letters ("" for none), how sure the model is, and its
second choice. A picture in which it reads no board gives {"failure": REASON}.

    python -m pgnify.recogniser --weights DIR

with pgnify's diagrams extra installed, DIR holding the weights `pgnify setup` installs.
"""

import argparse
import json
import os
import sys
from pathlib import Path

LETTERS = ("P", "N", "B", "R", "Q", "K", "p", "n", "b", "r", "q", "k", "")
"""The position model's 13 readings of a square, as upstream's chess spec orders them,
with an empty square last."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--weights",
        required=True,
        help="The folder holding the weights `pgnify setup` installs.",
    )
    arguments = parser.parse_args()
    # Only readings go to stdout. Whatever the libraries print, from Python or from C,
    # goes to stderr.
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    from PIL import Image

    from pgnify.recogniser.chess_diagram_to_fen.reading import Models

    models = Models.load(Path(arguments.weights))
    for line in sys.stdin:
        request = json.loads(line)
        try:
            picture = Image.open(request["picture"])
            picture.load()
        except OSError as error:  # such as a file that is not a picture PIL reads
            # PIL's message names the file, and the reason reaches the model.
            reading = {"failure": f"the picture could not be read: {type(error).__name__}"}
        else:
            with picture:
                reading = _read_request(picture, request.get("box"), models)
        out.write(json.dumps(reading) + "\n")
        out.flush()


def _read_request(picture, box: list | None, models) -> dict:
    """The reading of the picture, or of the part of it box covers."""
    if box is None:
        return read(picture, models)
    try:
        part = _part(box, picture.size)
    except ValueError as error:
        return {"failure": str(error)}
    with picture.crop(part) as cropped:
        reading = read(cropped, models)
    if "board" in reading:
        # The board's box in the whole picture's pixels, not the part's.
        x, y = part[0], part[1]
        x0, y0, x1, y1 = reading["board"]
        reading["board"] = [x0 + x, y0 + y, x1 + x, y1 + y]
    return reading


def _part(box: list, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """The part of a picture of that size that box, [x0, y0, x1, y1], covers, cut to the
    picture's edges, or ValueError saying why it covers none."""
    x0, y0, x1, y1 = box
    width, height = size
    if x0 >= x1 or y0 >= y1:
        raise ValueError(f"the box {box} is empty: x0 must be less than x1, and y0 less than y1")
    part = (max(0, x0), max(0, y0), min(width, x1), min(height, y1))
    if part[0] >= part[2] or part[1] >= part[3]:
        raise ValueError(
            f"the box {box} lies outside the picture, which is {width} by {height} pixels"
        )
    return part


def read(picture, models) -> dict:
    from pgnify.recogniser.chess_diagram_to_fen.reading import get_fen

    result = get_fen(picture, models, auto_rotate_image=True, auto_rotate_board=True)
    if result is None:
        return {"failure": "no board found"}
    if result.corners_px is None:
        return {"failure": "the board's corners were not found"}
    if result.fen is None:
        return {"failure": "no piece read on the board"}
    turned = result.board_is_flipped
    squares = []
    for square in range(64):
        rank, file = divmod(square, 8)
        # The square's row and column in the board as read, from its top left; get_fen
        # turned round the reading of a board printed from Black's side.
        row, column = (rank, 7 - file) if turned else (7 - rank, file)
        squares.append(_square(result.scores[row * 8 + column]))
    placement = _placement(squares)
    assert placement == result.fen.split()[0], f"the scores read {placement}, get_fen {result.fen}"
    board, flipped = _board(result, turned)
    return {"placement": placement, "squares": squares, "board": board, "flipped": flipped}


def _square(raw) -> list:
    """What a square holds, how sure the model is, and its second choice.

    The position model was trained with squared error against one-hot targets, so its
    13 scores estimate the chances of each reading without being made to sum to one,
    and get_fen clamps them to [0, 1] and takes the highest. The confidence is the
    chosen reading's clamped score over the clamped scores' sum, or over one if they
    sum to less: scores that sum to more than one are scaled to chances, and a
    shortfall is left as doubt rather than shared out. The second choice is the next
    highest score, compared before clamping, so that readings clamped alike still rank.
    """
    clamped = raw.clamp(0, 1)
    choice = int(clamped.argmax())
    others = raw.clone()
    others[choice] = float("-inf")
    second = int(others.argmax())
    confidence = float(clamped[choice]) / max(float(clamped.sum()), 1.0)
    return [LETTERS[choice], round(confidence, 4), LETTERS[second]]


def _placement(squares: list[list]) -> str:
    ranks = []
    for rank in range(7, -1, -1):
        row, empty = "", 0
        for file in range(8):
            piece = squares[rank * 8 + file][0]
            if piece:
                row += (str(empty) if empty else "") + piece
                empty = 0
            else:
                empty += 1
        ranks.append(row + (str(empty) if empty else ""))
    return "/".join(ranks)


def _board(result, turned: bool):
    """The board's box in the picture, and whether the board is printed from Black's
    side: whether its a8 corner is below the board's middle.

    The warp sends the four corners it is given to the warped board's top left, top
    right, bottom right and bottom left. get_fen turns the warped board clockwise by
    the quarter turns its rotation model finds, reads it, and turns the reading round,
    a1 for h8, if the board is printed from Black's side. (Upstream's corner sort hands the
    warp the bottom left corner first, so an upright board is warped a quarter turn
    clockwise and the rotation model finds three quarter turns more.)
    """
    corners = result.corners_px.tolist()
    xs, ys = [x for x, _ in corners], [y for _, y in corners]
    turns = result.image_rotation_angle
    # The picture's corner that a8 is at: the upright board's top left, or its bottom
    # right where get_fen turned the reading round.
    a8 = corners[(2 - turns) % 4] if turned else corners[-turns % 4]
    # A board a quarter turn round in the picture has a8 at its top right or bottom
    # left, which `flipped` cannot say; it is counted from Black's side if a8 is below.
    flipped = a8[1] > sum(ys) / 4
    return [min(xs), min(ys), max(xs), max(ys)], flipped


if __name__ == "__main__":
    main()
