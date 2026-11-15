"""The workflow benchmark's pinned environment, apart from the user's own install so that main can
move on: pgnify installed from one commit in a uv tool directory of its own, the plugin's skill
from that commit, a copy of one Claude Code release, the dependencies as installed, and the
diagram reader's weights. Each is recorded by hash in the environment's manifest, which every
benchmark session checks before it starts. Local only: the environment lives under
data/local/bench/env/, which holds a copy of Claude Code and the installed packages.

Made once, by hand:

    UV_TOOL_DIR=data/local/bench/env/tools UV_TOOL_BIN_DIR=data/local/bench/env/bin \\
        uv tool install --python 3.14.2 "pgnify[diagrams] @ git+https://github.com/dawsonbooth/pgnify@COMMIT"
    git archive COMMIT plugin | tar -x -C data/local/bench/env
    cp -p ~/.local/share/claude/versions/RELEASE data/local/bench/env/claude/RELEASE
    uv pip freeze --python data/local/bench/env/tools/pgnify \\
        > data/local/bench/env/requirements.lock.txt

then recorded with `uv run python experiments/bench_env.py freeze`, and checked with
`uv run python experiments/bench_env.py verify`.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from pgnify import weights

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "data" / "local" / "bench" / "env"
MANIFEST = ENV / "manifest.json"


def pgnify_command() -> Path:
    """The pinned environment's pgnify command."""
    return ENV / "bin" / "pgnify"


def plugin_dir() -> Path:
    """The pinned commit's plugin, which a session loads with --plugin-dir."""
    return ENV / "plugin"


def claude_command() -> Path:
    """The pinned copy of Claude Code: the only release in the environment's claude/ folder."""
    releases = sorted((ENV / "claude").iterdir())
    if len(releases) != 1:
        raise RuntimeError(f"the environment holds {len(releases)} Claude Code releases, not one")
    return releases[0]


def describe() -> dict[str, Any]:
    """What the environment holds now, each part by hash."""
    site = next((ENV / "tools" / "pgnify" / "lib").glob("python*/site-packages"))
    package = site / "pgnify"
    installed = json.loads(next(site.glob("pgnify-*.dist-info/direct_url.json")).read_text())
    claude = claude_command()
    version = subprocess.run([str(claude), "--version"], capture_output=True, text=True).stdout
    tesseract = shutil.which("tesseract")
    return {
        "pgnify": {
            "commit": installed.get("vcs_info", {}).get("commit_id"),
            "url": installed.get("url"),
            "package_sha256": _tree(package, ("*.py", "*.md", "*.json", "*.toml")),
            "instructions_sha256": _file(package / "instructions.md"),
        },
        "plugin_sha256": {
            str(path.relative_to(plugin_dir())): _file(path)
            for path in sorted(plugin_dir().rglob("*"))
            if path.is_file()
        },
        "claude": {"release": version.split()[0], "binary_sha256": _file(claude)},
        "python": _venv(ENV / "tools" / "pgnify" / "pyvenv.cfg").get("version_info"),
        "dependencies_sha256": _file(ENV / "requirements.lock.txt"),
        "weights_sha256": {
            path.name: _file(path) for path in sorted(weights.folder().rglob("*.pth"))
        },
        "tesseract": subprocess.run(
            [tesseract, "--version"], capture_output=True, text=True
        ).stdout.split("\n")[0]
        if tesseract
        else None,
    }


def verify() -> list[str]:
    """How the environment differs from its manifest: nothing, when it is as frozen."""
    if not MANIFEST.exists():
        return [f"no manifest at {MANIFEST}: freeze the environment first"]
    frozen = json.loads(MANIFEST.read_text())["environment"]
    return _differences(frozen, describe())


def _differences(frozen: Any, now: Any, path: str = "") -> list[str]:
    if isinstance(frozen, dict) and isinstance(now, dict):
        found = []
        for key in sorted(set(frozen) | set(now)):
            found += _differences(frozen.get(key), now.get(key), f"{path}.{key}".strip("."))
        return found
    return [] if frozen == now else [f"{path}: frozen {frozen!r}, now {now!r}"]


def _venv(config: Path) -> dict[str, str]:
    """A virtual environment's pyvenv.cfg, as keys and values."""
    pairs = (line.split("=", 1) for line in config.read_text().splitlines() if "=" in line)
    return {key.strip(): value.strip() for key, value in pairs}


def _file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree(folder: Path, patterns: tuple[str, ...]) -> str:
    """One hash over the files of a folder that match the patterns, by path and content."""
    digest = hashlib.sha256()
    files = sorted({path for pattern in patterns for path in folder.rglob(pattern)})
    for path in files:
        digest.update(str(path.relative_to(folder)).encode() + b"\0" + _file(path).encode() + b"\n")
    return digest.hexdigest()


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if action == "freeze":
        if MANIFEST.exists():
            sys.exit(f"{MANIFEST} exists: the environment is frozen once, never refrozen in place")
        MANIFEST.write_text(json.dumps({"environment": describe()}, indent=1) + "\n")
        print(f"froze {MANIFEST}")
    elif action == "verify":
        differences = verify()
        print("\n".join(differences) or "the environment is as frozen")
        sys.exit(1 if differences else 0)
    else:
        sys.exit("usage: bench_env.py freeze | verify")


if __name__ == "__main__":
    main()
