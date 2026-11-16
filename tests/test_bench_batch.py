"""Tests of how a benchmark plan names the run folder of an earlier batch to review
(experiments/bench_batch.py, review_of_folder). They need a checkout: the experiments are not in
the sdist."""

import hashlib
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from support import ROOT

if not (ROOT / "experiments").is_dir():
    pytest.skip("the experiments are not in this tree", allow_module_level=True)
sys.path.insert(0, str(ROOT / "experiments"))
import bench_batch  # ty: ignore[unresolved-import]
import bench_session  # ty: ignore[unresolved-import]

SLICE = "endgame-course-4"
EARLIER = f"experiments/results/bench/{SLICE}/A/claude-sonnet-5-5-high/20261115T160612"


@pytest.fixture
def results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository root in tmp_path, with an earlier run that wrote a PGN, and a reference
    taken as frozen."""
    monkeypatch.setattr(bench_batch, "ROOT", tmp_path)
    monkeypatch.setattr(bench_session, "RESULTS", tmp_path / "experiments" / "results" / "bench")
    monkeypatch.setattr(bench_batch, "frozen", lambda truth: [])
    work = tmp_path / EARLIER / "work"
    work.mkdir(parents=True)
    (work / "book.pgn").write_text("1. e4 *\n")
    return tmp_path


def plan_with(**review: Any) -> dict[str, Any]:
    run = {
        "name": "R-earlier",
        "workflow": "R",
        "model": "claude-opus-5-5",
        "effort": "high",
        "ceiling_usd": 1.0,
        **review,
    }
    return {
        "slice": SLICE,
        "budget_usd": 10.0,
        "reserve_usd": 1.5,
        "check_models": ["claude-opus-5-5"],
        "run": [run],
    }


def test_a_folder_of_an_earlier_batch_is_accepted(results: Path) -> None:
    assert bench_batch.check_plan(plan_with(review_of_folder=EARLIER)) == []


def test_review_of_and_review_of_folder_together_are_refused(results: Path) -> None:
    plan = plan_with(review_of="earlier", review_of_folder=EARLIER)
    plan["run"].insert(0, {**plan["run"][0], "name": "earlier", "workflow": "A"})
    for key in ("review_of", "review_of_folder"):
        del plan["run"][0][key]
    problems = bench_batch.check_plan(plan)
    assert any("both given" in problem for problem in problems)


def test_review_of_folder_on_a_workflow_that_is_not_a_review_is_refused(results: Path) -> None:
    problems = bench_batch.check_plan(plan_with(workflow="A", review_of_folder=EARLIER))
    assert any("for a review (R)" in problem for problem in problems)


def test_a_folder_that_does_not_exist_is_refused(results: Path) -> None:
    problems = bench_batch.check_plan(plan_with(review_of_folder=EARLIER.replace("2026", "2027")))
    assert any("not a folder" in problem for problem in problems)


def test_a_folder_without_pgn_is_refused(results: Path) -> None:
    (results / EARLIER / "work" / "book.pgn").unlink()
    problems = bench_batch.check_plan(plan_with(review_of_folder=EARLIER))
    assert any("no work/*.pgn" in problem for problem in problems)


@pytest.mark.parametrize(
    "elsewhere",
    [
        "experiments/results/bench/london12/A/m/stamp",
        f"experiments/results/bench/{SLICE}/../london12/A/m/stamp",
        "experiments/results/bench",
    ],
)
def test_a_folder_outside_the_slices_results_is_refused(results: Path, elsewhere: str) -> None:
    (results / elsewhere / "work").mkdir(parents=True, exist_ok=True)
    (results / elsewhere / "work" / "book.pgn").write_text("1. e4 *\n")
    problems = bench_batch.check_plan(plan_with(review_of_folder=elsewhere))
    assert any("not under the slice's results folder" in problem for problem in problems)


def test_an_absolute_path_is_refused(results: Path) -> None:
    problems = bench_batch.check_plan(plan_with(review_of_folder=str(results / EARLIER)))
    assert any("must be relative" in problem for problem in problems)


def test_the_run_loop_passes_the_folders_absolute_path(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stub in place of every process and the token: no session starts."""
    plan_path = results / "plan.toml"
    plan_path.write_text(
        f'slice = "{SLICE}"\nbudget_usd = 10.0\nreserve_usd = 1.5\n'
        'check_models = ["claude-opus-5-5"]\n'
        '[[run]]\nname = "R-earlier"\nworkflow = "R"\nmodel = "claude-opus-5-5"\n'
        f'effort = "high"\nceiling_usd = 1.0\nreview_of_folder = "{EARLIER}"\n'
    )
    plan = tomllib.loads(plan_path.read_text())
    commands: list[list[str]] = []

    def stub_wait(command: list[str], env: dict[str, str]) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(bench_batch, "wait", stub_wait)
    monkeypatch.setattr(bench_batch, "newest", lambda *_: None)
    monkeypatch.setattr(bench_batch, "MANAGED", [])
    monkeypatch.setattr(bench_batch.bench_env, "verify", lambda: [])
    monkeypatch.setattr(
        bench_batch,
        "subprocess",
        SimpleNamespace(run=lambda *a, **k: SimpleNamespace(stdout="", returncode=0)),
    )
    token = SimpleNamespace(ask=lambda: "sk-ant-oat-stub")
    digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()

    assert bench_batch.run_plan(plan_path, plan, token, digest) == 0

    session = next(c for c in commands if c[1].endswith("bench_session.py"))
    assert session[session.index("--review-of") + 1] == str((results / EARLIER).resolve())
    assert Path(session[session.index("--review-of") + 1]).is_absolute()
    batch = json.loads(
        next((results / "experiments/results/bench" / SLICE / "batches").glob("*")).read_text()
    )
    assert batch["runs"][0]["review_of_folder"] == str((results / EARLIER).resolve())
