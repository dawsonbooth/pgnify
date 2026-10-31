"""The book's text, within a scope of its pages, that no comment of a conversion holds: possible
omissions, for `pgnify report --coverage`. The comparison is mechanical, word for word as the
report compares comments with the book (pgnify.prose), and it shows textual coverage only. A
passage the comments hold may still be at the wrong move or in the wrong game, or give the book's
words a meaning they did not have; a passage they lack may have been left out on purpose.

The book's words are taken in stretches of five, each counted as held only as many times as the
comments give it. A passage the book gives twice and the comments once is therefore held at
neither place, since nothing tells which of the two the comments keep, and both are reported as
repeated. A comment too short for a stretch holds its words where the book gives them in that
order. A hyphen compares the same whether the next word follows on its line or not, a picture
inside a move, a figurine, compares as the piece it stands for, and words with a digit in them
are left out: numbers tell apart the book's furniture, "Fork 3" from "Fork 4", more than its
prose.

What the comments do not hold is cut into runs at each word they hold. Runs shorter than SHORTEST
words are counted, not listed: they are mostly headings, captions and labels. A run most of whose
stretches the whole book gives COMMON times or more, and no comment holds, is the book's
furniture, such as navigation labels, citations and running heads, and is counted, not listed."""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pgnify.doc_tools import page_text
from pgnify.prose import bare, passages, prose, sentences

SPAN = 5
# The fewest words a possible omission is listed with.
SHORTEST = 10
# A run most of whose stretches the whole book gives this many times or more is furniture.
COMMON = 3
# The words of an excerpt, from the start of an omission and from its end.
EXCERPT = (12, 4)
# A hyphen, and any line break or spaces after it, before the next word: on both sides one space
# follows it.
HYPHEN = re.compile(r"-[ \t]*\n?[ \t]*(?=\w)")
# A picture inside a move, as a book prints a figurine, read as a piece, so that the move is
# notation on both sides.
FIGURINE = re.compile(r"\[picture \d+: [0-9a-f]{6}\]\s?(?=x?[a-h][1-8])")
DIGIT = re.compile(r"\d")
# A page that ends with a hyphen, and the first word of the next.
ENDS_WITH_HYPHEN = re.compile(r"(?<=\w)-[ \t]*\n?$")
FIRST_WORD = re.compile(r"\s*(\S+)")
# The pages --coverage names: all, or ranges such as 46-103 or 175-227,876-883.
SCOPE = re.compile(r"\d+(-\d+)?(,\d+(-\d+)?)*")


@dataclass(frozen=True)
class Omission:
    """A run of the book's words that no comment holds."""

    first_page: int
    last_page: int
    words: int
    excerpt: str
    # The book gives these words more often within the scope than the comments do.
    repeated: bool = False


@dataclass(frozen=True)
class Coverage:
    """How much of the scope's text the comments hold, and the runs they lack."""

    pages: list[int]
    # The top-level entries of the table of contents the scope's pages fall under.
    sections: list[str]
    words: int
    held: int
    furniture: int
    omissions: list[Omission] = field(default_factory=list)
    shorter_runs: int = 0
    shorter_words: int = 0


def scope_pages(scope: str) -> list[int] | None:
    """The pages a scope names, "all" for every page (None), or ValueError."""
    if scope == "all":
        return None
    if not SCOPE.fullmatch(scope):
        raise ValueError(
            f"{scope!r} is no scope: give all, or pages as doc_text numbers them, such as 46-103 "
            "or 175-227,876-883"
        )
    pages: set[int] = set()
    for part in scope.split(","):
        first, _, last = part.partition("-")
        if int(last or first) < int(first):
            raise ValueError(f"the pages {part} run backwards")
        pages |= set(range(int(first), int(last or first) + 1))
    return sorted(pages)


def words_of(text: str) -> list[str]:
    """The text's words as coverage compares them."""
    words = bare(prose(HYPHEN.sub("- ", FIGURINE.sub("N", text)))).split()
    return [word for word in words if not DIGIT.search(word)]


def coverage(
    book: Path | str, comments: Iterable[str], pages: Iterable[int] | None = None
) -> Coverage:
    """The book's text on the pages given, every page by default, that the comments do not
    hold."""
    with pymupdf.open(book) as doc:
        texts = [page_text(page) for page in doc]
        toc = [(title, page) for level, title, page in doc.get_toc() if level == 1]
    scope = sorted(set(pages) if pages is not None else range(1, len(texts) + 1))
    if not scope or scope[0] < 1 or scope[-1] > len(texts):
        raise ValueError(f"the pages to check must be within 1 to {len(texts)}")
    # A word that a hyphen breaks across two pages goes with the first.
    for number in range(len(texts) - 1):
        if ENDS_WITH_HYPHEN.search(texts[number]) and (
            match := FIRST_WORD.match(texts[number + 1])
        ):
            texts[number] = texts[number].rstrip() + match[1] + "\n"
            texts[number + 1] = texts[number + 1][match.end() :]
    # The scope's words, each with its page, in runs of consecutive pages.
    chosen = set(scope)
    blocks: list[list[tuple[str, int]]] = []
    for number in scope:
        if number - 1 not in chosen:
            blocks.append([])
        blocks[-1] += [(word, number) for word in words_of(texts[number - 1])]
    blocks = [block for block in blocks if block]
    everywhere = Counter(_stretches([word for text in texts for word in words_of(text)]))
    given = Counter(s for block in blocks for s in _stretches([word for word, _ in block]))
    held: Counter[tuple[str, ...]] = Counter()
    short: set[tuple[str, ...]] = set()
    for comment in comments:
        held.update(_stretches(words_of(comment)))
        for line in comment.split("\n"):
            for passage in passages(line):
                for sentence in sentences(passage):
                    if 2 <= len(piece := tuple(words_of(sentence))) < SPAN:
                        short.add(piece)

    omissions: list[Omission] = []
    shorter: list[int] = []
    furniture = covered = total = 0
    for block in blocks:
        plain = [word for word, _ in block]
        stretches = _stretches(plain)
        # Each word is held, where a stretch of it is held as often as the scope gives it;
        # repeated, where one is held but less often, and every other is held or given again;
        # else missing.
        state = []
        for index in range(len(plain)):
            around = stretches[max(0, index - SPAN + 1) : index + 1]
            if any(held[s] >= given[s] for s in around):
                state.append("held")
            elif any(held[s] for s in around) and all(held[s] or given[s] > 1 for s in around):
                state.append("repeated")
            else:
                state.append("missing")
        _hold_short_comments(plain, state, short)
        total += len(plain)
        covered += state.count("held")
        for kind in ("missing", "repeated"):
            for start, end in _runs(state, kind):
                inside = stretches[start : max(start, end - SPAN + 1)]
                common = sum(everywhere[s] >= COMMON and not held[s] for s in inside)
                if inside and common * 2 >= len(inside):
                    furniture += end - start
                elif end - start >= SHORTEST:
                    omissions.append(_omission(block, start, end, kind == "repeated"))
                else:
                    shorter.append(end - start)
    omissions.sort(key=lambda omission: (omission.first_page, omission.last_page))
    return Coverage(
        pages=scope,
        sections=_sections(toc, scope),
        words=total,
        held=covered,
        furniture=furniture,
        omissions=omissions,
        shorter_runs=len(shorter),
        shorter_words=sum(shorter),
    )


def _stretches(words: list[str]) -> list[tuple[str, ...]]:
    return [tuple(words[i : i + SPAN]) for i in range(len(words) - SPAN + 1)]


def _hold_short_comments(plain: list[str], state: list[str], short: set[tuple[str, ...]]) -> None:
    """Mark held the words of each comment too short for a stretch, where the book gives them in
    that order among words held by nothing else."""
    for start, end in _runs(state, "missing"):
        index = start
        while index < end:
            for length in range(SPAN - 1, 1, -1):
                if index + length <= end and tuple(plain[index : index + length]) in short:
                    state[index : index + length] = ["held"] * length
                    index += length - 1
                    break
            index += 1


def _runs(state: list[str], kind: str) -> list[tuple[int, int]]:
    """The start and end of each longest run of words in the given state."""
    runs, start = [], None
    for index, value in enumerate([*state, None]):
        if value == kind and start is None:
            start = index
        elif value != kind and start is not None:
            runs.append((start, index))
            start = None
    return runs


def _omission(block: list[tuple[str, int]], start: int, end: int, repeated: bool) -> Omission:
    words = [word for word, _ in block[start:end]]
    head, tail = EXCERPT
    if len(words) > head + tail:
        words = [*words[:head], "…", *words[-tail:]]
    return Omission(
        first_page=block[start][1],
        last_page=block[end - 1][1],
        words=end - start,
        excerpt=" ".join(words),
        repeated=repeated,
    )


def _sections(toc: list[tuple[str, int]], scope: list[int]) -> list[str]:
    """The top-level entries of the table of contents that the pages fall under, in order."""
    found: list[str] = []
    for number in scope:
        title = next((title for title, page in reversed(toc) if 1 <= page <= number), None)
        if title is not None and title not in found:
            found.append(title)
    return found
