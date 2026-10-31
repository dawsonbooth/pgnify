"""Open a real-client session for a person to run on their Claude subscription. pgnify is set up
as a user would set it up: installed with `uv tool install`, its server registered with
`claude mcp add` and its skill installed from the plugin marketplace. An interactive Claude Code
gets the whole book and a request, as a user might write it, for one part of it, or for the whole
book when the slice names a source rather than a cut, with the evaluation's output conventions.
Of Claude Code's own tools, the model has those that plan and delegate work, and no others.

It checks that `pgnify` is installed as a command and the diagram reader's weights are in place,
then prepares a run folder with an empty Claude Code configuration, registers the server and
installs the plugin there, puts the request on the clipboard and opens Claude Code in an empty
working directory. The person logs in, pastes, sends and exits when the final answer is in.

A session tests pgnify and its skill as GitHub holds them, so it opens only when they are this
checkout's: pgnify installed from its commit, with no change to src/ or plugin/ outside it, and
the marketplace's skill the same as plugin/'s. Commit and push first, then upgrade pgnify.

A watchdog guards the experiment, not pgnify, which sets no limits of its own: it ends a session
that runs away or hangs, when its cost at list price, its subagents' and the advisor's included,
passes a safety ceiling or when its logs show no activity for half an hour. It never ends a
session for taking long while it works.

Writes experiments/results/real-client/<slice>/<timestamp>/: request.md; command.json, with the
version of Claude Code; work/, the server's output folder; claude-config/, whose session logs
are the transcripts, the subagents' among them; and, when Claude Code exits, session.json and
unwritten.pgn, the games the server saved that no PGN file was written from, if there are any,
so that work a session left unfinished stays visible. Score a part with
experiments/score_slice.py SLICE FOLDER, and a whole book with experiments/score_book.py.

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
SKILL_FILE = ROOT / "plugin" / "skills" / "pgnify-convert" / "SKILL.md"
# Claude Code's own tools that plan and delegate work, the only ones the model is given: none
# reads or writes files, runs commands or reaches the network, so the book reaches the model
# through pgnify alone. ToolSearch loads the tools Claude Code defers, pgnify's among them.
TOOLS = ["Agent", "SendMessage", "TaskStop", "EnterPlanMode", "ExitPlanMode", "Skill", "ToolSearch"]
# Of those, and of pgnify's, the ones allowed: everything else asks for approval, which
# --permission-mode dontAsk refuses. ExitPlanMode, so that a plan made in plan mode can be left;
# the skill by its plugin-qualified name and by its plain one, since Claude Code may check either.
ALLOWED = ["mcp__pgnify", f"Skill({SKILL})", f"Skill({SKILL.split(':')[1]})", "ExitPlanMode"]
# The watchdog's safety ceiling on the session's cost at its model's list price, unless its slice
# sets client_ceiling_usd: a guard against runaway work, well above the cost expected, and no
# target.
CEILING_USD = 3.0
# How long the session's logs may show no activity before the watchdog ends the session, as hung
# or as left open after its final answer.
IDLE_MINUTES = 30
DIFFERENCES = [
    "pgnify installed with uv tool install and registered with claude mcp add, not run from the "
    "checkout with --strict-mcp-config",
    "the whole book as --books, not a clipped source",
    "the conversion instructions from the plugin's skill, which the model loads itself, not in "
    "the prompt; the request names the part, or the whole book, and the output conventions only",
    f"Claude Code's tools that plan and delegate work, {', '.join(TOOLS)}, not --tools ''; "
    f"allowed: {', '.join(ALLOWED)}; --permission-mode dontAsk refuses whatever else asks for "
    "approval",
    "Claude Code's default compaction, not --autocompact 100k",
    "the release of Claude Code installed, recorded, not a pinned one",
    "a watchdog, the experiment's guard against runaway or hung sessions, ends the session at "
    "a ceiling on its cost or after half an hour without activity, never for its time",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("slice")
    slice_name = parser.parse_args().slice
    layout = session.slices()
    spec = layout["slices"][slice_name]
    # A slice of the whole book names its source; one of a part names the cut it is in.
    whole = "cut" not in spec
    book = session.book(spec["source"] if whole else layout["cuts"][spec["cut"]]["source"])
    part = spec.get("client_part", spec.get("part"))
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
    if untested := _untested(commit):
        sys.exit(f"{untested}: commit and push the checkout, then run uv tool upgrade pgnify")
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
    # The skill as the marketplace installed it, in each copy Claude Code keeps.
    copies = list(config_dir.glob("plugins/**/pgnify-convert/SKILL.md"))
    if not copies or any(copy.read_bytes() != SKILL_FILE.read_bytes() for copy in copies):
        shutil.rmtree(folder)
        sys.exit("the marketplace's skill is not this checkout's: push the checkout")
    command = ["claude", "--model", MODEL, "--effort", EFFORT, "--permission-mode", "dontAsk"]
    command += ["--tools", ",".join(TOOLS), "--allowedTools", *ALLOWED]
    asked = f"Convert {title}" if whole else f"Convert part of {title}"
    request = (
        f"{asked} into PGN, with pgnify: {part}.\n\nOutput conventions: {layout['conventions']}\n"
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
        f"{version}; pgnify was installed from commit {commit or 'unknown'}.\n"
        "Exit only once the final answer is in and no subagent is still running.\n"
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
    if whole or "truth" in spec:
        scorer = "score_book.py" if whole else "score_slice.py"
        print(f"Score it: uv run python experiments/{scorer} {slice_name} {folder}")
    else:
        print("It has no truth: check it as experiments/slices.toml says for its slice.")


def _installed_commit() -> str | None:
    """The commit uv tool installed pgnify from, when it came from a git repository: pgnify's
    --version names only the release."""
    tool_dir = subprocess.run(["uv", "tool", "dir"], capture_output=True, text=True).stdout.strip()
    site = "lib/*/site-packages/pgnify-*.dist-info/direct_url.json"
    for found in (Path(tool_dir) / "pgnify").glob(site):
        return json.loads(found.read_text()).get("vcs_info", {}).get("commit_id")
    return None


def _untested(commit: str | None) -> str | None:
    """Why a session would not test this checkout's pgnify, if it would not: another commit is
    installed, or src/ or plugin/ has changes that no commit holds."""
    git = ["git", "-C", str(ROOT)]
    head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    if commit != head.strip():
        return f"pgnify was installed from commit {commit or 'unknown'}, not {head.strip()}"
    changes = [*git, "status", "--porcelain", "--", "src", "plugin"]
    if subprocess.run(changes, capture_output=True, text=True).stdout:
        return "src/ or plugin/ has changes that no commit holds"
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
    """The session's cost at list price, from its logs, its subagents' included: each request
    counted once, at the highest count its entries record, by its model's price, the advisor's
    passes by the advisor's. A model without a price is priced as the dearest that has one, so
    that the ceiling is never passed unseen."""
    requests: dict[str, tuple[str, dict]] = {}
    for log in (config_dir / "projects").glob("**/*.jsonl"):
        for line in log.read_text(errors="replace").split("\n"):
            # Only an assistant's entry, which holds its usage, is read.
            if '"usage"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message") or {}
            usage = message.get("usage")
            if entry.get("type") != "assistant" or not usage:
                continue
            key = message.get("id") or line
            model, before = requests.get(key, (str(message.get("model")), {}))
            requests[key] = (model, score_slice.larger_usage(before, usage))
    dearest = max(score_slice.PRICES, key=lambda name: score_slice.PRICES[name].output)
    return sum(
        score_slice.request_cost(model, usage) or score_slice.request_cost(dearest, usage) or 0.0
        for model, usage in requests.values()
    )


if __name__ == "__main__":
    main()
