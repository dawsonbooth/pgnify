"""Tabulate the workflow benchmark's runs of a slice from their records: each run's assigned
workflow, model and effort, what it did, how it ended, its replies and its API's retries and
errors, its scores by category (structure apart from content), its cost at list price (none for
a local model) and its time, alone and end to end with the runs it builds on (a C run's map; a
review's first pass, and that pass's map), and its exploration. Runs are listed as they are, one
row each; nothing is averaged away. For a review, the faults of the run it reviewed that it
corrected, left or introduced are listed after the table (see review()). The nags column's total
leaves out the marks of plies before a run game that starts later.

    uv run python experiments/compare.py SLICE [--csv FILE]

Reads experiments/results/bench/SLICE/: each run's command.json, session.json, score.json (from
experiments/score_slice.py) and map-score.json (from experiments/score_map.py), where they exist.
"""

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "experiments" / "results" / "bench"
# What score_slice.py lists that is no fault of the run, and so is left out of the faults counted
# and of the faults a review corrected, left or introduced: a game that starts later on the
# truth's main line, a main line accepted in place of the truth's (structure), a line the truth
# makes optional that the run gives.
INFORMATIONAL = ("starts from the position after", "(structure)", "optional line given")
# The faults of a unit of the book's prose, which score_slice.py words by where the run has it:
# the same unit, as the truth game's and the words it quotes, is one fault in any of them.
UNIT_FAULTS = (
    "words missing",
    "words not at their place",
    "words split between comments",
    "words near but not verbatim",
    "words in run game",
)
QUOTE = re.compile(r"«(.*?)»")


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def own_cost(run: Path) -> float | None:
    """A run's cost at list price; none for a local model, which has no list price."""
    if load(run / "command.json").get("mode") == "local":
        return None
    return load(run / "session.json").get("cost_usd_at_list") or 0.0


def linked(run: Path) -> list[Path]:
    """The runs a run builds on: a C run's map, a review's first pass."""
    inputs = load(run / "command.json").get("inputs", {})
    found = [Path(inputs["map"]["run"])] if inputs.get("map", {}).get("run") else []
    return found + ([Path(inputs["review_of"]["run"])] if "review_of" in inputs else [])


def faults(run: Path) -> list[str]:
    """The faults score_slice.py listed for a run, without its running and without what it lists
    as information (INFORMATIONAL)."""
    page = run / "faults.md"
    text = page.read_text(encoding="utf-8").split("\n## Running")[0] if page.exists() else ""
    return [
        line
        for line in text.splitlines()
        if line.startswith("- ") and not any(info in line for info in INFORMATIONAL)
    ]


def unit(line: str) -> tuple[str, str] | None:
    """The truth game and the words a fault of a unit of prose quotes, whatever its wording; None
    for any other fault."""
    quoted = QUOTE.search(line)
    if quoted is None or not any(wording in line for wording in UNIT_FAULTS):
        return None
    return line[2:].split(":", 1)[0], quoted.group(1)


def review(run: Path) -> dict[str, Any]:
    """For a review, the faults of the run it reviewed, by the same scorer, that it corrected,
    left and introduced, and whether that run's PGN is still the one the review was given. A
    unit of prose whose fault the review left, worded otherwise by score_slice.py because the run
    now has the words in another place, is left, listed in changed as the pair of wordings."""
    given = load(run / "command.json").get("inputs", {}).get("review_of")
    if not given:
        return {}
    first = Path(given["run"])
    before, after = set(faults(first)), set(faults(run))
    corrected, introduced = sorted(before - after), sorted(after - before)
    changed = []
    for line in corrected[:]:
        match = next((a for a in introduced if unit(a) and unit(a) == unit(line)), None)
        if match is not None:
            corrected.remove(line)
            introduced.remove(match)
            changed.append(f"{line}\n    now: {match}")
    files = sorted((first / "work").glob("*.pgn"))
    pgn = "\n\n".join(path.read_text(encoding="utf-8").strip() for path in files)
    return {
        "of": first,
        "corrected": corrected,
        "left": sorted(before & after) + changed,
        "changed": changed,
        "introduced": introduced,
        "unchanged": hashlib.sha256(pgn.encode()).hexdigest() == given.get("pgn_sha256"),
    }


def end_to_end(run: Path) -> tuple[float | None, float | None]:
    """A run's cost and time with every run it builds on, theirs included: R on C counts C's
    map as well as C."""
    cost, seconds = own_cost(run), load(run / "session.json").get("seconds_to_final_answer")
    for earlier in linked(run):
        more_cost, more_seconds = end_to_end(earlier)
        cost = None if cost is None or more_cost is None else cost + more_cost
        seconds = None if seconds is None or more_seconds is None else seconds + more_seconds
    return cost, seconds


def row(run: Path) -> dict[str, Any]:
    command, record, score = (
        load(run / "command.json"),
        load(run / "session.json"),
        load(run / "score.json"),
    )
    mapped = load(run / "map-score.json")
    inputs = command.get("inputs", {})
    reviewed = review(run)
    cost = own_cost(run)
    total_cost, total_seconds = end_to_end(run)
    exploration = score.get("exploration", {})

    def of(have: str, total: str, apart: str | None = None) -> str:
        """have/total, the total less what the score counts apart."""
        if total not in score:
            return ""
        return f"{score.get(have, 0)}/{score[total] - (score.get(apart, 0) if apart else 0)}"

    return {
        "run": run.name,
        "workflow": command.get("workflow"),
        "model": command.get("model"),
        "effort": command.get("effort"),
        "mode": command.get("mode"),
        # C-verified's map is made with the reference in hand: a control of what a correct map
        # can do, not a workflow whose strategist costs nothing.
        "map": "verified (oracle control)"
        if command.get("workflow") == "C-verified"
        else ("model" if inputs.get("map") else ""),
        "status": record.get("status"),
        "first_attempt": record.get("first_attempt"),
        "nudges": record.get("nudges"),
        "api_retries": score.get("api_retries"),
        "api_errors": score.get("api_errors"),
        "subagents": score.get("subagents"),
        "map_written": (run / "map.md").exists(),
        "games": of("games_found", "truth_games"),
        "main_lines_exact": of("main_lines_exact", "truth_games"),
        # A main line the reference accepts in place of its own: structure, not a fault.
        "main_lines_accepted_instead": score.get("main_lines_accepted_instead", 0),
        "lines_kept": of("lines_kept", "truth_lines_required"),
        "units_verbatim": of("units_verbatim", "truth_units"),
        "units_attached": of("units_attached", "truth_units"),
        "units_missing": score.get("units_missing", 0),
        "characters_exact": of("lines_character_exact", "truth_lines"),
        # Not the marks of plies before a run game that starts later: counted apart.
        "nags": of("nags_agreeing", "truth_nags", "nags_before_run_start"),
        "headers": of("headers_kept", "truth_headers"),
        "unsupported_lines": score.get("unsupported_lines", 0),
        "plays_as_continuation": score.get("plays_as_continuation", 0),
        "games_unpaired": score.get("games_unpaired", 0),
        "units_duplicated": score.get("units_duplicated", 0),
        "games_out_of_order": score.get("games_out_of_order", 0),
        "units_out_of_order": score.get("units_out_of_order", 0),
        "prose_partition_differs": score.get("text_partition_differs", 0),
        "faults": len(faults(run)),
        "faults_corrected": len(reviewed["corrected"]) if reviewed else None,
        "faults_left": len(reviewed["left"]) if reviewed else None,
        "faults_introduced": len(reviewed["introduced"]) if reviewed else None,
        "reviewed_pgn_unchanged": reviewed["unchanged"] if reviewed else None,
        "cost_usd": round(cost, 3) if cost is not None else None,
        "cost_with_linked_usd": round(total_cost, 3) if total_cost is not None else None,
        "seconds": record.get("seconds_to_final_answer"),
        "seconds_with_linked": round(total_seconds, 1) if total_seconds is not None else None,
        "output_tokens": score.get("output_tokens"),
        "pages_read": exploration.get("pages_read"),
        "distinct_pages": exploration.get("distinct_pages_read"),
        "outside_scope": exploration.get("pages_read_outside_scope"),
        "searches": exploration.get("searches"),
        "map_facts": f"{mapped.get('facts_found')}/{mapped.get('facts_total')}"
        if mapped.get("facts_total")
        else "",
        "models_seen": ";".join(
            sorted({m for ms in (record.get("models") or {}).values() for m in ms})
        ),
        "scorer": score.get("scorer"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("slice")
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()
    runs = sorted(p.parent for p in (RESULTS / args.slice).glob("*/*/*/command.json"))
    rows = [row(run) for run in runs]
    if not rows:
        print("no runs")
        return
    scorers = {found["scorer"] for found in rows if found["scorer"]}
    if len(scorers) > 1:
        print(f"Scored with {len(scorers)} scorers, {', '.join(sorted(scorers))}: score again.")
    columns = list(rows[0])
    print("| " + " | ".join(columns) + " |")
    print("|" + "---|" * len(columns))
    for found in rows:
        print(
            "| " + " | ".join(str(found[c]) if found[c] is not None else "" for c in columns) + " |"
        )
    for run in runs:
        reviewed = review(run)
        if not reviewed:
            continue
        print(f"\nReview {run.name} of {reviewed['of'].relative_to(RESULTS)}:")
        for kind in ("corrected", "introduced", "changed"):
            title = "Left, worded otherwise" if kind == "changed" else kind.capitalize()
            print(f"\n{title} ({len(reviewed[kind])}):")
            print("\n".join(reviewed[kind]) or "(none)")
    if args.csv:
        with args.csv.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()
