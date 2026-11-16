"""Check, before a stage of the workflow benchmark, that Claude Code takes the subscription token
as its credential, that a bad token is refused and named as an authentication failure, and that
each hosted model answers as itself: first Claude Code's own account of its credential and API,
asked with no prompt and so no call to a model, then one short request per model, with the pinned
Claude Code in restricted mode, no tools and no server, at low effort. Stops at the first check
that fails. Records, for each request, the authentication Claude Code used (apiKeySource: "none"
is the subscription's OAuth token, never an API key), the model the session was started with and
the exact model that answered, how the request ended, and its cost at list price. The token comes
from CLAUDE_CODE_OAUTH_TOKEN in this process's environment (as bench_batch.py passes it), goes to
Claude Code alone, and is never written; the output folder is searched for it afterwards, and
any file that holds it is redacted.

    uv run python experiments/bench_models.py [MODEL...]

Writes experiments/results/bench/models/<timestamp>.json and its folder, with the overage fields
each request's stream reported. Exits 2 when a check fails: a wrong credential, an authentication
failure not named, a model that answers as another, an error, or overage_possible (a rate limit
report whose overageStatus is not "rejected", or whose isUsingOverage or overageInUse is true).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import bench_env
import bench_session
import client_session
import session

PROMPT = "Reply with the single word: ready"
BAD_TOKEN = "sk-ant-oat01-not-a-real-token-for-the-refusal-check"


def environment(token: str, folder: Path) -> dict[str, str]:
    """A probe's environment, as bench_session.py makes a headless session's."""
    (folder / "claude-config").mkdir(parents=True)
    (folder / "claude-tmp").mkdir()
    env = session.child_environment(
        bench_session.SETTINGS, folder / "claude-config", drop=("VIRTUAL_ENV", "UV_")
    )
    env["CLAUDE_CODE_TMPDIR"] = str(folder / "claude-tmp")
    env[bench_session.TOKEN] = token
    return env


def command(model: str) -> list[str]:
    return [
        str(bench_env.claude_command()),
        "--model",
        model,
        "--effort",
        "low",
        "--restricted",
        "--tools",
        "",
        "--strict-mcp-config",
    ]


def account(token: str, folder: Path) -> dict[str, Any]:
    """Claude Code's account of its credential and API, with no prompt sent."""
    env = environment(token, folder / "account")
    with tempfile.TemporaryDirectory(prefix="pgnify-models-") as cwd:
        answers = bench_session.control(command(bench_session.MODELS[0]), env, cwd, False)
    failures = [f"{subtype}: {a['error']}" for subtype, a in answers.items() if a.get("error")]
    failures += bench_session.credential_failures(answers, env.get("ANTHROPIC_BASE_URL"))
    if answers["get_settings"].get("sources"):
        failures.append(f"settings were read: {answers['get_settings']['sources']}")
    return {
        "account": answers["initialize"].get("account"),
        "status": bench_session.rows(answers["get_status"]),
        "failures": failures,
    }


def ask(model: str, token: str, folder: Path) -> dict[str, Any]:
    env = environment(token, folder)
    run = [
        *command(model),
        "-p",
        PROMPT,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-prompts",
        "none",
    ]
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="pgnify-models-") as cwd:
        done = subprocess.run(run, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    events = []
    for line in done.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    said = [(e.get("message") or {}).get("model") for e in events if e.get("type") == "assistant"]
    answered = sorted({name for name in said if name and name != "<synthetic>"})
    overage = [why for e in events if (why := bench_session.overage_failure(e))]
    reports = [e for e in events if e.get("type") == "rate_limit_event"]
    seen = [
        {
            k: (e.get("rate_limit_info") or {}).get(k)
            for k in ("overageStatus", "isUsingOverage", "overageInUse", "overageDisabledReason")
        }
        for e in reports
    ]
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    texts = [str(result.get("result") or ""), done.stderr]
    return {
        "requested": model,
        "api_key_source": init.get("apiKeySource"),
        "session_model": init.get("model"),
        "messaging_socket_path": init.get("messaging_socket_path"),
        "answered_by": answered,
        "overage": sorted(set(overage)),
        "overage_reported": seen or "no rate_limit_event",
        "is_error": result.get("is_error"),
        "failure": bench_session.failure(texts)
        if result.get("is_error") or done.returncode
        else None,
        "result": (result.get("result") or "")[:200],
        "exit_code": done.returncode,
        "stderr": done.stderr[-300:],
        "seconds": round(time.time() - started, 1),
        "cost_usd_at_list": round(client_session.cost(folder / "claude-config"), 5),
    }


def failures_of(check: dict[str, Any]) -> list[str]:
    """The reasons a check failed: the API key in use, a session or answering model other than the
    one requested, an error, overage possible, or messages from other sessions."""
    found = []
    if check["api_key_source"] != "none":
        found.append(f"the API key {check['api_key_source']} was in use")
    if check["session_model"] != check["requested"]:
        found.append(f"the session's model was {check['session_model']}")
    if check["answered_by"] != [check["requested"]]:
        found.append(f"it was answered by {check['answered_by']}")
    if check["is_error"] or check["exit_code"]:
        found.append(f"it ended in an error ({check['failure']}): {check['result'][:100]}")
    if check["overage"]:
        found.append(f"overage_possible: {'; '.join(check['overage'])}")
    if check["messaging_socket_path"]:
        found.append("it took messages from other sessions")
    return found


def main() -> None:
    token = os.environ.get(bench_session.TOKEN)
    if not token:
        sys.exit(f"{bench_session.TOKEN} is not set: run this through bench_batch.py")
    if differences := bench_env.verify():
        sys.exit("the pinned environment differs from its manifest:\n" + "\n".join(differences))
    models = sys.argv[1:] or bench_session.MODELS
    folder = bench_session.RESULTS / "models" / datetime.now().strftime("%Y%m%dT%H%M%S")
    folder.mkdir(parents=True)
    report: dict[str, Any] = {
        "claude": json.loads(bench_env.MANIFEST.read_text())["environment"]["claude"]["release"],
        "checks": [],
    }
    failed: list[str] = []
    report["account"] = account(token, folder)
    failed += [f"account: {f}" for f in report["account"]["failures"]]
    if not failed:
        refused = ask(models[0], BAD_TOKEN, folder / "bad-token")
        report["bad_token"] = refused
        if refused["failure"] != "auth_error":
            failed.append(
                f"bad token: not named an authentication failure ({refused['failure']}): "
                f"{refused['result'][:100]}"
            )
    for model in models:
        if failed:
            break
        check = ask(model, token, folder / model)
        check["failures"] = failures_of(check)
        report["checks"].append(check)
        failed += [f"{model}: {f}" for f in check["failures"]]
    found = bench_session.token_in(folder, token)
    for name in found:
        path = folder / name
        path.write_bytes(path.read_bytes().replace(token.encode(), b"<redacted>"))
    if found:
        failed.append(f"the token was in {len(found)} files, now redacted")
    report["credential_found_in"] = found
    report["total_cost_usd_at_list"] = round(
        sum(c["cost_usd_at_list"] for c in report["checks"])
        + report.get("bad_token", {}).get("cost_usd_at_list", 0.0),
        5,
    )
    report["failures"] = failed
    folder.with_suffix(".json").write_text(json.dumps(report, indent=1))
    print(f"account: {report['account']['account']}")
    if "bad_token" in report:
        print(f"bad token: {report['bad_token']['failure'] or 'not refused'}")
    for check in report["checks"]:
        print(
            f"{check['requested']}: {'ok' if not check['failures'] else 'failed'} | auth "
            f"{check['api_key_source']} | answered by {check['answered_by']} "
            f"| ${check['cost_usd_at_list']}"
        )
    print("\n".join(failed) or "every check passed")
    print(f"token in files: {found or 'none'}; written {folder.with_suffix('.json')}")
    sys.exit(2 if failed else 0)


if __name__ == "__main__":
    main()
