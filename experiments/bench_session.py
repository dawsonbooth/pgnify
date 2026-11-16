"""Run one session of the workflow benchmark: one slice of experiments/slices.toml, one workflow,
one model at one effort, in the pinned environment that experiments/bench_env.py records.

The workflows, whose texts are experiments/workflows/:
- A: the ordinary request, the part and the output conventions, as client_session.py sends it;
- B: A, then a brief survey and a book map before converting;
- C-map: C's first session, a survey that gives a book map as its final answer and converts
  nothing, saved as the run's map.md;
- C: A with a map given, from a C-map run (--map RUN) or a file;
- C-verified: A with the slice's verified map, the control (--map FILE);
- D: A, then a map with work units, subagents for the units, reconciliation and an audit;
- R: a review of an earlier run against the book (--review-of RUN), on a copy of its output.

Every session runs Claude Code as the environment pins it: its copy of the release, in
restricted mode, which reads no settings file and neither sends nor takes messages from other
sessions on this machine; pgnify's server from the pinned commit, given with --mcp-config as the
only server, reading only the slice's book and writing only the run's work/ folder; the skill
from that commit through --plugin-dir; the advisor tool off; no auto-update; Claude Code's tools
that plan and delegate and pgnify's, and nothing else. Claude Code keeps its temporary files in
the run's folder. Three modes:
- interactive, the default: the request goes on the clipboard and the person pastes it, as with
  client_session.py; a session that ends its turn before writing its PGN gets the fixed reply of
  experiments/workflows/nudge.md, pasted by the person, at most twice;
- --headless: `claude -p` on the person's Claude subscription, with the token `claude
  setup-token` gives, read from CLAUDE_CODE_OAUTH_TOKEN in this process's environment (as
  bench_batch.py passes it), given to Claude Code alone, not to pgnify's server, and never
  written; the fixed reply is sent with --resume, at most twice;
- --local: `claude -p` on a local model through Ollama, with long stream and request timeouts and
  no idle timeout on its HTTP client; the model's digest, quantization, context and parameters,
  and Ollama's memory for it, are recorded.

Before the session, a preflight asks Claude Code how it is set up, with no prompt and so no call
to a model: the credential and the API's address, the model and effort, the settings files read
(none), pgnify's server connected with its tools, the skill, the version, the permission mode,
fast mode off. Once a headless session starts, its stream is checked as it grows: each report of
its setup (its tools exactly these, an API key in use or not, its model, server, skill, plugin,
and no messages from other sessions), and every answer, the session's own from its model and a
subagent's from a model the benchmark runs, with no fallback to another. A failed check ends the
session at once, as preflight_failed or unexpected_model. A rate limit report that leaves
usage beyond the plan possible (overage not refused, or in use) ends it the same way, as
overage_possible; the stop comes after the request that reports it.

A watchdog ends a session whose cost at list price passes its ceiling (never for a local model),
whose logs and stream show no activity for 30 minutes, or, for a local model, that runs six hours,
its fixed replies included.

Writes experiments/results/bench/<slice>/<workflow>/<model>-<effort>/<timestamp>/: request.md;
command.json, with every input and the harness's files by hash; mcp.json, the server's
configuration; preflight.json; work/, the server's output folder; claude-config/, whose logs are
the transcripts; claude-tmp/, Claude Code's temporary files; stream-N.jsonl in a headless
session; session.json, with the status, the phases and their cost and time, the models that
answered and the credential scan; map.md where the session wrote a book map; unwritten.pgn where
it saved games it never wrote.

Exits 0 when the session ended in a way the benchmark measures (written, no_pgn, no_map,
stopped), and 2 when what follows it should not run: a failed preflight, an unexpected model,
overage possible, an authentication failure, a usage or rate limit, an API error, an interrupted
session, or the token found in a file of the run, which is then redacted.

    uv run python experiments/bench_session.py SLICE WORKFLOW --model MODEL --effort LEVEL [...]
"""

import argparse
import contextlib
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bench_env
import client_session
import score_slice
import session

from pgnify.outputs import read_notes, read_records

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "experiments" / "results" / "bench"
WORKFLOWS = ROOT / "experiments" / "workflows"
CHOICES = ("A", "B", "C-map", "C", "C-verified", "D", "R")
TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
# What every benchmark session runs with, besides its configuration and temporary directories:
# no claude.ai connectors or editor, the advisor tool off so that the session's model is the one
# named, no update of Claude Code during the session, and a headless session waiting for its
# subagents in the background however long they take, the watchdog bounding it: by default
# Claude Code stops them ten minutes after the main conversation's last turn.
SETTINGS = {
    **session.ISOLATED,
    "CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1",
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0",
}
# Ollama's address and Claude Code's settings for a local model: a long stream and request timeout,
# since Ollama sends a tool call only once it is complete, and no idle timeout on its HTTP client.
OLLAMA = {
    "ANTHROPIC_BASE_URL": "http://localhost:11434",
    "ANTHROPIC_AUTH_TOKEN": "ollama",
    "ANTHROPIC_API_KEY": "ollama",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_STREAM_IDLE_TIMEOUT_MS": "1800000",
    "API_TIMEOUT_MS": "3600000",
    "API_FORCE_IDLE_TIMEOUT": "0",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32000",
}
# The hosted models the benchmark runs, by exact ID: an answer from any other is unexpected.
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"]
# pgnify's tools at the pinned commit, and Claude Code's that a session may have besides; its
# report of its setup calls the Agent tool Task.
PGNIFY_TOOLS = [
    "build_pgn",
    "check_fen",
    "check_pgn",
    "doc_images",
    "doc_info",
    "doc_page",
    "doc_search",
    "doc_text",
    "read_diagram",
    "validate_moves",
    "write_pgn",
]
AGENT_TOOLS = {*client_session.TOOLS, "Task"}
# Claude Code's reports that a request was answered by another model than the session's.
FALLBACKS = {"model_fallback", "model_refusal_fallback", "model_consent_fallback"}
NUDGES = 2
IDLE_MINUTES = 30
LOCAL_HOURS = 6
# How a session may end and the benchmark measure it; any other end stops a batch of runs.
MEASURED = {"written", "no_pgn", "no_map", "stopped"}
# How much a workflow's session costs against A's on the same slice and model, for its ceiling.
WORKFLOW_FACTOR = {
    "A": 1.0,
    "B": 1.2,
    "C-map": 0.4,
    "C": 1.0,
    "C-verified": 1.0,
    "D": 1.5,
    "R": 1.2,
}
# The harness's own files, recorded by hash in every run.
HARNESS = [
    Path(__file__),
    ROOT / "experiments" / "bench_env.py",
    ROOT / "experiments" / "client_session.py",
    ROOT / "experiments" / "session.py",
    ROOT / "experiments" / "score_slice.py",
    ROOT / "experiments" / "slices.toml",
    *sorted(WORKFLOWS.glob("*.md")),
]
# Messages that tell how a session failed, as the API or Claude Code words them.
FAILURES = [
    (
        "auth_error",
        re.compile(
            r"authentication_error|\b401\b|invalid (x-api-key|api key|bearer)|oauth token|/login",
            re.I,
        ),
    ),
    (
        "usage_limit",
        re.compile(
            r"usage limit|hit your [a-z ]*limit|reached your [a-z0-9. ]*limit|limit reached"
            r"|out of (extra )?usage|requires usage credits",
            re.I,
        ),
    ),
    ("rate_limited", re.compile(r"rate_limit_error|\b429\b|rate limit", re.I)),
    (
        "api_error",
        re.compile(
            r"overloaded_error|\b529\b|\b5\d\d\b|api error|connection error|ECONNRESET"
            r"|socket hang up",
            re.I,
        ),
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("slice")
    parser.add_argument("workflow", choices=CHOICES)
    parser.add_argument(
        "--model", required=True, help="the exact model ID, or a local model's name"
    )
    parser.add_argument(
        "--effort", required=True, choices=["low", "medium", "high", "xhigh", "max"]
    )
    parser.add_argument("--map", type=Path, help="C: a C-map run's folder; C-verified: a map file")
    parser.add_argument("--review-of", type=Path, help="R: the run to review")
    parser.add_argument("--subagent-model", help="D: the Agent tool's model for the units")
    parser.add_argument(
        "--instructions",
        action="store_true",
        help="give pgnify's conversion instructions in the request, as its MCP prompt does, for a "
        "model that does not load the skill: a configuration of its own, reported apart",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true")
    mode.add_argument("--local", action="store_true")
    parser.add_argument("--ceiling", type=float, help="the watchdog's ceiling, at list price")
    parser.add_argument("--label", default="", help="a word for the run folder's name")
    args = parser.parse_args()
    if differences := bench_env.verify():
        sys.exit("the pinned environment differs from its manifest:\n" + "\n".join(differences))

    layout = session.slices()
    spec = layout["slices"][args.slice]
    source = spec["source"] if "cut" not in spec else layout["cuts"][spec["cut"]]["source"]
    book = session.book(source)
    title = book.stem.split(" - ")[0]
    part = spec.get("client_part", spec["part"])
    text, inputs = request(args, title, part, layout["conventions"])
    if args.instructions:
        pinned = next(
            (bench_env.ENV / "tools" / "pgnify" / "lib").glob(
                "python*/site-packages/pgnify/instructions.md"
            )
        )
        given = pinned.read_text(encoding="utf-8").strip()
        text = f"{given}\n\n{text}"
        inputs["instructions"] = {"path": str(pinned), "sha256": _digest(given.encode())}
    mode_name = "local" if args.local else "headless" if args.headless else "interactive"
    token = os.environ.get(TOKEN) if args.headless else None
    if args.headless and not token:
        sys.exit(f"--headless needs {TOKEN}, from `claude setup-token`, in the environment")
    if not args.local and args.model not in MODELS:
        sys.exit(f"{args.model} is not a model the benchmark runs: {', '.join(MODELS)}")
    local = _ollama_model(args.model) if args.local else None

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    name = (
        f"{args.model}-{args.effort}"
        + ("-instructions" if args.instructions else "")
        + (f"-{args.label}" if args.label else "")
    )
    folder = RESULTS / args.slice / args.workflow / name / stamp
    work, config_dir = folder / "work", folder / "claude-config"
    if args.workflow == "R":
        shutil.copytree(args.review_of / "work", work)
    else:
        work.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (folder / "claude-tmp").mkdir()
    env = environment(args, folder, token)
    serve = [str(bench_env.pgnify_command()), "serve", "--books", str(book), "--out", str(work)]
    # The server is started without the subscription token in its environment.
    server = {"type": "stdio", "command": "/usr/bin/env", "args": ["-u", TOKEN, *serve]}
    (folder / "mcp.json").write_text(json.dumps({"mcpServers": {"pgnify": server}}, indent=1))
    command = [str(bench_env.claude_command()), "--model", args.model, "--effort", args.effort]
    command += ["--restricted", "--mcp-config", str(folder / "mcp.json"), "--strict-mcp-config"]
    command += ["--permission-mode", "dontAsk", "--tools", ",".join(client_session.TOOLS)]
    command += [
        "--allowedTools",
        *client_session.ALLOWED,
        "--plugin-dir",
        str(bench_env.plugin_dir()),
    ]
    if args.local:
        # Compaction before Ollama's 131,072-token window: Claude Code, not knowing the model,
        # takes its window for 200,000 tokens, and past Ollama's, context can be cut unseen.
        command += ["--autocompact", "100k"]
    ceiling = None if args.local else args.ceiling or default_ceiling(spec, args)
    expected = {
        "model": args.model,
        "effort": args.effort,
        "local": args.local,
        "server": server,
        "base_url": env.get("ANTHROPIC_BASE_URL"),
        "models": {args.model} if args.local else {args.model, *MODELS},
        "release": json.loads(bench_env.MANIFEST.read_text())["environment"]["claude"]["release"],
    }
    (folder / "request.md").write_text(text)
    (folder / "command.json").write_text(
        json.dumps(
            {
                "slice": args.slice,
                "part": part,
                "workflow": args.workflow,
                "model": args.model,
                "effort": args.effort,
                "subagent_model": args.subagent_model,
                "instructions_in_request": args.instructions,
                "mode": mode_name,
                "inputs": inputs,
                "request_sha256": _digest(text.encode()),
                "book": str(book),
                "book_sha256": _digest(book.read_bytes()),
                "environment_manifest_sha256": _digest(bench_env.MANIFEST.read_bytes()),
                "harness_sha256": {
                    str(p.relative_to(ROOT)): _digest(p.read_bytes()) for p in HARNESS
                },
                "command": command,
                "server": server,
                "variables": sorted(env),
                "variables_set": {
                    k: v
                    for k, v in env.items()
                    if (k in SETTINGS or k in OLLAMA or k == "CLAUDE_CODE_TMPDIR")
                    and k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
                },
                "local_model": local,
                "cost_ceiling_usd": ceiling,
                "idle_limit_seconds": IDLE_MINUTES * 60,
                "wall_limit_seconds": LOCAL_HOURS * 3600 if args.local else None,
                "nudge_sha256": _digest((WORKFLOWS / "nudge.md").read_bytes()),
                "nudges_at_most": NUDGES,
            },
            indent=1,
        )
    )
    started = time.time()
    memory: dict[str, Any] | None = {} if args.local else None
    with tempfile.TemporaryDirectory(prefix="pgnify-bench-") as cwd:
        checked = preflight(command, env, cwd, folder, expected)
        (folder / "preflight.json").write_text(json.dumps(checked, indent=1))
        if checked["failures"]:
            phases: list[dict] = []
            print("preflight failed:\n- " + "\n- ".join(checked["failures"]))
        elif args.local or args.headless:
            phases = headless(
                command, text, env, cwd, folder, ceiling, expected, memory, args.workflow
            )
        else:
            phases = interactive(command, folder, env, cwd, ceiling)
    if local is not None:
        local["memory"] = memory
    status, found = record(folder, started, phases, token, args.workflow, checked, local)
    sys.exit(0 if status in MEASURED and not found else 2)


def request(args: argparse.Namespace, title: str, part: str, conventions: str) -> tuple[str, dict]:
    """The text the session is sent, and what went into it, by path and hash."""
    ordinary = (
        f"Convert part of {title} into PGN, with pgnify: {part}.\n\n"
        f"Output conventions: {conventions}\n"
    )
    fill = {"title": title, "part": part, "conventions": conventions}
    template = _text("map.md").format(**fill)
    inputs: dict[str, Any] = {}
    if args.workflow == "A":
        return ordinary, inputs
    if args.workflow == "B":
        return f"{ordinary}\n{_text('survey.md').format(template=template)}", inputs
    if args.workflow == "C-map":
        return _text("strategist.md").format(template=template, **fill), inputs
    if args.workflow in ("C", "C-verified"):
        if args.map is None:
            sys.exit(f"{args.workflow} needs --map")
        path = args.map / "map.md" if args.map.is_dir() else args.map
        given = path.read_text()
        inputs["map"] = {"path": str(path), "sha256": _digest(given.encode())}
        if args.map.is_dir():
            inputs["map"]["run"] = str(args.map)
        return f"{ordinary}\n{_text('with-map.md').format(map=given)}", inputs
    if args.workflow == "D":
        units = _text("map-units.md").format(**fill)
        model = (
            f", launched with the Agent tool's model set to {args.subagent_model}"
            if args.subagent_model
            else ""
        )
        return (
            f"{ordinary}\n{_text('delegate.md').format(template=units, subagent_model=model)}",
            inputs,
        )
    reviewed = args.review_of
    if reviewed is None:
        sys.exit("R needs --review-of")
    records = read_records(reviewed / "work" / ".pgnify")
    files = sorted((reviewed / "work").glob("*.pgn"))
    pgn = "\n\n".join(path.read_text(encoding="utf-8").strip() for path in files)
    notes = "\n\n".join(read_notes(path) or "" for path in files).strip() or "(none)"
    names = []
    for path in files:
        games = records.get(path.name, {}).get("games")
        if isinstance(games, list):
            names += [n for n in games if isinstance(n, str)]
    inputs["review_of"] = {
        "run": str(reviewed),
        "pgn_sha256": _digest(pgn.encode()),
        "names": names,
    }
    text = _text("review.md").format(
        pgn=pgn, notes=notes, names=", ".join(names) or "(none)", **fill
    )
    return text, inputs


def environment(args: argparse.Namespace, folder: Path, token: str | None) -> dict[str, str]:
    """The session's environment: this process's without its CLAUDE* and ANTHROPIC* variables,
    with the benchmark's settings and the run's temporary folder, and with Ollama's for a local
    model or the subscription's token for a headless one. ANTHROPIC_API_KEY is never set for a
    hosted session, so that it cannot be billed to an API key."""
    env = session.child_environment(SETTINGS, folder / "claude-config", drop=("VIRTUAL_ENV", "UV_"))
    env["CLAUDE_CODE_TMPDIR"] = str(folder / "claude-tmp")
    if args.local:
        env.update(OLLAMA)
    elif token:
        env[TOKEN] = token
    return env


def default_ceiling(spec: dict, args: argparse.Namespace) -> float:
    """Three times the run's forecast at list price: the slice's forecast for A on Sonnet 5.5,
    scaled by the model's output price against Sonnet's and by the workflow, and at least $1."""
    forecast = spec.get("forecast_usd", 1.0)
    price = next(p for name, p in score_slice.PRICES.items() if args.model.startswith(name))
    sonnet = score_slice.PRICES["claude-sonnet-5-5"]
    factor = price.output / sonnet.output
    return round(max(1.0, 3 * forecast * factor * WORKFLOW_FACTOR[args.workflow]), 2)


def control(command: list[str], env: dict, cwd: str, wait_for_servers: bool) -> dict[str, Any]:
    """Claude Code's answers to control requests sent over stream-json input with no prompt, so
    that no model is called: initialize, mcp_status (asked again while a server is still
    connecting, if wait_for_servers), get_status and get_settings."""
    process = subprocess.Popen(
        [
            *command,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-prompts",
            "none",
        ],
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdin, stdout, stderr = process.stdin, process.stdout, process.stderr
    assert stdin is not None and stdout is not None and stderr is not None
    lines: queue.Queue[str] = queue.Queue()
    errors: list[str] = []
    threading.Thread(target=lambda: [lines.put(line) for line in stdout], daemon=True).start()
    threading.Thread(target=lambda: errors.extend(stderr), daemon=True).start()
    asked = 0

    def ask(subtype: str) -> dict[str, Any]:
        nonlocal asked
        asked += 1
        number = f"preflight-{asked}"
        request = {"type": "control_request", "request_id": number, "request": {"subtype": subtype}}
        try:
            stdin.write(json.dumps(request) + "\n")
            stdin.flush()
        except OSError:
            return {"error": f"Claude Code ended before {subtype}: {''.join(errors)[-300:]}"}
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                event = json.loads(lines.get(timeout=0.5))
            except queue.Empty:
                if process.poll() is not None and lines.empty():
                    return {
                        "error": f"Claude Code ended before {subtype}: {''.join(errors)[-300:]}"
                    }
                continue
            except json.JSONDecodeError:
                continue
            answer = event.get("response") or {}
            if event.get("type") == "control_response" and answer.get("request_id") == number:
                return answer.get("response") or {"error": answer.get("error")}
        return {"error": f"no answer to {subtype} in 60 seconds"}

    answers: dict[str, Any] = {}
    try:
        answers["initialize"] = ask("initialize")
        for _ in range(120):
            answers["mcp_status"] = ask("mcp_status")
            servers = answers["mcp_status"].get("mcpServers") or []
            if not wait_for_servers or all(s.get("status") != "pending" for s in servers):
                break
            time.sleep(0.5)
        answers["get_status"] = ask("get_status")
        answers["get_settings"] = ask("get_settings")
    finally:
        with contextlib.suppress(OSError):
            stdin.close()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
    return answers


def credential_failures(answers: dict[str, Any], base_url: str | None) -> list[str]:
    """How the credential and the API in control()'s answers differ from the subscription token
    alone, on Anthropic's own API at base_url (None: its usual address)."""
    account = answers["initialize"].get("account") or {}
    status_rows = rows(answers["get_status"])
    address = status_rows.get("Anthropic base URL")
    failures = []
    if account.get("tokenSource") != TOKEN or "not in use" in str(status_rows.get("Auth token")):
        failures.append(f"the credential is not the subscription token: {account}")
    if account.get("apiKeySource") not in (None, "none") or "API key" in status_rows:
        failures.append(f"an API key would be used: {account.get('apiKeySource')}")
    if account.get("apiProvider") != "firstParty":
        failures.append(f"the API is {account.get('apiProvider')!r}, not Anthropic's own")
    if address != base_url and not (base_url is None and address == "https://api.anthropic.com"):
        failures.append(f"the API's address is {address}, not {base_url or 'the usual one'}")
    return failures


def catalog_failures(catalog: list[dict], model: str) -> list[str]:
    """Why the model is not one the session may use, from the models of the initialize answer.
    MODELS guards the ID itself. This refuses an ID with no row resolving to it but Claude Code's
    fallback for an ID it cannot name ("Custom model"), and a row that says the model needs usage
    credits, beyond the subscription. A row may list the model under an alias or under its own
    ID, as a Fable session's row does. The credits test is best effort: the suffix follows the
    account's usage-credit settings as Claude Code has loaded them when it answers. A
    claude-fable- ID it cannot name (claude-fable-5-9) gets the current Fable's row, and a known
    ID with a suffix (claude-sonnet-5-5-x) is named by its family; MODELS refuses both before
    the preflight."""
    rows = [
        m
        for m in catalog
        if m.get("resolvedModel") == model
        and not (m.get("description") or "").startswith("Custom model")
    ]
    if not rows:
        return [f"{model} is not a model Claude Code offers"]
    if any("Requires usage credits" in (m.get("description") or "") for m in rows):
        return [f"{model} requires usage credits, beyond the subscription"]
    return []


def preflight(
    command: list[str], env: dict, cwd: str, folder: Path, expected: dict
) -> dict[str, Any]:
    """How the session is set up, from control() in a configuration directory of its own, so
    that the session starts from an empty one, and each way it is not as the run expects."""
    apart = folder / "preflight"
    (apart / "claude-config").mkdir(parents=True)
    (apart / "claude-tmp").mkdir()
    env = {
        **env,
        "CLAUDE_CONFIG_DIR": str(apart / "claude-config"),
        "CLAUDE_CODE_TMPDIR": str(apart / "claude-tmp"),
    }
    answers = control(command, env, cwd, wait_for_servers=True)
    init, settings = answers["initialize"], answers["get_settings"]
    catalog = init.get("models") or []
    status_rows = rows(answers["get_status"])
    servers = answers["mcp_status"].get("mcpServers") or []
    failures = [f"{subtype}: {a['error']}" for subtype, a in answers.items() if a.get("error")]

    def need(ok: bool, what: str) -> None:
        if not ok:
            failures.append(what)

    if expected["local"]:
        address = status_rows.get("Anthropic base URL")
        need(address == expected["base_url"], f"the API's address is {address}, not Ollama's")
    else:
        failures += credential_failures(answers, expected["base_url"])
        failures += catalog_failures(catalog, expected["model"])
    applied = settings.get("applied") or {}
    need(applied.get("model") == expected["model"], f"the model is {applied.get('model')!r}")
    if not expected["local"]:
        need(
            applied.get("effort") == expected["effort"], f"the effort is {applied.get('effort')!r}"
        )
    need(not applied.get("advisor"), "the advisor is on")
    need(
        not settings.get("sources") and not settings.get("effective"),
        f"settings were read: {settings.get('sources')}",
    )
    need(init.get("fast_mode_state") == "off", f"fast mode is {init.get('fast_mode_state')!r}")
    need(
        init.get("claude_code_version") == expected["release"],
        f"Claude Code is {init.get('claude_code_version')!r}, not {expected['release']}",
    )
    need(
        init.get("current_permission_mode") == "dontAsk",
        f"the permission mode is {init.get('current_permission_mode')!r}",
    )
    commands = {c.get("name") for c in init.get("commands") or []}
    need(client_session.SKILL in commands, f"the skill {client_session.SKILL} is not loaded")
    need(
        [s.get("name") for s in servers] == ["pgnify"],
        f"the MCP servers are {[s.get('name') for s in servers]}, not pgnify's alone",
    )
    if servers:
        server = servers[0]
        config = server.get("config") or {}
        need(server.get("status") == "connected", f"pgnify's server is {server.get('status')}")
        need(
            config.get("command") == expected["server"]["command"]
            and config.get("args") == expected["server"]["args"],
            "pgnify's server is not the pinned one, on this run's book and folder",
        )
        tools = sorted(t.get("name") for t in server.get("tools") or [])
        need(tools == PGNIFY_TOOLS, f"pgnify's tools are {tools}")
    return {
        "account": init.get("account"),
        "status": status_rows,
        "settings": settings,
        "mcp_servers": servers,
        "catalog": [
            {
                key: m.get(key)
                for key in (
                    "value",
                    "resolvedModel",
                    "displayName",
                    "description",
                    "supportedEffortLevels",
                )
            }
            for m in catalog
        ],
        "skill_loaded": client_session.SKILL in commands,
        "failures": failures,
    }


def interactive(
    command: list[str], folder: Path, env: dict, cwd: str, ceiling: float | None
) -> list[dict]:
    """A session the person runs: the request on the clipboard, the fixed reply named in the
    hand-over, the watchdog over it all."""
    session.hand_over(
        folder / "request.md",
        "If the session ends its turn before it has written its PGN, paste this, at most twice:\n"
        f"  {_text('nudge.md').strip()}\n"
        "Exit only once the final answer is in and no subagent is still running.",
    )
    start = time.time()
    process = subprocess.Popen(command, cwd=cwd, env=env)
    stopped = watch(process, folder, start, ceiling, None)
    code = process.wait()
    return [
        {
            "phase": 0,
            "start": start,
            "end": time.time(),
            "exit_code": code,
            "stop": stopped[0] if stopped else None,
            "stopped_by": stopped[1] if stopped else None,
            "cost_before_usd": 0.0,
            "cost_after_usd": round(client_session.cost(folder / "claude-config"), 4),
        }
    ]


def headless(
    command: list[str],
    text: str,
    env: dict,
    cwd: str,
    folder: Path,
    ceiling: float | None,
    expected: dict,
    memory: dict[str, Any] | None,
    workflow: str,
) -> list[dict]:
    """A session run with -p: the request, then the fixed reply through --resume while the
    session ends without its work done, at most twice. A C-map session's work is its map; any
    other session's, a PGN written in it."""
    config_dir, work = folder / "claude-config", folder / "work"
    phases: list[dict] = []
    session_id = None
    since = datetime.now(UTC)
    # A local model's wall limit holds for the whole session, its fixed replies included.
    deadline = time.time() + LOCAL_HOURS * 3600 if expected["local"] else None
    for index in range(1 + NUDGES):
        prompt = text if index == 0 else _text("nudge.md").strip()
        resume = ["--resume", session_id] if index and session_id else []
        run = [
            *command,
            *resume,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-prompts",
            "none",
        ]
        before, start = client_session.cost(config_dir), time.time()
        stream, errors = folder / f"stream-{index}.jsonl", folder / f"stderr-{index}.txt"
        with stream.open("w") as out, errors.open("w") as err:
            process = subprocess.Popen(
                run, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True
            )
            stdin = process.stdin
            assert stdin is not None
            stdin.write(prompt)
            stdin.close()
            stopped = watch(process, folder, start, ceiling, deadline, stream, expected, memory)
            code = process.wait()
        events = _events(stream)
        init = next(
            (e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {}
        )
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        session_id = session_id or init.get("session_id")
        if workflow == "C-map":
            written = bool(_map(_assistant_texts(config_dir), workflow))
        else:
            written = _written_between(work, since)
        phases.append(
            {
                "phase": index,
                "start": start,
                "end": time.time(),
                "exit_code": code,
                "stop": stopped[0] if stopped else None,
                "stopped_by": stopped[1] if stopped else None,
                "init": {
                    k: init.get(k)
                    for k in (
                        "session_id",
                        "model",
                        "apiKeySource",
                        "permissionMode",
                        "claude_code_version",
                        "tools",
                        "mcp_servers",
                        "plugins",
                        "fast_mode_state",
                        "messaging_socket_path",
                    )
                    if k in init
                },
                "result": {
                    k: result.get(k)
                    for k in (
                        "subtype",
                        "is_error",
                        "num_turns",
                        "duration_ms",
                        "total_cost_usd",
                        "result",
                    )
                }
                if result
                else None,
                "written": written,
                "cost_before_usd": round(before, 4),
                "cost_after_usd": round(client_session.cost(config_dir), 4),
            }
        )
        if stopped or written or session_id is None or (result and result.get("is_error")):
            break
    return phases


def watch(
    process: subprocess.Popen,
    folder: Path,
    started: float,
    ceiling: float | None,
    deadline: float | None,
    stream: Path | None = None,
    expected: dict | None = None,
    memory: dict[str, Any] | None = None,
) -> tuple[str, str] | None:
    """Wait for the process; end it when its stream shows it is not the run expected, when its
    cost at list price passes the ceiling, when its logs and stream show no activity for
    IDLE_MINUTES, when the deadline, a time, has passed, or when the person interrupts. What
    ended it and why, if anything did. For a local model, Ollama's memory for it is sampled
    into memory."""
    config_dir = folder / "claude-config"
    offset, checked = 0, 0.0
    stop: tuple[str, str] | None = None
    try:
        while process.poll() is None and stop is None:
            time.sleep(0.5)
            if stream is not None and expected is not None:
                stop, offset = _read_failure(stream, offset, expected)
            if stop is not None or time.time() - checked < 5:
                continue
            checked = time.time()
            activity = [client_session.last_activity(config_dir, started)]
            activity += [s.stat().st_mtime for s in folder.glob("stream-*.jsonl")]
            if time.time() - max(activity) > IDLE_MINUTES * 60:
                stop = ("idle", f"{IDLE_MINUTES} minutes without activity")
            elif ceiling is not None and client_session.cost(config_dir) > ceiling:
                stop = ("ceiling", f"the ${ceiling:.2f} safety ceiling")
            elif deadline is not None and time.time() > deadline:
                stop = ("wall", f"{LOCAL_HOURS:g} hours, its fixed replies included")
            if memory is not None and expected is not None:
                _sample_memory(expected["model"], memory)
        if stop is None and stream is not None and expected is not None:
            # What the process wrote after the last read, with its result.
            stop, offset = _read_failure(stream, offset, expected)
    except KeyboardInterrupt:
        stop = ("interrupted", "the person interrupted it")
    if stop is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
    return stop


def _read_failure(stream: Path, offset: int, expected: dict) -> tuple[tuple[str, str] | None, int]:
    """Why the stream's complete lines from offset show it is not the run expected, if they do, and
    the offset after them."""
    if not stream.exists():
        return None, offset
    with stream.open("rb") as read:
        read.seek(offset)
        data = read.read()
    end = data.rfind(b"\n") + 1
    lines = data[:end].decode(errors="replace").splitlines()
    return _stream_failure(lines, expected), offset + end


def _stream_failure(lines: list[str], expected: dict) -> tuple[str, str] | None:
    """Why a headless session's stream shows that it is not the run expected: a report of its
    setup (init) that differs, an answer from an unexpected model, a fallback to another, or a
    rate limit report that leaves usage beyond the plan possible."""
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind, subtype = event.get("type"), event.get("subtype")
        if kind == "system" and subtype == "init":
            if failures := _init_failures(event, expected):
                return ("preflight_failed", "; ".join(failures))
        elif kind == "system" and subtype in FALLBACKS:
            return ("unexpected_model", f"Claude Code reported {subtype}")
        elif kind == "rate_limit_event":
            if why := overage_failure(event):
                return ("overage_possible", why)
        elif kind == "assistant":
            model = (event.get("message") or {}).get("model")
            if not model or model == "<synthetic>":
                continue
            if event.get("parent_tool_use_id") is None and model != expected["model"]:
                return ("unexpected_model", f"the session answered with {model}")
            if model not in expected["models"]:
                return ("unexpected_model", f"a subagent answered with {model}")
    return None


def overage_failure(event: dict) -> str | None:
    """Why a rate_limit_event leaves usage beyond the plan possible: its overageStatus is present
    and not 'rejected', or isUsingOverage or overageInUse is true. None for any other event, or
    one with none of them."""
    if event.get("type") != "rate_limit_event":
        return None
    info = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return None
    status = info.get("overageStatus")
    if status is not None and status != "rejected":
        return f"overage is {status!r}, not rejected"
    if info.get("isUsingOverage") or info.get("overageInUse"):
        return "usage beyond the plan is in use"
    return None


def _init_failures(init: dict, expected: dict) -> list[str]:
    """How a session's report of its setup differs from what the run expects."""
    failures = []

    def need(ok: bool, what: str) -> None:
        if not ok:
            failures.append(what)

    if not expected["local"]:
        need(
            init.get("apiKeySource") == "none", f"the API key {init.get('apiKeySource')} is in use"
        )
    need(init.get("model") == expected["model"], f"the session's model is {init.get('model')!r}")
    tools = set(init.get("tools") or [])
    pgnify = sorted(
        t.removeprefix(score_slice.TOOL_PREFIX)
        for t in tools
        if t.startswith(score_slice.TOOL_PREFIX)
    )
    others = sorted(
        t for t in tools if not t.startswith(score_slice.TOOL_PREFIX) and t not in AGENT_TOOLS
    )
    need(pgnify == PGNIFY_TOOLS, f"pgnify's tools are {pgnify}")
    need(not others, f"it has other tools: {others}")
    need(bool(tools & {"Agent", "Task"}) and "Skill" in tools, "the Agent or Skill tool is missing")
    servers = [(s.get("name"), s.get("status")) for s in init.get("mcp_servers") or []]
    need(servers == [("pgnify", "connected")], f"its MCP servers are {servers}")
    need(client_session.SKILL in (init.get("skills") or []), "the skill is not loaded")
    plugins = {p.get("name"): p.get("path") for p in init.get("plugins") or []}
    pinned = str(bench_env.plugin_dir())
    need(plugins.get("pgnify") == pinned, f"pgnify's plugin is {plugins.get('pgnify')!r}")
    need(
        all(path in ("builtin", pinned) for path in plugins.values()),
        f"it has other plugins: {plugins}",
    )
    need(init.get("permissionMode") == "dontAsk", f"the mode is {init.get('permissionMode')!r}")
    need(init.get("fast_mode_state", "off") == "off", "fast mode is on")
    need(not init.get("messaging_socket_path"), "it takes messages from other sessions")
    need(
        init.get("claude_code_version") == expected["release"],
        f"Claude Code is {init.get('claude_code_version')!r}",
    )
    return failures


def record(
    folder: Path,
    started: float,
    phases: list[dict],
    token: str | None,
    workflow: str,
    checked: dict[str, Any],
    local: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """session.json: how the session ended, its phases, the models that answered, and whether the
    token is anywhere in its folder, where it is then redacted; and its map, and the games it
    saved but never wrote. The status, and the files that held the token."""
    config_dir, work = folder / "claude-config", folder / "work"
    since = datetime.fromtimestamp(started, UTC)
    unwritten = client_session.unwritten(work)
    if unwritten:
        (folder / "unwritten.pgn").write_text(
            "\n\n".join(t.strip() for _, t in unwritten) + "\n", encoding="utf-8"
        )
    texts = _assistant_texts(config_dir)
    map_text = _map(texts, workflow)
    if map_text:
        (folder / "map.md").write_text(map_text)
    if checked["failures"]:
        status = "preflight_failed"
    else:
        status = _status(
            phases,
            _written_between(work, since),
            _error_texts(config_dir) + _stream_texts(folder),
            workflow,
            map_text,
        )
    nudged = _nudge_times(config_dir)
    if not nudged:
        first_attempt = status
    elif phases and phases[0].get("init") is not None:
        # A headless session: its first phase says what the first attempt did.
        first_attempt = (
            "written"
            if phases[0].get("written")
            else _status(phases[:1], False, [], workflow, None)
        )
    else:
        # An interactive one: what the server wrote before the first reply, to the second.
        first = datetime.fromtimestamp(nudged[0], UTC).replace(microsecond=0)
        first_attempt = (
            "written"
            if _written_between(work, since, first)
            else ("no_map" if workflow == "C-map" else "no_pgn")
        )
    found = token_in(folder, token) if token else []
    for name in found:
        path = folder / name
        path.write_bytes(path.read_bytes().replace(str(token).encode(), b"<redacted>"))
    (folder / "session.json").write_text(
        json.dumps(
            {
                "status": status,
                "first_attempt": first_attempt,
                "nudges": len(nudged),
                "nudge_times": nudged,
                "preflight_failures": checked["failures"],
                "phases": phases,
                "seconds_open": round(time.time() - started, 1),
                "seconds_to_final_answer": client_session.to_final_answer(folder),
                "cost_usd_at_list": None
                if local is not None
                else round(client_session.cost(config_dir), 4),
                "models": _models(config_dir),
                "local_model": local,
                "unwritten_games": [n for n, _ in unwritten],
                "credential_found_in": found,
            },
            indent=1,
        )
    )
    print(
        f"\n{folder}\nstatus: {status}"
        + (
            f"; the token was in {len(found)} files, now redacted: see session.json"
            if found
            else ""
        )
    )
    return status, found


def _status(
    phases: list[dict], written: bool, texts: list[str], workflow: str, map_text: str | None
) -> str:
    """How a session ended. Only a session that wrote its PGN (or, for C-map, its map) and ended
    without an error is 'written'; every other end has its own name."""
    stops = [p.get("stop") for p in phases if p.get("stop")]
    for kind in ("preflight_failed", "unexpected_model", "overage_possible", "interrupted"):
        if kind in stops:
            return kind
    if stops:
        return "stopped"
    done = bool(map_text) if workflow == "C-map" else written
    last = phases[-1] if phases else {}
    errored = bool(last.get("result") and last["result"].get("is_error"))
    if done and not errored:
        return "written"
    if named := failure(texts):
        return named
    if last.get("exit_code") not in (0, None) or (
        last and last.get("result") is None and last.get("init") is not None
    ):
        return "interrupted"
    return "no_map" if workflow == "C-map" else "no_pgn"


def failure(texts: list[str]) -> str | None:
    """The failure the texts name, of FAILURES, if any."""
    for name, pattern in FAILURES:
        if any(pattern.search(t) for t in texts):
            return name
    return None


def _written_between(work: Path, since: datetime, before: datetime | None = None) -> bool:
    """Whether the server wrote a PGN file at or after since, and before before if it is given."""
    for entry in read_records(work / ".pgnify").values():
        written = entry.get("written")
        if not written:
            continue
        when = datetime.fromisoformat(written)
        if when >= since.replace(microsecond=0) and (before is None or when < before):
            return True
    return False


def _nudge_times(config_dir: Path) -> list[float]:
    """When the main conversation was sent the fixed reply, as Unix times, in order."""
    nudge = _text("nudge.md").strip()
    times = []
    for log in sorted((config_dir / "projects").glob("*/*.jsonl")):
        for line in log.read_text(errors="replace").splitlines():
            if nudge[:40] not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (entry.get("message") or {}).get("content")
            text = (
                content
                if isinstance(content, str)
                else " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict))
            )
            if entry.get("type") == "user" and text.strip() == nudge and entry.get("timestamp"):
                times.append(datetime.fromisoformat(entry["timestamp"]).timestamp())
    return sorted(set(times))


def _events(stream: Path) -> list[dict]:
    found = []
    for line in stream.read_text(errors="replace").splitlines():
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return found


def _stream_texts(folder: Path) -> list[str]:
    """The texts of a headless session's results and errors, where failures are worded."""
    texts = []
    for stream in sorted(folder.glob("stream-*.jsonl")):
        for event in _events(stream):
            if event.get("type") == "result" and event.get("is_error"):
                texts.append(str(event.get("result") or event.get("subtype")))
    for errors in sorted(folder.glob("stderr-*.txt")):
        texts.append(errors.read_text(errors="replace"))
    return texts


def _assistant_texts(config_dir: Path) -> list[str]:
    """The main conversation's texts, in order, from its log."""
    texts = []
    for log in sorted((config_dir / "projects").glob("*/*.jsonl")):
        for line in log.read_text(errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant" or entry.get("isSidechain"):
                continue
            for block in (entry.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block["text"])
    return texts


def _error_texts(config_dir: Path) -> list[str]:
    """The messages Claude Code itself wrote into the logs for an API error, not the model's:
    a failure is read from these alone, so that a model's own words never name one."""
    texts = []
    for log in (config_dir / "projects").glob("**/*.jsonl"):
        for line in log.read_text(errors="replace").splitlines():
            if "isApiErrorMessage" not in line and "<synthetic>" not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message") or {}
            if entry.get("isApiErrorMessage") or message.get("model") == "<synthetic>":
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block["text"])
    return texts


def _map(texts: list[str], workflow: str) -> str | None:
    """The book map a session wrote: C-map's final answer, or the first message that holds one."""
    maps = [t for t in texts if "# Book map" in t]
    if workflow == "C-map":
        return texts[-1] if texts and "# Book map" in texts[-1] else (maps[-1] if maps else None)
    return maps[0] if maps else None


def _models(config_dir: Path) -> dict[str, list[str]]:
    """The exact models that answered, the main conversation's and each subagent's, from the
    logs."""
    found: dict[str, set[str]] = {}
    for log in (config_dir / "projects").glob("**/*.jsonl"):
        agent = "main" if log.parent.name != "subagents" else log.stem
        for line in log.read_text(errors="replace").splitlines():
            if '"model"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            model = (entry.get("message") or {}).get("model")
            if entry.get("type") == "assistant" and model and model != "<synthetic>":
                found.setdefault(agent, set()).add(model)
    return {agent: sorted(models) for agent, models in sorted(found.items())}


def _ollama(path: str, payload: dict | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        OLLAMA["ANTHROPIC_BASE_URL"] + path, data=data, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _ollama_model(name: str) -> dict[str, Any]:
    """A local model as Ollama has it: its digest, format and quantization, its context length
    as made and as set (num_ctx), its parameters, and Ollama's version."""
    shown = _ollama("/api/show", {"model": name})
    listed = next(
        (m for m in _ollama("/api/tags").get("models") or [] if m.get("name") == name), {}
    )
    info = shown.get("model_info") or {}
    return {
        "name": name,
        "digest": listed.get("digest"),
        "size_bytes": listed.get("size"),
        "details": shown.get("details"),
        "context_length_trained": next(
            (v for k, v in info.items() if k.endswith(".context_length")), None
        ),
        "parameters": shown.get("parameters"),
        "capabilities": shown.get("capabilities"),
        "modified_at": shown.get("modified_at"),
        "ollama_version": _ollama("/api/version").get("version"),
    }


def _sample_memory(name: str, memory: dict[str, Any]) -> None:
    """Ollama's memory for the model now, kept as the largest seen, with how often it was
    sampled and how often the model was loaded on the GPU in full."""
    try:
        running = _ollama("/api/ps").get("models") or []
    except OSError:
        return
    for model in running:
        if model.get("name") != name and model.get("model") != name:
            continue
        memory["samples"] = memory.get("samples", 0) + 1
        memory["peak_bytes"] = max(memory.get("peak_bytes", 0), model.get("size") or 0)
        memory["peak_vram_bytes"] = max(
            memory.get("peak_vram_bytes", 0), model.get("size_vram") or 0
        )
        memory["context_length"] = model.get("context_length")
        memory["all_on_gpu"] = memory.get("all_on_gpu", 0) + (
            model.get("size") == model.get("size_vram")
        )


def rows(status: dict[str, Any]) -> dict[str, Any]:
    """get_status's rows, by label."""
    return {
        row.get("label"): row.get("value")
        for section in status.get("sections") or []
        for row in section.get("rows") or []
    }


def token_in(folder: Path, token: str) -> list[str]:
    """The files under folder that hold the token, by path; the token is never printed."""
    needle = token.encode()
    return [
        str(path.relative_to(folder))
        for path in folder.rglob("*")
        if path.is_file() and not path.is_symlink() and needle in path.read_bytes()
    ]


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    main()
