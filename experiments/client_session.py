"""Open a real-client session for a person to run on their Claude subscription. pgnify is set up
as a user would set it up: installed with `uv tool install`, its server registered with
`claude mcp add` and its skill installed from the plugin marketplace. An interactive Claude Code
gets the whole book and a request, as a user might write it, for one part of it, with the
evaluation's output conventions.

It checks that `pgnify` is installed as a command and the diagram reader's weights are in place,
then prepares a run folder with an empty Claude Code configuration, registers the server and
installs the plugin there, puts the request on the clipboard and opens Claude Code in an empty
working directory. The person logs in, pastes, sends and exits when the final answer is in.

A watchdog guards the experiment, not pgnify, which sets no limits of its own: it ends a session
that runs away or hangs, when its cost at list price passes a safety ceiling or when its log
shows no activity for half an hour. It never ends a session for taking long while it works.

Writes experiments/results/real-client/<slice>/<timestamp>/: request.md; command.json; work/,
the server's output folder; claude-config/, whose session log is the transcript; and, when
Claude Code exits, session.json and unwritten.pgn, the games the server saved that no PGN file
was written from, if there are any, so that work a session left unfinished stays visible. Score
it with experiments/score_slice.py SLICE FOLDER.

    uv run python experiments/client_session.py SLICE
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import score_slice
import session

from pgnify import weights
from pgnify.outputs import read_records

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "experiments" / "results" / "real-client"
MODEL = session.MODEL
EFFORT = session.EFFORT
MARKETPLACE = "dawsonbooth/pgnify"
SKILL = "pgnify:pgnify-convert"
# The watchdog's safety ceiling on the session's cost at its model's list price, unless its slice
# sets client_ceiling_usd: a guard against runaway work, well above the cost expected, and no
# target.
CEILING_USD = 3.0
# How long the session's log may show no activity before the watchdog ends the session, as hung
# or as left open after its final answer.
IDLE_MINUTES = 30
# The kinds of token the watchdog counts, each with its list price in dollars per million tokens;
# cache writes at the one-hour rate, the higher.
PRICE = score_slice.PRICES[MODEL]
RATES = {
    "input_tokens": PRICE.input,
    "output_tokens": PRICE.output,
    "cache_read_input_tokens": PRICE.cache_read,
    "cache_creation_input_tokens": PRICE.cache_write_1h,
}
DIFFERENCES = [
    "pgnify installed with uv tool install and registered with claude mcp add, not run from the "
    "checkout with --strict-mcp-config",
    "the whole book as --books, not a clipped source",
    "the conversion instructions from the plugin's skill, which the model loads itself, not in "
    "the prompt; the request names the part and the output conventions only",
    "Claude Code's built-in tools listed, but refused by --permission-mode dontAsk; only "
    f"pgnify's tools and the skill {SKILL} are allowed",
    "Claude Code's default compaction, not --autocompact 100k",
    "a watchdog, the experiment's guard against runaway or hung sessions, ends the session at "
    "a ceiling on its cost or after half an hour without activity, never for its time",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("slice")
    slice_name = parser.parse_args().slice
    layout = session.slices()
    spec = layout["slices"][slice_name]
    book = session.book(layout["cuts"][spec["cut"]]["source"])
    part = spec.get("client_part", spec["part"])
    ceiling = spec.get("client_ceiling_usd", CEILING_USD)
    # The library's file names are "Title - Author".
    title = book.stem.split(" - ")[0]
    version = session.claude_version()
    # Claude Code, and the server it starts, run as they would from the user's shell: without
    # this checkout's environment, which uv run puts first on the PATH, so that `pgnify` is the
    # command uv tool installed.
    path = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if Path(entry) != Path(sys.prefix) / "bin"
    )
    uv_tool = subprocess.run(["uv", "tool", "dir", "--bin"], capture_output=True, text=True)
    tool_bin = Path(uv_tool.stdout.strip())
    pgnify = shutil.which("pgnify", path=path)
    if pgnify is None or Path(pgnify).parent != tool_bin:
        sys.exit(f"pgnify is {pgnify}, not uv tool's: install it as the README says")
    pgnify_version = subprocess.run([pgnify, "--version"], capture_output=True, text=True).stdout
    commit = _installed_commit()
    if missing := weights.missing():
        sys.exit(f"the diagram reader's weights are not installed ({len(missing)} missing)")

    folder = RESULTS / slice_name / datetime.now().strftime("%Y%m%dT%H%M%S")
    work = folder / "work"
    config_dir = folder / "claude-config"
    work.mkdir(parents=True)
    config_dir.mkdir()
    env = session.child_environment(session.ISOLATED, config_dir, drop=("VIRTUAL_ENV", "UV_"))
    env["PATH"] = path
    serve = ["pgnify", "serve", "--books", str(book), "--out", str(work)]
    setup = [
        ["claude", "mcp", "add", "--scope", "user", "pgnify", "--", *serve],
        ["claude", "plugin", "marketplace", "add", MARKETPLACE],
        ["claude", "plugin", "install", "pgnify@pgnify"],
    ]
    for step in setup:
        done = subprocess.run(step, env=env, capture_output=True, text=True)
        if done.returncode != 0:
            sys.exit(f"{' '.join(step[:4])} failed: {done.stdout}{done.stderr}")
    command = ["claude", "--model", MODEL, "--effort", EFFORT, "--permission-mode", "dontAsk"]
    # Allow the skill by its plugin-qualified name and by its plain one; Claude Code may check
    # either.
    command += ["--allowedTools", "mcp__pgnify", f"Skill({SKILL})", f"Skill({SKILL.split(':')[1]})"]
    request = (
        f"Convert part of {title} into PGN, with pgnify: {part}.\n\n"
        f"Output conventions: {layout['conventions']}\n"
    )
    (folder / "request.md").write_text(request)
    (folder / "command.json").write_text(
        json.dumps(
            {
                "slice": slice_name,
                "part": part,
                "model": MODEL,
                "effort": EFFORT,
                "claude_version": version,
                "pgnify": {"path": pgnify, "version": pgnify_version.strip(), "commit": commit},
                "marketplace": MARKETPLACE,
                "book": str(book),
                "book_sha256": hashlib.sha256(book.read_bytes()).hexdigest(),
                "setup": setup,
                "command": command,
                "environment": session.ISOLATED,
                "cost_ceiling_usd": ceiling,
                "idle_limit_seconds": IDLE_MINUTES * 60,
                "differences_from_the_evaluation_sessions": DIFFERENCES,
            },
            indent=1,
        )
    )
    session.hand_over(
        folder / "request.md",
        f"pgnify was installed from commit {commit or 'unknown'}.\n"
        f"The experiment's watchdog ends the session if its cost passes ${ceiling:.2f} at list "
        f"price,\nor after {IDLE_MINUTES} minutes without activity; never for taking long while "
        "it works.",
    )
    started = time.time()
    stopped: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pgnify-client-") as cwd:
        process = subprocess.Popen(command, cwd=cwd, env=env)
        watchdog = threading.Thread(
            target=_watch,
            args=(process, config_dir, started, stopped, ceiling),
            daemon=True,
        )
        watchdog.start()
        code = process.wait()
    unwritten = _unwritten(work)
    if unwritten:
        (folder / "unwritten.pgn").write_text(
            "\n\n".join(text.strip() for _, text in unwritten) + "\n", encoding="utf-8"
        )
    (folder / "session.json").write_text(
        json.dumps(
            {
                "exit_code": code,
                "open_seconds": round(time.time() - started, 1),
                "stopped_by": stopped[0] if stopped else None,
                "cost_usd_at_list": round(_cost(config_dir), 4),
                "unwritten_games": [name for name, _ in unwritten],
            },
            indent=1,
        )
    )
    stopped_by = f", stopped by {stopped[0]}" if stopped else ""
    print(f"\nSession closed{stopped_by}. Its folder: {folder}")
    if unwritten:
        print(f"{len(unwritten)} saved games were written to no PGN file: see unwritten.pgn.")
    print(f"Score it: uv run python experiments/score_slice.py {slice_name} {folder}")


def _installed_commit() -> str | None:
    """The commit uv tool installed pgnify from, when it came from a git repository: pgnify's
    --version names only the release."""
    tool_dir = subprocess.run(["uv", "tool", "dir"], capture_output=True, text=True).stdout.strip()
    site = "lib/*/site-packages/pgnify-*.dist-info/direct_url.json"
    for found in (Path(tool_dir) / "pgnify").glob(site):
        return json.loads(found.read_text()).get("vcs_info", {}).get("commit_id")
    return None


def _unwritten(work: Path) -> list[tuple[str, str]]:
    """The games the server saved that no PGN file was written from, each with its PGN, book by
    book and by name: work a session left unfinished, or set aside."""
    written: dict[str, set[str]] = {}
    for record in read_records(work / ".pgnify").values():
        if isinstance(record.get("games"), list):
            written.setdefault(record["book_key"], set()).update(record["games"])
    unwritten = []
    for games in sorted((work / ".pgnify" / "books").glob("*/games")):
        for saved in sorted(games.glob("*.pgn")):
            if saved.stem not in written.get(games.parent.name, set()):
                unwritten.append((saved.stem, saved.read_text(encoding="utf-8")))
    return unwritten


def _watch(
    process: subprocess.Popen,
    config_dir: Path,
    started: float,
    stopped: list[str],
    ceiling: float,
) -> None:
    """End the session when its cost at list price passes the ceiling, in dollars, or when its
    log has shown no activity for IDLE_MINUTES; never for its time while it works."""
    while process.poll() is None:
        time.sleep(10)
        reason = None
        if time.time() - _last_activity(config_dir, started) > IDLE_MINUTES * 60:
            reason = f"{IDLE_MINUTES} minutes without activity"
        elif _cost(config_dir) > ceiling:
            reason = f"the ${ceiling:.2f} safety ceiling"
        if reason and process.poll() is None:
            stopped.append(reason)
            process.terminate()
            return


def _last_activity(config_dir: Path, started: float) -> float:
    """When the session's log last changed, or when the session started if it has no log yet."""
    return max(
        [started, *(log.stat().st_mtime for log in (config_dir / "projects").glob("**/*.jsonl"))]
    )


def _cost(config_dir: Path) -> float:
    """The session's cost at list price, from its log: each request counted once, at the highest
    count its entries record for each kind of token."""
    requests: dict[str, tuple[int, ...]] = {}
    for log in (config_dir / "projects").glob("**/*.jsonl"):
        for line in log.read_text(errors="replace").split("\n"):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message") or {}
            usage = message.get("usage")
            if entry.get("type") != "assistant" or not usage:
                continue
            tokens = tuple(usage.get(kind) or 0 for kind in RATES)
            key = message.get("id") or line
            requests[key] = tuple(map(max, requests.get(key, (0,) * len(RATES)), tokens))
    return sum(
        sum(count * rate for count, rate in zip(tokens, RATES.values(), strict=True)) / 1e6
        for tokens in requests.values()
    )


if __name__ == "__main__":
    main()
