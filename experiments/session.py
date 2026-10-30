"""Open an evaluation session for a person to run on their Claude subscription: Claude Sonnet 5.5,
at high effort, on one slice of experiments/slices.toml, from its clipped source, which
experiments/cut.py cuts.

It prepares a run folder, puts the prompt on the clipboard and opens an interactive Claude Code
in an empty working directory and with an empty configuration directory, so no hooks, plugins,
settings, memory, connectors or project instructions load; the person logs in, pastes, sends and
exits when the final answer is in. The model has pgnify's tools alone: Claude Code's built-in
tools are off. The prompt is the text of pgnify's MCP prompt `convert` for the slice's part, with
slices.toml's output conventions after the request. The server is this checkout's
`pgnify serve`, given the clipped source alone as its books location and the run's work/ as its
output folder. Claude Code must be the release the evaluation pins.

Writes experiments/results/cross-book/<slice>/MS/<timestamp>/: prompt.md; command.json; work/,
the server's output folder, with the PGN, the model's account and .pgnify/; claude-config/,
whose session log is the transcript; and session.json, when Claude Code exits. Score it with
experiments/score_slice.py; `pgnify report` reports on its work/.

    uv run python experiments/session.py SLICE
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from pgnify import instructions

ROOT = Path(__file__).resolve().parents[1]
# The local layout the experiments share: the slices, the manifest of books, the clipped sources.
SLICES = ROOT / "experiments" / "slices.toml"
MANIFEST = ROOT / "data" / "local" / "manifest.toml"
CUTS = ROOT / "data" / "local" / "cuts"
RESULTS = ROOT / "experiments" / "results" / "cross-book"
# A copy of the diagram reader's weights a checkout may keep, as the live tests use it.
CHECKOUT_WEIGHTS = ROOT / "models" / "chess-diagram-to-fen"
MODEL = "claude-sonnet-5-5"
EFFORT = "high"
# The name the sessions' run folders carry.
CONFIG = "MS"
# The Claude Code release of the evaluation.
PINNED = "2.1.312"
# What keeps an interactive session as closed as a headless one: no claude.ai connectors from
# the subscription, and no connection to the terminal's editor.
ISOLATED = {
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
    "CLAUDE_CODE_AUTO_CONNECT_IDE": "false",
}
# Claude Code's other settings for the evaluation's sessions: no nonessential traffic, no
# background tasks, and timeouts long enough for a long tool call.
SETTINGS = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
    "CLAUDE_STREAM_IDLE_TIMEOUT_MS": "1800000",
    "API_TIMEOUT_MS": "3600000",
    "API_FORCE_IDLE_TIMEOUT": "0",
}
# The code and the instructions a session's tools and prompt come from, hashed into
# command.json's tools_sha256.
TOOL_CODE = [
    *sorted((ROOT / "src" / "pgnify").glob("*.py")),
    ROOT / "src" / "pgnify" / "instructions.md",
    ROOT / "src" / "pgnify" / "recogniser" / "__main__.py",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("slice")
    slice_name = parser.parse_args().slice
    folder, command, variables = prepare(slice_name)
    hand_over(folder / "prompt.md")
    env = child_environment(variables, folder / "claude-config")
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="pgnify-run-") as cwd:
        code = subprocess.run(command, cwd=cwd, env=env).returncode
    (folder / "session.json").write_text(
        json.dumps({"exit_code": code, "open_seconds": round(time.time() - started, 1)}, indent=1)
    )
    print(f"\nSession closed. Its folder: {folder}")
    print(f"Score it: uv run python experiments/score_slice.py {slice_name} {folder}")


def prepare(slice_name: str) -> tuple[Path, list[str], dict[str, str]]:
    """The run folder, the command and the environment of one session."""
    layout = slices()
    spec = layout["slices"][slice_name]
    cut_spec = layout["cuts"][spec["cut"]]
    source = cut_file(spec["cut"])
    cut_from = book(cut_spec["source"])
    version = claude_version()
    if not version.startswith(PINNED):
        sys.exit(f"Claude Code is {version!r}; the evaluation pins {PINNED}")
    folder = RESULTS / slice_name / CONFIG / datetime.now().strftime("%Y%m%dT%H%M%S")
    work = folder / "work"
    work.mkdir(parents=True)
    (folder / "claude-config").mkdir()
    command = [
        "claude",
        "--model",
        MODEL,
        "--strict-mcp-config",
        "--mcp-config",
        mcp_config(source, work),
        "--autocompact",
        "100k",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "",
        "--allowedTools",
        "mcp__pgnify",
        "--effort",
        EFFORT,
    ]
    variables = {**SETTINGS, **ISOLATED}
    prompt = instructions.request(source.name, spec["part"], layout["conventions"])
    (folder / "prompt.md").write_text(prompt)
    (folder / "command.json").write_text(
        json.dumps(
            {
                "config": CONFIG,
                "model": MODEL,
                "effort": EFFORT,
                "slice": slice_name,
                "part": spec["part"],
                "claude_version": version,
                "source": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "cut_from": str(cut_from),
                "cut_from_sha256": hashlib.sha256(cut_from.read_bytes()).hexdigest(),
                "cut_pages": cut_spec["pages"],
                "tools_sha256": tools_digest().hexdigest(),
                "environment": variables,
                "command": command,
            },
            indent=1,
        )
    )
    return folder, command, variables


def mcp_config(books: Path, out: Path) -> str:
    """Claude Code's configuration of pgnify's server, run by uv from this checkout with the
    books and output folder given, and with the checkout's diagram weights where it has them."""
    server = ["run", "--project", str(ROOT), "pgnify", "serve"]
    server += ["--books", str(books), "--out", str(out)]
    pgnify: dict[str, Any] = {"command": "uv", "args": server}
    if CHECKOUT_WEIGHTS.is_dir():
        pgnify["env"] = {"PGNIFY_WEIGHTS": str(CHECKOUT_WEIGHTS)}
    return json.dumps({"mcpServers": {"pgnify": pgnify}})


def child_environment(
    variables: dict[str, str], config_dir: Path, drop: tuple[str, ...] = ()
) -> dict[str, str]:
    """This process's environment for Claude Code: without the parent session's CLAUDE* and
    ANTHROPIC* variables, or any whose name starts with one of drop, and with the variables
    given and the configuration directory."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("CLAUDE", "ANTHROPIC", *drop))
    }
    env.update(variables, CLAUDE_CONFIG_DIR=str(config_dir))
    return env


def tools_digest() -> "hashlib._Hash":
    """A SHA-256 of the code and the instructions a session's tools and prompt come from."""
    digest = hashlib.sha256()
    for path in TOOL_CODE:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest


def slices() -> dict[str, Any]:
    """experiments/slices.toml: the output conventions, the cuts and the slices."""
    return tomllib.loads(SLICES.read_text())


def book(source: str) -> Path:
    """The book file of a source in the local manifest."""
    books = {
        entry["name"]: entry["book"] for entry in tomllib.loads(MANIFEST.read_text())["source"]
    }
    return Path(books[source])


def cut_file(cut: str) -> Path:
    """A cut's clipped source in data/local/cuts/, an EPUB or a PDF."""
    return next(CUTS.glob(f"{cut}.*"))


def claude_version() -> str:
    """The version of Claude Code installed, as `claude --version` gives it."""
    return subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()


def hand_over(path: Path, note: str = "") -> None:
    """Put a file's text on the clipboard, tell the person what to do with it, and wait until
    they are ready for Claude Code."""
    subprocess.run(
        ["pbcopy"], input=path.read_bytes(), check=True, env={**os.environ, "LC_CTYPE": "UTF-8"}
    )
    print(
        f"\nThe {path.stem} is on the clipboard (and in {path};\n"
        "if logging in replaces the clipboard, copy it again with pbcopy < that file).\n"
        "Claude Code opens next. Log in with your Claude subscription when it asks, and trust\n"
        "the folder. Then paste (Cmd+V), press Enter, and leave it until the final answer.\n"
        "/exit when done.\n"
    )
    if note:
        print(f"{note}\n")
    input("Press Enter to open Claude Code... ")


if __name__ == "__main__":
    main()
