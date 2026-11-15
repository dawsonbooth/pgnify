"""Score a book map (the template is experiments/workflows/map.md) apart from any conversion:
how large it is, whether its lines cite pages and say seen or assumed, whether the labels it
quotes are on the pages it cites, whether it carries anything a map must not (moves, positions,
long quotations of the book), and, given the slice's list of facts, which of them it states. A
fact is found when its patterns all match the map; whether a stated fact is right, and a line's
claim beyond its quotation, are the review's.

    uv run python experiments/score_map.py SLICE MAP.md [FACTS.json]

Writes map-score.json beside the map and prints a summary.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

import session

from pgnify.doc_tools import doc_text

# A page, or a range of pages with a hyphen or an en dash, U+2013.
DASH = "[-\u2013]"
PAGES = re.compile(rf"\bpp?\.\s*(\d+)(?:\s*{DASH}\s*(\d+))?((?:\s*,\s*\d+(?:\s*{DASH}\s*\d+)?)*)")
QUOTED = re.compile(r"[\"“«]([^\"”»]{3,80})[\"”»]")
MOVE = re.compile(
    r"(?<![\w-])(?:[KQRBN♔♕♖♗♘][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8]|O-O(?:-O)?|0-0(?:-0)?)"
    r"(?:=[QRBN])?[+#†]?(?![\w-])"
)
FEN = re.compile(r"\b(?:[rnbqkpRNBQKP1-8]{1,8}/){7}[rnbqkpRNBQKP1-8]{1,8}\b")
# "seen" as the template writes it, a word of its own: neither "unseen" nor "not seen" counts.
SEEN = re.compile(r"(?<!not )\bseen\b")
LONG_QUOTE = 12
WORDS_FOR_A_PART = 600


def cited(line: str) -> list[int]:
    """The pages a line cites, as "p. 12", "pp. 10-15" or "pp. 6, 73-90", with a hyphen or an
    en dash."""
    found: list[int] = []
    for match in PAGES.finditer(line):
        more = re.findall(rf"(\d+)(?:\s*{DASH}\s*(\d+))?", match[3] or "")
        ranges = [(match[1], match[2]), *more]
        for first, last in ranges:
            first_page = int(first)
            last_page = int(last) if last else first_page
            if last_page >= first_page and last_page - first_page < 400:
                found += range(first_page, last_page + 1)
    return found


def score(book: Path, text: str, facts: list[dict[str, Any]] | None) -> dict[str, Any]:
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("```")]
    claims = [line for line in lines if not line.lstrip().startswith(("#", "|---", "| ---"))]
    pages_cache: dict[int, str] = {}

    def page(number: int) -> str:
        if number not in pages_cache:
            try:
                got = doc_text(str(book), number, number)["pages"]
                pages_cache[number] = " ".join(got[0]["text"].split()) if got else ""
            except ValueError:
                pages_cache[number] = ""
        return pages_cache[number]

    supported, unsupported, unchecked = [], [], 0
    for line in claims:
        quotes = QUOTED.findall(line)
        numbers = cited(line)
        if not quotes or not numbers:
            unchecked += bool(quotes)
            continue
        for quote in quotes:
            target = " ".join(quote.split())
            if any(target in page(n) for n in numbers):
                supported.append(target)
            else:
                unsupported.append({"quote": target, "pages": numbers[:6]})
    moves = MOVE.findall(text)
    long_quotes = [q for q in QUOTED.findall(text) if len(q.split()) > LONG_QUOTE]
    result: dict[str, Any] = {
        "words": len(text.split()),
        "over_size": len(text.split()) > WORDS_FOR_A_PART,
        "claim_lines": len(claims),
        "lines_citing_pages": sum(bool(cited(line)) for line in claims),
        "lines_marked_seen": sum(bool(SEEN.search(line)) for line in claims),
        "lines_marked_assumed": sum("assumed" in line for line in claims),
        "quotes_on_cited_pages": len(supported),
        "quotes_not_on_cited_pages": unsupported,
        "quotes_without_pages": unchecked,
        "leaks": {
            "moves": moves[:20],
            "move_count": len(moves),
            "fens": FEN.findall(text),
            "long_quotes": long_quotes,
        },
    }
    if facts is not None:
        states = []
        for fact in facts:
            found = all(
                re.search(pattern, text, re.I | re.S) for pattern in fact.get("patterns", [])
            )
            wrong = any(
                re.search(pattern, text, re.I | re.S) for pattern in fact.get("wrong_patterns", [])
            )
            states.append(
                {
                    "id": fact["id"],
                    "kind": fact.get("kind"),
                    "state": "wrong" if wrong else "found" if found else "missing",
                }
            )
        result["facts"] = states
        result["facts_found"] = sum(s["state"] == "found" for s in states)
        result["facts_wrong"] = sum(s["state"] == "wrong" for s in states)
        result["facts_total"] = len(states)
    return result


def main() -> None:
    slice_name, map_path = sys.argv[1], Path(sys.argv[2])
    facts = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) > 3 else None
    layout = session.slices()
    spec = layout["slices"][slice_name]
    source = spec["source"] if "cut" not in spec else layout["cuts"][spec["cut"]]["source"]
    result = score(session.book(source), map_path.read_text(), facts)
    (map_path.parent / "map-score.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False)
    )
    shown = {
        k: v for k, v in result.items() if k not in ("facts", "quotes_not_on_cited_pages", "leaks")
    }
    print(json.dumps(shown, ensure_ascii=False))
    print("leaks:", json.dumps(result["leaks"], ensure_ascii=False)[:400])
    if result["quotes_not_on_cited_pages"]:
        print(
            "quotes not on their pages:",
            json.dumps(result["quotes_not_on_cited_pages"], ensure_ascii=False)[:400],
        )


if __name__ == "__main__":
    main()
