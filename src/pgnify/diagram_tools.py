"""read_diagram, on Chess_diagram_to_FEN's models: where the pieces stand in a picture of a chess
diagram. It needs pgnify's diagrams extra and the weights `pgnify setup` installs, and runs the
reader in a separate process, `python -m pgnify.recogniser`, so that PyTorch's memory and any
crash stay out of the server. Without the extra or the weights, read_diagram says which is
missing and how to install it."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import chess

from pgnify import weights
from pgnify.chess_tools import status_names

# What the diagrams extra installs that the reader imports.
NEEDED = ("torch", "torchvision", "cv2", "skimage", "PIL", "numpy")
# A square read with less confidence than this is reported as a doubt. On clean diagrams every
# square reads above 0.99; on a scan's dark squares, as low as 0.3.
DOUBT_BELOW = 0.9
# Seconds a reading may take: the reader loads its models from disk at every call, which takes
# longest on the first.
TIMEOUT = 600


def read_diagram(image_path: str, box: list[float] | None = None) -> dict[str, Any]:
    """Read where the pieces stand in a picture of a chess diagram that doc_images extracted,
    given by the path it returned. The diagram reader finds the board and reads each square; the
    book's text gives whose move it is, castling and en passant. A picture may hold more than one
    diagram, side by side or one above another, as scanned books may print them; the reader
    reads one board, so give box to read one diagram of such a picture.

    box, optional: the part of the picture to read, as [x0, y0, x1, y1] in the picture's pixels,
    the units of doc_images' width and height. A box past the picture's edges is cut to them,
    and the board is looked for within it.

    Returns ok; placement, the first field of a FEN, from White's side even when the picture
    shows the board from Black's; flipped, whether it does; board, where the board was found, as
    [x0, y0, x1, y1] in the picture's pixels, with or without a box; doubts, each square read
    with a confidence below 0.9, with the piece read there ("" for none), the confidence, from 0
    to 1, and the second choice; and problems, why the placement cannot be a legal position with
    either side to move, such as a missing king or a pawn on the first or last rank, empty when
    there is none. When it reads no board, ok is false and reason says why.
    """
    if absent := [module for module in NEEDED if importlib.util.find_spec(module) is None]:
        raise RuntimeError(
            f"the diagram reader is not installed ({', '.join(absent)} missing): install pgnify "
            "with its diagrams extra, pgnify[diagrams], then run pgnify setup"
        )
    if weights.missing():
        raise RuntimeError("the diagram reader's weights are not installed: run pgnify setup")
    request: dict[str, Any] = {"picture": str(Path(image_path).resolve())}
    if box is not None:
        if len(box) != 4:
            raise ValueError(f"box must be [x0, y0, x1, y1], not {box}")
        request["box"] = [round(value) for value in box]
    process = subprocess.run(
        [sys.executable, "-m", "pgnify.recogniser", "--weights", str(weights.folder())],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    if process.returncode != 0:
        last = process.stderr.strip().splitlines()[-1:] or ["no message"]
        raise RuntimeError(f"the diagram reader failed: {last[0]}")
    reading = json.loads(process.stdout)
    if "failure" in reading:
        return {"ok": False, "reason": reading["failure"]}
    return {
        "ok": True,
        "placement": reading["placement"],
        "flipped": reading["flipped"],
        "board": [round(value) for value in reading["board"]],
        "doubts": [
            {
                "square": chess.square_name(index),
                "piece": piece,
                "confidence": confidence,
                "second": second,
            }
            for index, (piece, confidence, second) in enumerate(reading["squares"])
            if confidence < DOUBT_BELOW
        ],
        "problems": problems(reading["placement"]),
    }


def problems(placement: str) -> list[str]:
    """Why the placement is not a legal position with either side to move, as python-chess
    names the faults: a king missing or too many, pawns on the first or last rank, too many
    pieces, checks no move could give. Empty when it is legal with one side to move or the
    other, since a diagram's text says whose move it is."""
    statuses = [chess.Board(f"{placement} {turn} - - 0 1").status() for turn in "wb"]
    if chess.STATUS_VALID in statuses:
        return []
    return status_names(statuses[0] | statuses[1])
