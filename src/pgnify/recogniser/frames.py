"""Where on a page a board may be: the search read_diagram makes on a page before the reader
judges each place it finds. It looks, with OpenCV, for the frame a printed board is drawn in:

- a square, its four edges drawn;
- two parallel lines a board's width apart, where a scan has broken the other two edges;
- where neither found anything, the square on either side of a single line a board long.

It proposes places, and most of the single lines' hold no board: the reader rejects them. A
board printed without a frame, or partly, is not looked for. The thresholds were set on a scan,
a ClearScan scan and a book whose boards are set in a chess font, drawn as doc_page draws a page,
1,568 pixels on its longer side."""

import cv2
import numpy as np

# A pixel darker than this, of 255, is ink.
INK = 160
# The shortest side a board's frame may have, as a share of the page's width, and the longest
# horizontal or vertical stretch of ink, as a share of it, joined into a line.
SMALLEST, LINE = 1 / 7, 1 / 10
# The widths over heights a board's frame may have.
SQUARE = (0.85, 1.18)
# How much of its bounding box a square's outline must enclose.
FILLED = 0.85


def candidates(grey: np.ndarray) -> list[dict]:
    """The places on a page, drawn in grey, where a board may be, in the order the reader is to
    judge them: squares and pairs of lines, largest first, then the single lines' squares,
    largest first, each box once. Each has its box, [x0, y0, x1, y1] in the picture's pixels;
    found_by, "square", "two lines" or "one line"; and edge, the share of the box's outline that
    is inked along its least inked side."""
    ink = (grey < INK).astype(np.uint8)
    width = grey.shape[1]
    framed = [*_squares(ink, width)]
    single = []
    for horizontal in (True, False):
        segments = _segments(ink, width, horizontal)
        framed += _pairs(ink, segments, horizontal)
        single += _beside(ink, segments, horizontal)
    ordered = sorted(framed, key=_order, reverse=True) + sorted(single, key=_order, reverse=True)
    places: list[dict] = []
    for place in ordered:
        if place["box"] not in [other["box"] for other in places]:
            places.append(place)
    return places


def covered(place: dict, boards: list[list[float]]) -> bool:
    """Whether a board already read covers the place: overlaps it by more than half the smaller
    of the two, or, for a single line's square, which is likelier to hold part of a board, by a
    twentieth. Only a board read with its pieces covers a place, so that a place the reader
    rejects, such as one spanning several boards, or one where it reads a board with no piece,
    such as a sliver of a board, hides none of the boards inside it."""
    share = 0.05 if place["found_by"] == "one line" else 0.5
    return any(overlap(place["box"], board) > share for board in boards)


def _squares(ink: np.ndarray, width: int) -> list[dict]:
    """Outlines of the page's ink, closed across one-pixel gaps, that are squares of a board's
    size: four corners, filling their bounding boxes."""
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < width * SMALLEST or not SQUARE[0] <= w / h <= SQUARE[1]:
            continue
        if len(cv2.approxPolyDP(contour, 0.03 * cv2.arcLength(contour, True), True)) != 4:
            continue
        if cv2.contourArea(contour) / (w * h) < FILLED:
            continue
        found.append({"box": [x, y, x + w, y + h], "found_by": "square", "edge": 1.0})
    return found


def _segments(ink: np.ndarray, width: int, horizontal: bool) -> list[tuple[int, int, int]]:
    """The page's straight lines of ink at least a board's smallest side long, horizontal or
    vertical, each as its start and end along it and its place across it."""
    run = max(20, int(width * LINE))
    kernel = np.ones((1, run) if horizontal else (run, 1), np.uint8)
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
    segments = []
    for x, y, w, h in (stats[index, :4].tolist() for index in range(1, count)):
        if (w if horizontal else h) >= width * SMALLEST:
            segments.append((x, x + w, y) if horizontal else (y, y + h, x))
    return segments


def _pairs(ink: np.ndarray, segments: list[tuple[int, int, int]], horizontal: bool) -> list[dict]:
    """Squares between two parallel lines a board's width apart, sharing half their length."""
    found = []
    for start, end, across in segments:
        for other_start, other_end, other_across in segments:
            if other_across <= across:
                continue
            shared = min(end, other_end) - max(start, other_start)
            if shared < 0.5 * min(end - start, other_end - other_start):
                continue
            first, last = min(start, other_start), max(end, other_end)
            if not SQUARE[0] <= (last - first) / (other_across - across) <= SQUARE[1]:
                continue
            box = _box(first, last, across, other_across + 1, horizontal)
            found.append({"box": box, "found_by": "two lines", "edge": _edge(ink, box)})
    return found


def _beside(ink: np.ndarray, segments: list[tuple[int, int, int]], horizontal: bool) -> list[dict]:
    """The squares on either side of each line, as long as the line, within the page."""
    height, width = ink.shape
    found = []
    for start, end, across in segments:
        side = end - start
        for near, far in ((across - side, across + 1), (across, across + side)):
            box = _box(start, end, near, far, horizontal)
            if box[0] >= 0 and box[1] >= 0 and box[2] <= width and box[3] <= height:
                found.append({"box": box, "found_by": "one line", "edge": _edge(ink, box)})
    return found


def _box(start: int, end: int, near: int, far: int, horizontal: bool) -> list[int]:
    return [start, near, end, far] if horizontal else [near, start, far, end]


def _edge(ink: np.ndarray, box: list[int]) -> float:
    """The share of the box's outline inked, within two pixels of each edge, along its least
    inked side."""
    x0, y0, x1, y1 = box

    def inked(strip: np.ndarray) -> float:
        return float(strip.any(axis=0).mean()) if strip.size else 0.0

    sides = (
        ink[max(0, y0 - 2) : y0 + 3, x0:x1],
        ink[max(0, y1 - 3) : y1 + 2, x0:x1],
        ink[y0:y1, max(0, x0 - 2) : x0 + 3].T,
        ink[y0:y1, max(0, x1 - 3) : x1 + 2].T,
    )
    return round(min(inked(strip) for strip in sides), 3)


def _order(found: dict) -> tuple:
    """Largest first, then by box and edge, so that the order never depends on the search's."""
    x0, y0, x1, y1 = found["box"]
    return (x1 - x0) * (y1 - y0), found["box"], found["edge"]


def overlap(a: list[float], b: list[float]) -> float:
    """The two boxes' intersection over the smaller one's area."""
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return width * height / smaller if smaller else 0.0
