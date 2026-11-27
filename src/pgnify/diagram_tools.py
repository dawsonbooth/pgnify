"""read_diagram, on Chess_diagram_to_FEN's models: where the pieces stand in a picture of a chess
diagram, or on each board found on a page. It needs pgnify's diagrams extra and the weights
`pgnify setup` installs, and runs the reader in a separate process, `python -m
pgnify.recogniser`, so that PyTorch's memory and any crash stay out of the server. Without the
extra or the weights, read_diagram says which is missing and how to install it.

On a page, the search for boards (recogniser/frames.py) proposes places and the reader judges
them: a place in which it reads no board is dropped. What stands beside each board comes from
the page's text layer, or, where the page has none, from Tesseract's reading of the strips
beside the board (tesseract.py)."""

import atexit
import collections
import contextlib
import importlib.util
import io
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, Any

import chess
import pymupdf

from pgnify import doc_tools, tesseract, weights
from pgnify.chess_tools import status_names

# What the diagrams extra installs that the reader imports.
NEEDED = ("torch", "torchvision", "cv2", "skimage", "PIL", "numpy")
# A square read with less confidence than this is reported as a doubt. On clean diagrams every
# square reads above 0.99; on a scan's dark squares, as low as 0.3.
DOUBT_BELOW = 0.9
# Seconds one request's reading may take: the reader loads its models from disk at the first
# request that reads a board, which takes longest, and reads a page's places in about half a
# second each.
TIMEOUT = 600
# Seconds without a request after which the reader's process ends, which frees the memory its
# models hold, about a gigabyte; the next request starts a new one.
IDLE = 300
# How many of the reader's last lines on stderr are kept to say why it failed.
STDERR_LINES = 20
# The widths over heights a board may have, as the search in recogniser/frames.py takes them.
SQUARE = (0.85, 1.18)
# Text within this share of a board's side of it stands beside it.
BESIDE = 1 / 3
# The share of a board's side around it left white with it for Tesseract: its frame.
FRAME = 1 / 32
# Points around a board within which a line of text is part of its diagram, such as the
# coordinates a chess font sets against the board.
TOUCH = 2.0
# The orientation model's score from which its call is doubted: the file letters under the board
# are read, and where they do not decide, the score is reported. On the boards of one book
# printed from White's side its scores ran from 0 to 0.68, a dozen of them to 0.3 or more; the
# boards it turned wrongly, in two books, scored 0.70 to 0.84.
ORIENTATION_DOUBT = 0.3
# How many more of the files' letters under a board must read in one order than in the other to
# overrule the model: noise, such as a caption, reached 2 on 4 of 117 boards without letters;
# boards with printed letters read 3 or more.
LETTERS_DECIDE = 3
# The height of the strip under a board in which its file letters stand, in its widths.
LETTERS_STRIP = 0.12
FILES = "abcdefgh"
SIDES = ("above", "left", "right", "below")

READ_DIAGRAM = """Read where the pieces stand in a chess diagram: in a picture, or on each board
found on a page of the book. The diagram reader finds the board and reads each square; the
book's text gives whose move it is, castling and en passant.

image_path: a picture that doc_images extracted or doc_page showed, by the path it returned. A
picture may hold more than one diagram, side by side or one above another, as a scanned page or
a page doc_page shows may; the reader reads one board, so give box to read one diagram of such a
picture, around the whole board: a board the box cuts is misread.

box, optional with image_path: the part of the picture to read, as [x0, y0, x1, y1] in the
picture's pixels, the units of the width and height doc_images and doc_page give. A box past the
picture's edges is cut to them, and the board is looked for within it.

page, in place of image_path: a page, numbered as doc_text numbers them, whose boards to find
and read. The page is drawn as doc_page draws it and searched for the frames printed boards are
drawn in: a square, two parallel lines a board apart, or a line a board long. The reader reads
each place found, and a place where it reads no board is dropped. A board printed without a
frame, or only in part, is not looked for, so finding none does not show that the page has none.

Returns, for a picture: ok; placement, the first field of a FEN, from White's side even when the
picture shows the board from Black's; flipped, whether it does, as the orientation model says,
checked against the file letters printed under the board where the model is unsure;
orientation_doubt, the model's raw score for Black's side, which it calls above 0.7, when the
score is 0.3 or more and the letters did not settle it, null otherwise; board, where the board
was found, as [x0, y0, x1, y1] in the picture's pixels, with or without a box; doubts, each
square read with a confidence below 0.9, with the piece read there ("" for none), the
confidence, from 0 to 1, and the second choice; and problems, why the placement cannot be a
legal position with either side to move, such as a missing king or a pawn on the first or last
rank, empty when there is none. When it reads no board, ok is false and reason says why.

Returns, for a page: page; its section; path, width and height, the page's picture, from which
image_path and box read a board again; boards, from the top of the page down, left to right
where they stand side by side, each with box, where it is on the page in points, as doc_page
takes a box to show it close up; found_by, the frame the search found it by; the reading, as for
a picture, its board in the page picture's pixels; other_readings, any other placement read for
the same board from another place the search found, a sign of doubt; and text, the lines beside
the board within a third of its side, by side (above, left, right, below) as the page is shown,
each with its gap from the board and its box, in points: a line between two boards is beside
both. text_source says where the text comes from: the page's text layer, or, on a page without
one, Tesseract's reading of the strips beside each board, a machine reading that cuts a line
where a strip ends; or why there is none, such as Tesseract missing. unread lists, in points,
places that look like a board but were not read as one, each with why: a square frame of a
board's size in which the reader read no board, a diagram it cannot read or no diagram; a board
on which it read no piece, such as a scheme of lines on an empty board; and a board on which it
doubted every square, such as a table ruled in squares."""


def missing() -> str | None:
    """What the diagram reader lacks, and how to install it, or None when it is installed."""
    if absent := [module for module in NEEDED if importlib.util.find_spec(module) is None]:
        return (
            f"the diagram reader is not installed ({', '.join(absent)} missing): install pgnify "
            "with its diagrams extra, pgnify[diagrams], then run pgnify setup"
        )
    if weights.missing():
        return "the diagram reader's weights are not installed: run pgnify setup"
    return None


def read_diagram(image_path: str, box: list[float] | None = None) -> dict[str, Any]:
    """The reading of the picture at image_path, or of the part box covers, in its pixels, as
    READ_DIAGRAM says."""
    if reason := missing():
        raise RuntimeError(reason)
    request: dict[str, Any] = {"picture": str(Path(image_path).resolve())}
    if box is not None:
        if len(box) != 4:
            raise ValueError(f"box must be [x0, y0, x1, y1], not {box}")
        request["box"] = [round(value) for value in box]
    [reading] = _run([request])
    if "failure" in reading:
        return {"ok": False, "reason": reading["failure"]}
    return {"ok": True, **_oriented(_reading(reading), Path(image_path))}


def read_page(path: str, number: int, out_dir: str) -> dict[str, Any]:
    """The boards found on page `number` of the book at path, each read, with the text beside
    it, as READ_DIAGRAM says. out_dir is where the page's picture is saved, as doc_page saves
    it."""
    [(shown, boards, unread)] = _found(path, [number], out_dir)
    # other_readings, the placements read from other places, stay as the reader gave them.
    oriented = [
        {**board, "reading": _oriented(board["reading"], Path(shown["path"]))} for board in boards
    ]
    with pymupdf.open(path) as doc:
        page = doc[number - 1]
        source, texts = _texts(page, [board["box"] for board in boards])
    return {
        "page": number,
        "section": shown["section"],
        "path": shown["path"],
        "width": shown["width"],
        "height": shown["height"],
        "boards": [
            {
                "box": _rounded(board["box"]),
                "found_by": board["found_by"],
                **board["reading"],
                "other_readings": board["other_readings"],
                "text": text,
            }
            for board, text in zip(oriented, texts, strict=True)
        ],
        "text_source": source,
        "unread": [{"box": _rounded(place["box"]), "why": place["why"]} for place in unread],
    }


def find_boards(path: str, numbers: list[int], out_dir: str) -> dict[int, list[pymupdf.Rect]]:
    """Where the boards found on each of the pages are, in points, as read_page finds them."""
    return {
        shown["page"]: [board["box"] for board in boards]
        for shown, boards, _ in _found(path, numbers, out_dir)
    }


def settle(found: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The boards among the places the search found, and the places left unread. Places where
    the reader read a board are one board where the boards it found in them overlap by more than
    half their union. Of their readings, the one with the fewest doubtful squares stands for the
    board, a frame's before a single line's, and the other placements read are kept as
    other_readings. Left unread, each with why, where they overlap no board read: square frames
    in which the reader read no board; boards on which it read no piece that are square and fill
    half the place they were read in; and boards on which it doubted every square."""
    read = [place for place in found if _sure(place["reading"])]
    read.sort(key=lambda place: (_doubtful(place["reading"]), place["found_by"] == "one line"))
    boards: list[dict[str, Any]] = []
    for place in read:
        reading = place["reading"]
        same = next(
            (board for board in boards if _iou(reading["board"], board["reading"]["board"]) > 0.5),
            None,
        )
        if same is None:
            boards.append({**place, "other_readings": []})
        elif reading["placement"] not in [same["reading"]["placement"], *same["other_readings"]]:
            same["other_readings"].append(reading["placement"])
    unread: list[dict[str, Any]] = []
    for place in found:
        reading = place["reading"]
        if _sure(reading):
            continue
        if "placement" in reading:
            box, why = reading["board"], "a board on which the reader doubted every square"
        elif "board" in reading:
            if not _fills(reading["board"], place["box"]):
                continue  # the corners of a sliver: no board
            box, why = reading["board"], "a board on which the reader read no piece"
        elif place["found_by"] == "square":
            box, why = place["box"], "a square frame in which the reader read no board"
        else:
            continue
        taken = [board["reading"]["board"] for board in boards] + [u["box"] for u in unread]
        if not any(_iou(box, other) > 0.5 for other in taken):
            unread.append({"box": box, "why": why})
    return _in_reading_order(boards), unread


def problems(placement: str) -> list[str]:
    """Why the placement is not a legal position with either side to move, as python-chess
    names the faults: a king missing or too many, pawns on the first or last rank, too many
    pieces, checks no move could give. Empty when it is legal with one side to move or the
    other, since a diagram's text says whose move it is."""
    statuses = [chess.Board(f"{placement} {turn} - - 0 1").status() for turn in "wb"]
    if chess.STATUS_VALID in statuses:
        return []
    return status_names(statuses[0] | statuses[1])


def _found(
    path: str, numbers: list[int], out_dir: str
) -> list[tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]]:
    """For each page: doc_page's picture of it; its boards, each read, with its box in points;
    and the places left unread, with their boxes in points. One reader process reads every
    page."""
    if reason := missing():
        raise RuntimeError(reason)
    shown = [doc_tools.doc_page(path, number, out_dir) for number in numbers]
    searched = _run([{"picture": str(Path(s["path"]).resolve()), "find": True} for s in shown])
    found = []
    with pymupdf.open(path) as doc:
        for picture, search in zip(shown, searched, strict=True):
            page = doc[picture["page"] - 1]
            # From the picture's pixels to the page's points, unturned.
            back = (
                pymupdf.Matrix(
                    page.rect.width / picture["width"], page.rect.height / picture["height"]
                )
                * page.derotation_matrix
            )
            places = [
                {**place, "reading": _reading(place["reading"])}
                if "placement" in place["reading"]
                else place
                for place in search.get("candidates", [])
            ]
            boards, unread = settle(places)
            on_page = [
                {**board, "box": (pymupdf.Rect(board["reading"]["board"]) * back).normalize()}
                for board in boards
            ]
            left = [{**u, "box": (pymupdf.Rect(u["box"]) * back).normalize()} for u in unread]
            found.append((picture, on_page, left))
    return found


class _Reader:
    """One reader process, with threads that take its answers and its stderr off their pipes, so
    that the wait for an answer can time out and the stderr pipe can never fill."""

    def __init__(self) -> None:
        self.command = _command()
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert self.process.stdin and self.process.stdout and self.process.stderr
        self.stdin = self.process.stdin
        # Each line of stdout, then None at its end.
        self.answers: queue.Queue[str | None] = queue.Queue()
        self.stderr: collections.deque[str] = collections.deque(maxlen=STDERR_LINES)
        self.threads = [
            threading.Thread(target=self._take, args=(self.process.stdout,), daemon=True),
            threading.Thread(target=self._keep, args=(self.process.stderr,), daemon=True),
        ]
        for thread in self.threads:
            thread.start()
        # How many requests it has had, which tells an idle timer whether any came since.
        self.requests = 0

    def _take(self, stream: IO[str]) -> None:
        for line in stream:
            self.answers.put(line)
        self.answers.put(None)

    def _keep(self, stream: IO[str]) -> None:
        for line in stream:
            if line.strip():
                self.stderr.append(line.strip())

    def ask(self, request: dict[str, Any]) -> dict[str, Any]:
        """The answer to one request: RuntimeError if the process ends before it answers, and
        TimeoutExpired if it takes longer than TIMEOUT."""
        self.requests += 1
        try:
            self.stdin.write(json.dumps(request) + "\n")
            self.stdin.flush()
        except OSError:  # it has ended; the wait below says how
            pass
        try:
            line = self.answers.get(timeout=TIMEOUT)
        except queue.Empty:
            raise subprocess.TimeoutExpired(self.command, TIMEOUT) from None
        if line is None:
            self.process.wait()
            self.threads[1].join(timeout=1)  # the last of its stderr
            last = self.stderr[-1] if self.stderr else "no message"
            raise RuntimeError(f"the diagram reader failed: {last}")
        return json.loads(line)

    def stop(self) -> None:
        """Kill the process and close its pipes."""
        self.process.kill()
        self.process.wait()
        for thread in self.threads:
            thread.join(timeout=1)
        for stream in (self.stdin, self.process.stdout, self.process.stderr):
            if stream:
                # A pipe to a process that has ended may fail to flush as it closes.
                with contextlib.suppress(OSError):
                    stream.close()


# The reader that serves every call, started at the first, and the timer that ends it after IDLE
# seconds without a request. _lock keeps concurrent calls from interleaving their requests.
_lock = threading.Lock()
_reader: _Reader | None = None
_idle: threading.Timer | None = None


def _command() -> list[str]:
    """The command that starts the reader."""
    return [sys.executable, "-m", "pgnify.recogniser", "--weights", str(weights.folder())]


def _run(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The reader's answers to the requests, in order, from the one process that serves every
    call. A process that ends, fails or takes longer than TIMEOUT over a request is killed, and
    the next call starts a new one, as does one that ended between calls. An empty list of
    requests starts none."""
    global _reader, _idle
    if not requests:
        return []
    with _lock:
        if _idle:
            _idle.cancel()
        if _reader and _reader.process.poll() is not None:
            _discard()  # it ended while idle: asking it would fail on a broken pipe
        try:
            if _reader is None:
                _reader = _Reader()
            answers = [_reader.ask(request) for request in requests]
        except BaseException:
            _discard()  # its next answer could be one to this call's request
            raise
        _idle = threading.Timer(IDLE, _expire, args=(_reader, _reader.requests))
        _idle.daemon = True
        _idle.start()
        return answers


def _expire(reader: _Reader, requests: int) -> None:
    """End the reader if it has had no request since its count was requests."""
    with _lock:
        if _reader is reader and reader.requests == requests:
            _discard()


def _discard() -> None:
    """End the reader's process, if there is one."""
    global _reader
    reader, _reader = _reader, None
    if reader:
        reader.stop()


atexit.register(_discard)


def _reading(reading: dict[str, Any]) -> dict[str, Any]:
    """A board's reading as read_diagram gives it."""
    return {
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
        "orientation_doubt": (
            reading["orientation"] if reading["orientation"] >= ORIENTATION_DOUBT else None
        ),
    }


def _oriented(reading: dict[str, Any], picture: Path) -> dict[str, Any]:
    """The reading with its orientation checked against the file letters printed under the board,
    where the model's score is 0.3 or more: where they read in an order that disagrees with its
    call by LETTERS_DECIDE or more, either call, to White's side or to Black's, is turned round,
    and where they agree the doubt is cleared. Otherwise the reading stands, with its doubt."""
    if reading["orientation_doubt"] is None:
        return reading
    votes = _letters(picture, reading["board"])
    if votes is None or abs(votes) < LETTERS_DECIDE:
        return reading
    reading = {**reading, "orientation_doubt": None}
    if (votes < 0) == reading["flipped"]:
        return reading
    placement = reading["placement"][::-1]
    return {
        **reading,
        "placement": placement,
        "flipped": not reading["flipped"],
        "doubts": [
            {**doubt, "square": chess.square_name(63 - chess.parse_square(doubt["square"]))}
            for doubt in reading["doubts"]
        ],
        "problems": problems(placement),
    }


def _letters(picture: Path, board: list[int]) -> int | None:
    """How many more of the letters a to h Tesseract reads in order, each under the file it names,
    than in reverse, in the strip under the board, in the picture's pixels; None where Tesseract or
    the picture cannot be read."""
    from PIL import Image, ImageOps

    try:
        with Image.open(picture) as opened:
            grey = opened.convert("L")
        x0, _, x1, y1 = board
        width = x1 - x0
        read = ""
        for file in range(8):
            cell = (
                round(x0 + file * width / 8),
                y1,
                round(x0 + (file + 1) * width / 8),
                min(grey.height, round(y1 + LETTERS_STRIP * width)),
            )
            if cell[2] <= cell[0] or cell[3] <= cell[1] or cell[0] < 0 or cell[2] > grey.width:
                return None
            part = grey.crop(cell).resize((4 * (cell[2] - cell[0]), 4 * (cell[3] - cell[1])))
            # A character alone in white, which Tesseract reads better than one at the edge.
            part = ImageOps.expand(part, border=20, fill=255)
            data = io.BytesIO()
            part.save(data, "PNG")
            read += tesseract.read_character(data.getvalue(), FILES)[:1] or "-"
    except (RuntimeError, OSError):
        return None
    forward = sum(a == b for a, b in zip(read, FILES, strict=True))
    return forward - sum(a == b for a, b in zip(read, FILES[::-1], strict=True))


def _fills(board: list[float], place: list[float]) -> bool:
    """Whether a board the reader found is square and fills half the place it was read in, as a
    printed board does its frame."""
    width, height = board[2] - board[0], board[3] - board[1]
    area = (place[2] - place[0]) * (place[3] - place[1])
    return (
        SQUARE[0] <= width / height <= SQUARE[1] and width * height >= 0.5 * area
        if height
        else False
    )


def _doubtful(reading: dict[str, Any]) -> int:
    return len(reading["doubts"])


def _sure(reading: dict[str, Any]) -> bool:
    """Whether the reader read a board and is sure of at least one of its squares."""
    return "placement" in reading and _doubtful(reading) < len(chess.SQUARES)


def _in_reading_order(boards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The boards from the top of the page down, those whose heights overlap by more than half
    the smaller one's side by side, left to right."""
    rows: list[list[dict[str, Any]]] = []
    for board in sorted(boards, key=lambda b: b["reading"]["board"][1]):
        y0, y1 = board["reading"]["board"][1::2]
        if rows:
            first = rows[-1][0]["reading"]["board"]
            shared = min(y1, first[3]) - max(y0, first[1])
            if shared > 0.5 * min(y1 - y0, first[3] - first[1]):
                rows[-1].append(board)
                continue
        rows.append([board])
    return [board for row in rows for board in sorted(row, key=lambda b: b["reading"]["board"][0])]


def _texts(
    page: pymupdf.Page, boards: list[pymupdf.Rect]
) -> tuple[str, list[dict[str, list[dict[str, Any]]]]]:
    """Where the text beside the boards comes from, and for each board, the lines beside it by
    side: the page's text layer's lines, or Tesseract's reading of the strips beside each board,
    the other boards left white, on a page without one; or, where Tesseract is missing or fails,
    why there are none."""
    shown = page.rotation_matrix
    if page.get_text("text").strip():
        lines = doc_tools.boxed_lines(page)
        return "the page's text layer", [_beside(board, lines, boards, shown) for board in boards]
    if not boards:
        return "none: the page has no text layer, and no board was found", []
    blank = [framed(board) for board in boards]
    texts = []
    try:
        engine = tesseract.version()
        for board in boards:
            lines = []
            for strip in _strips(board, page.rect * page.derotation_matrix):
                read = tesseract.read(page, strip, blank, tesseract.BLOCK)
                lines += [(line.box, line.text) for line in read]
            texts.append(_beside(board, lines, boards, shown))
    except RuntimeError as error:
        return f"none: the page has no text layer, and {error}", [{} for _ in boards]
    return f"{engine}'s reading of the strips beside each board, a machine reading", texts


def _strips(board: pymupdf.Rect, bounds: pymupdf.Rect) -> list[pymupdf.Rect]:
    """The strips along the board's four sides, within a third of its side, cut to the page."""
    reach = max(board.width, board.height) * BESIDE
    x0, y0, x1, y1 = board
    strips = [
        pymupdf.Rect(x0, y0 - reach, x1, y0),
        pymupdf.Rect(x0 - reach, y0, x0, y1),
        pymupdf.Rect(x1, y0, x1 + reach, y1),
        pymupdf.Rect(x0, y1, x1, y1 + reach),
    ]
    return [strip & bounds for strip in strips if not (strip & bounds).is_empty]


def framed(board: pymupdf.Rect) -> pymupdf.Rect:
    """The board with its frame: a thirty-second of its side around it."""
    margin = max(board.width, board.height) * FRAME
    return board + pymupdf.Rect(-margin, -margin, margin, margin)


def _beside(
    board: pymupdf.Rect,
    lines: list[tuple[pymupdf.Rect, str]],
    boards: list[pymupdf.Rect],
    shown: pymupdf.Matrix = pymupdf.Identity,
) -> dict[str, list[dict[str, Any]]]:
    """The lines beside the board, by side as the page is shown, which shown takes the page's
    points to: above or below the board and sharing some of its width, or left or right of it
    and sharing some of its height, within a third of its side. A line beside it on two sides is
    given on the nearer, and a line between two boards is beside both. A line that overlaps any
    of the page's boards, or comes within two points of one, is part of a diagram, such as its
    frame or coordinates set in a chess font, and stands beside none."""
    seen = board * shown
    reach = max(seen.width, seen.height) * BESIDE
    diagrams = [other * shown + pymupdf.Rect(-TOUCH, -TOUCH, TOUCH, TOUCH) for other in boards]
    found: dict[str, list[tuple[pymupdf.Rect, dict[str, Any]]]] = {}
    for box, text in lines:
        text, line = text.strip(), box * shown
        if not text or any(not (line & diagram).is_empty for diagram in diagrams):
            continue
        gaps = {}
        if line.x1 > seen.x0 and line.x0 < seen.x1:
            if line.y1 <= seen.y0:
                gaps["above"] = seen.y0 - line.y1
            if line.y0 >= seen.y1:
                gaps["below"] = line.y0 - seen.y1
        if line.y1 > seen.y0 and line.y0 < seen.y1:
            if line.x1 <= seen.x0:
                gaps["left"] = seen.x0 - line.x1
            if line.x0 >= seen.x1:
                gaps["right"] = line.x0 - seen.x1
        near = {side: gap for side, gap in gaps.items() if gap <= reach}
        if near:
            side = min(near, key=lambda s: near[s])
            given = {"text": text, "gap": round(near[side], 1), "box": _rounded(box)}
            found.setdefault(side, []).append((line, given))
    return {
        side: [given for _, given in sorted(found[side], key=lambda pair: (pair[0].y0, pair[0].x0))]
        for side in SIDES
        if side in found
    }


def _rounded(box: Any) -> list[float]:
    return [round(value, 1) for value in box]


def _iou(a: list[float], b: list[float]) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    shared = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - shared
    return shared / union if union else 0.0
