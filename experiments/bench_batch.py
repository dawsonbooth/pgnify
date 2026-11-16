"""Run a batch of the workflow benchmark's sessions (experiments/bench_session.py) headless on the
person's Claude subscription, from a plan, within a budget at list price, one session at a time.

    caffeinate -ims uv run python experiments/bench_batch.py PLAN.toml [PLAN.toml ...]

Several plans run one after another in the order given, each exactly as it would alone, with the
token asked for once and held only in memory. Every plan is checked before the first is run. The
next plan starts only when the one before ended with exit code 0 (a budget stop included); any
other end stops the invocation with that code, and says which plans were not run.

The plan names the slice, the budget, the part of it held back, the models to check first, and
the runs in order; a run takes its map from, or reviews, an earlier run of the plan by name,
or takes a map file (map_file, from the repository's root, as C-verified does), or reviews the
run folder of an earlier batch (review_of_folder, from the repository's root, under the slice's
folder in experiments/results/bench/, with PGN in its work/ folder, in place of review_of):

    slice = "london12"
    budget_usd = 10.0
    reserve_usd = 1.5
    check_models = ["claude-haiku-5-5", "claude-sonnet-5-5"]
    scorer = "0123456789abcdef"   # optional: score_slice.fingerprint(), frozen for a stage

    [[run]]
    name = "C-map"
    workflow = "C-map"
    model = "claude-opus-5-5"
    effort = "high"
    ceiling_usd = 1.0

    [[run]]
    name = "C"
    workflow = "C"
    model = "claude-haiku-5-5"
    effort = "high"
    ceiling_usd = 0.5
    map = "C-map"

    [[run]]
    name = "R-earlier"
    workflow = "R"
    model = "claude-opus-5-5"
    effort = "high"
    ceiling_usd = 1.0
    review_of_folder = "experiments/results/bench/london12/A/claude-sonnet-5-5-high/20261112T172717"

Refuses a plan whose slice's reference is not frozen (its digests beside it, matching), or whose
scorer differs from the fingerprint the plan gives. Asks for the token `claude setup-token` printed,
without showing it, so that it is in no file, no shell history and no command line, and refuses one
that is not a subscription token. Checks, before any model is called, that no other benchmark
session is running, that the computer is on mains power or that the person agrees to go on, the
pinned environment, and that no managed settings could set Claude Code's credentials; then the
account and the models (bench_models.py), whose cost counts against the budget. Then runs each
session with a ceiling of the smaller of its own and what is left of the budget once the reserve is
held back, the reserve covering a watchdog's overshoot; a run whose map or review has nothing to
work on is skipped. Stops at once, with nothing more run, when a check fails, when a session ends in
a way the benchmark does not measure (a failed preflight, an unexpected model, overage possible, an
authentication failure, a usage or rate limit, an API error, an interrupted session, the token found
in a file), or when the budget left is under $0.25. Scores what the sessions wrote (score_slice.py,
score_map.py) and tabulates the slice (compare.py), without the token.

Writes experiments/results/bench/<slice>/batches/<timestamp>.json: the plan and its hash, each
check and run with its folder, status, ceiling and cost at list price, the total, and why the
batch ended.
"""

import getpass
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

import bench_env
import bench_session
import client_session
import score_slice
import session

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "experiments"
# Settings an administrator can install, which apply even in restricted mode.
MANAGED = [
    Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
    Path("/Library/Application Support/ClaudeCode/managed-settings.d"),
    Path("/Library/Application Support/ClaudeCode/managed-mcp.json"),
]
# Claude Code's own test of a subscription (OAuth) token, and an API key's prefix.
OAUTH_PREFIX, API_KEY_PREFIX = "sk-ant-oat", "sk-ant-api"
MINIMUM_USD = 0.25
# A benchmark session's command line, as pgrep finds it running.
SESSIONS = "experiments/bench_session.py"


class Token:
    """The subscription token, asked for at most once per invocation and held only in memory."""

    def __init__(self) -> None:
        self.value: str | None = None

    def ask(self) -> str:
        if self.value is None:
            self.value = getpass.getpass("Token from `claude setup-token` (not shown): ").strip()
        return self.value


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: bench_batch.py PLAN.toml [PLAN.toml ...]")
    plan_paths = [Path(arg) for arg in sys.argv[1:]]
    texts = [path.read_bytes() for path in plan_paths]
    plans = [tomllib.loads(text.decode()) for text in texts]
    problems = {str(path): check_plan(plan) for path, plan in zip(plan_paths, plans, strict=True)}
    if any(problems.values()):
        if len(plans) == 1:
            sys.exit("the plan:\n- " + "\n- ".join(problems[str(plan_paths[0])]))
        sys.exit(
            "\n".join(
                f"{name}:\n- " + "\n- ".join(found) if found else f"{name}: no problems"
                for name, found in problems.items()
            )
        )
    token = Token()
    for number, (plan_path, plan) in enumerate(zip(plan_paths, plans, strict=True)):
        if len(plans) > 1:
            print(f"\nPlan {number + 1} of {len(plans)}: {plan_path}")
        code = run_plan(plan_path, plan, token, hashlib.sha256(texts[number]).hexdigest())
        if code != 0:
            if later := plan_paths[number + 1 :]:
                print(f"\nNot run: {', '.join(str(path) for path in later)}")
            sys.exit(code)


def run_plan(plan_path: Path, plan: dict[str, Any], token: Token, parsed_sha256: str) -> int:
    """Run one checked plan; the exit code it ended with. `parsed_sha256` is the SHA-256 of the
    bytes main() parsed. A plan after the first starts long after main() checked them all, so its
    file and its checks are read again at its start."""
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    text = plan_path.read_bytes()
    record: dict[str, Any] = {
        "plan": str(plan_path),
        "plan_sha256": hashlib.sha256(text).hexdigest(),
        "budget_usd": plan["budget_usd"],
        "reserve_usd": plan["reserve_usd"],
        "started": stamp,
        "checks": {},
        "runs": [],
    }
    batch = bench_session.RESULTS / plan["slice"] / "batches" / f"{stamp}.json"

    def finish(ended: str, code: int) -> int:
        record["ended"] = ended
        record["spent_usd_at_list"] = round(spent(record), 4)
        batch.parent.mkdir(parents=True, exist_ok=True)
        batch.write_text(json.dumps(record, indent=1))
        print(
            f"\n{ended}\nspent at list price: ${record['spent_usd_at_list']:.2f} of "
            f"${plan['budget_usd']:.2f}; written {batch}"
        )
        return code

    problems = []
    if record["plan_sha256"] != parsed_sha256:
        problems.append("the plan's file changed after it was checked")
    try:
        problems += check_plan(tomllib.loads(text.decode()))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        problems.append(f"the plan's file no longer reads: {error}")
    if problems:
        return finish("stopped: the plan no longer passes its checks: " + "; ".join(problems), 2)
    running = subprocess.run(
        ["pgrep", "-f", SESSIONS], capture_output=True, text=True
    ).stdout.split()
    if running:
        record["checks"]["sessions_running"] = running
        return finish("stopped: another benchmark session is running; one runs at a time", 1)
    power = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    record["checks"]["power"] = power.splitlines()[0] if power else None
    if "Battery Power" in power and input("On battery power: go on? [y/N] ").lower() != "y":
        return finish("stopped: on battery power", 1)
    if differences := bench_env.verify():
        record["checks"]["environment"] = differences
        return finish("stopped: the pinned environment differs from its manifest", 2)
    if managed := [str(p) for p in MANAGED if p.exists()]:
        record["checks"]["managed_settings"] = managed
        return finish("stopped: managed settings are installed, which could set credentials", 2)
    passed_over = sorted(n for n in os.environ if n.startswith(("ANTHROPIC", "CLAUDE")))
    record["checks"]["variables_not_passed"] = passed_over
    if passed_over:
        print(f"Not passed to the sessions: {', '.join(passed_over)}")

    secret = token.ask()
    if secret.startswith(API_KEY_PREFIX):
        return finish("stopped: that is an API key, not a subscription token", 2)
    if not secret.startswith(OAUTH_PREFIX) or any(c.isspace() for c in secret):
        return finish(f"stopped: a subscription token starts {OAUTH_PREFIX} and has no spaces", 2)
    with_token = {**os.environ, bench_session.TOKEN: secret}
    without_token = {k: v for k, v in os.environ.items() if k != bench_session.TOKEN}

    before = set((bench_session.RESULTS / "models").glob("*.json"))
    print("\nChecking the account and the models")
    checked = wait(
        [sys.executable, str(EXPERIMENTS / "bench_models.py"), *plan["check_models"]], with_token
    )
    report = next(iter(set((bench_session.RESULTS / "models").glob("*.json")) - before), None)
    record["checks"]["models"] = {
        "report": str(report) if report else None,
        "exit_code": checked,
        "cost_usd_at_list": load(report).get("total_cost_usd_at_list", 0.0) if report else 0.0,
    }
    if checked is None:
        return finish("stopped: interrupted by the person", 130)
    if checked != 0:
        return finish("stopped: the account or model check failed", 2)

    folders: dict[str, Path] = {}
    ended, code = "every run of the plan is done", 0
    for run in plan["run"]:
        entry: dict[str, Any] = {
            "name": run["name"],
            "workflow": run["workflow"],
            "model": run["model"],
            "effort": run["effort"],
        }
        if "review_of_folder" in run:
            entry["review_of_folder"] = str((ROOT / run["review_of_folder"]).resolve())
        record["runs"].append(entry)
        arguments = [run["workflow"], "--model", run["model"], "--effort", run["effort"]]
        if "map" in run:
            given = folders.get(run["map"])
            if given is None or not (given / "map.md").exists():
                entry["status"] = f"skipped: {run['map']} wrote no map"
                continue
            arguments += ["--map", str(given)]
        if "map_file" in run:
            arguments += ["--map", str(ROOT / run["map_file"])]
        if "review_of" in run:
            given = folders.get(run["review_of"])
            if given is None or not list((given / "work").glob("*.pgn")):
                entry["status"] = f"skipped: {run['review_of']} wrote no PGN"
                continue
            arguments += ["--review-of", str(given)]
        if "review_of_folder" in run:
            arguments += ["--review-of", entry["review_of_folder"]]
        if "subagent_model" in run:
            arguments += ["--subagent-model", run["subagent_model"]]
        if "label" in run:
            arguments += ["--label", run["label"]]
        left = plan["budget_usd"] - plan["reserve_usd"] - spent(record)
        if left < MINIMUM_USD:
            entry["status"] = "not run: the budget is spent"
            ended = f"stopped: under ${MINIMUM_USD:.2f} of the budget is left past the reserve"
            break
        ceiling = round(min(run["ceiling_usd"], left), 2)
        entry["ceiling_usd"] = ceiling
        print(
            f"\n{run['name']}: {run['workflow']} on {run['model']} at {run['effort']}, "
            f"ceiling ${ceiling:.2f}"
        )
        started = datetime.now().strftime("%Y%m%dT%H%M%S")
        command = [
            sys.executable,
            str(EXPERIMENTS / "bench_session.py"),
            plan["slice"],
            *arguments,
            "--headless",
            "--ceiling",
            f"{ceiling:.2f}",
        ]
        exit_code = wait(command, with_token)
        folder = newest(plan["slice"], run["workflow"], started)
        entry["folder"] = str(folder) if folder else None
        entry["exit_code"] = exit_code
        if folder is not None:
            folders[run["name"]] = folder
            entry["cost_usd_at_list"] = round(client_session.cost(folder / "claude-config"), 4)
            outcome = load(folder / "session.json")
            entry["status"] = outcome.get("status", "no record")
            entry["first_attempt"] = outcome.get("first_attempt")
            entry["seconds"] = outcome.get("seconds_to_final_answer")
            entry["credential_found_in"] = outcome.get("credential_found_in")
        if exit_code is None:
            return finish("stopped: interrupted by the person", 130)
        if exit_code != 0:
            ended, code = f"stopped: {run['name']} ended {entry.get('status')}", 2
            break

    for entry in record["runs"]:
        folder = Path(entry["folder"]) if entry.get("folder") else None
        if folder is None:
            continue
        if entry["workflow"] == "C-map" and (folder / "map.md").exists():
            facts = ROOT / "data" / "local" / "truth" / f"{plan['slice']}.map-facts.json"
            scorer = [str(EXPERIMENTS / "score_map.py"), plan["slice"], str(folder / "map.md")]
            scorer += [str(facts)] if facts.exists() else []
        elif list((folder / "work").glob("*.pgn")):
            scorer = [str(EXPERIMENTS / "score_slice.py"), plan["slice"], str(folder)]
        else:
            continue
        scored = subprocess.run(
            [sys.executable, *scorer], cwd=ROOT, env=without_token, capture_output=True, text=True
        )
        entry["score_exit_code"] = scored.returncode
        if scored.returncode:
            print(f"{' '.join(scorer)} exited {scored.returncode}:\n{scored.stdout}{scored.stderr}")
    subprocess.run(
        [sys.executable, str(EXPERIMENTS / "compare.py"), plan["slice"]],
        cwd=ROOT,
        env=without_token,
    )
    return finish(ended, code)


def check_plan(plan: dict[str, Any]) -> list[str]:
    """What is wrong with a plan, if anything."""
    problems = []
    if plan.get("slice") not in session.slices()["slices"]:
        problems.append(f"no slice {plan.get('slice')!r} in experiments/slices.toml")
    for key in ("budget_usd", "reserve_usd"):
        if not isinstance(plan.get(key), (int, float)) or plan[key] < 0:
            problems.append(f"{key} must be a number of dollars")
    for model in plan.get("check_models", []):
        if model not in bench_session.MODELS:
            problems.append(f"{model} is not a model the benchmark runs")
    names: set[str] = set()
    for run in plan.get("run", []):
        for key in ("name", "workflow", "model", "effort", "ceiling_usd"):
            if key not in run:
                problems.append(f"a run has no {key}: {run}")
        if run.get("workflow") not in bench_session.CHOICES:
            problems.append(f"{run.get('name')}: no workflow {run.get('workflow')!r}")
        if run.get("model") not in bench_session.MODELS:
            problems.append(f"{run.get('name')}: {run.get('model')} is not a hosted model")
        for key in ("map", "review_of"):
            if key in run and run[key] not in names:
                problems.append(f"{run.get('name')}: {key} names no earlier run, {run[key]!r}")
        problems += review_folder(plan, run)
        if run.get("name") in names:
            problems.append(f"two runs are named {run.get('name')!r}")
        names.add(run.get("name"))
    if not plan.get("run"):
        problems.append("the plan has no runs")
    if plan.get("slice") in session.slices()["slices"]:
        problems += frozen(session.slices()["slices"][plan["slice"]]["truth"])
    if "scorer" in plan and plan["scorer"] != score_slice.fingerprint():
        problems.append(
            f"the scorer is {score_slice.fingerprint()}, not the {plan['scorer']} the plan froze"
        )
    return problems


def review_folder(plan: dict[str, Any], run: dict[str, Any]) -> list[str]:
    """What is wrong with a run's review_of_folder, if it gives one: given beside review_of, on a
    workflow other than R, an absolute path, not a folder under the slice's in
    experiments/results/bench/, or without PGN in its work/ folder."""
    if "review_of_folder" not in run:
        return []
    name, given = run.get("name"), run["review_of_folder"]
    if "review_of" in run:
        return [f"{name}: review_of and review_of_folder are both given"]
    if run.get("workflow") != "R":
        return [f"{name}: review_of_folder is for a review (R), not {run.get('workflow')!r}"]
    if not isinstance(given, str):
        return [f"{name}: review_of_folder must be a path from the repository's root"]
    if Path(given).is_absolute():
        return [f"{name}: review_of_folder {given!r} must be relative to the repository's root"]
    folder = (ROOT / given).resolve()
    if not folder.is_relative_to((bench_session.RESULTS / str(plan.get("slice"))).resolve()):
        return [f"{name}: review_of_folder {given!r} is not under the slice's results folder"]
    if not folder.is_dir():
        return [f"{name}: review_of_folder {given!r} is not a folder"]
    if not list((folder / "work").glob("*.pgn")):
        return [f"{name}: review_of_folder {given!r} has no work/*.pgn"]
    return []


def frozen(truth: str) -> list[str]:
    """What keeps a slice's reference from counting as frozen: no file, no digests beside it,
    or a file that differs from its digest."""
    path = ROOT / truth
    digests = path.with_suffix(".sha256")
    if not path.exists() or not digests.exists():
        return [f"the reference {truth} is not frozen"]
    listed = {}
    for line in digests.read_text().splitlines():
        digest, _, name = line.partition("  ")
        listed[name] = digest
    if listed.get(path.name) != hashlib.sha256(path.read_bytes()).hexdigest():
        return [f"the reference {truth} differs from its frozen digest"]
    return []


def wait(command: list[str], env: dict[str, str]) -> int | None:
    """Run a command to its end; None if the person interrupted it, once it has stopped, so
    that a session has recorded itself."""
    process = subprocess.Popen(command, cwd=ROOT, env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        try:
            process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()
        return None


def newest(slice_name: str, workflow: str, started: str) -> Path | None:
    """The run folder of the slice's workflow made at or after started, the latest of them."""
    found = sorted(
        (p.name, p)
        for p in (bench_session.RESULTS / slice_name / workflow).glob("*/*")
        if p.is_dir() and p.name >= started
    )
    return found[-1][1] if found else None


def spent(record: dict[str, Any]) -> float:
    """The batch's cost at list price so far: the model checks' and each run's, from its logs."""
    total = (record["checks"].get("models") or {}).get("cost_usd_at_list", 0.0)
    for entry in record["runs"]:
        if entry.get("folder"):
            total += client_session.cost(Path(entry["folder"]) / "claude-config")
    return total


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


if __name__ == "__main__":
    main()
