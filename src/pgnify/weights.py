"""The diagram reader's weights: Chess_diagram_to_FEN's release 1.0 models for chess, five files,
about 310 MB. `pgnify setup` installs them once into pgnify's cache, downloaded from upstream's
release or copied from a folder with --from, and checks each against the SHA-256 pinned here.
pgnify never ships them.

The cache is $PGNIFY_CACHE, else ~/.cache/pgnify. $PGNIFY_WEIGHTS names another folder for the
weights instead, holding them in its chess/ subfolder."""

import hashlib
import os
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

RELEASE = "https://github.com/tsoj/Chess_diagram_to_FEN/releases/download/1.0/models.zip"
# models.zip holds them under models/chess/, beside other games' models and plots.
ZIP_FOLDER = "models/chess/"
# The SHA-256 of each file.
PINNED = {
    "best_model_existence_0.999_2026-03-09-00-23-33.pth": (
        "7dbcaa4203874b0ad821b1144855c2466fb22a97b3209b55db2ae8c4a2a89ac9"
    ),
    "best_model_image_rotation_0.995_2026-03-09-02-01-21.pth": (
        "7c1c976e79f78ce20b9528c1ea2f4b575402efb265fd6b5441202d30bfb990e8"
    ),
    "best_model_orientation_0.988_2026-03-03-02-34-18.pth": (
        "0cc1197e2567affc4696dba1897ce725cc57f339959dbaf5901e60ab1ed6be07"
    ),
    "best_model_position_0.977_2026-03-09-00-47-01.pth": (
        "35b58ecbc72b546b980730b6127bdf6903d2109aa3f8a40ed8a800ea609f711d"
    ),
    "best_model_quad_0.971_2026-03-09-22-01-38.pth": (
        "a46a1164d7574ecf13e936aff75f0a3bc7800e81b855fbf09e22494aa1814114"
    ),
}


def folder() -> Path:
    """The folder the reader loads its weights from: it holds them in chess/."""
    if named := os.environ.get("PGNIFY_WEIGHTS"):
        return Path(named).expanduser()
    cache = os.environ.get("PGNIFY_CACHE") or Path.home() / ".cache" / "pgnify"
    return Path(cache).expanduser() / "models" / "chess-diagram-to-fen"


def missing() -> list[str]:
    """The weights' files not in the folder."""
    chess = folder() / "chess"
    return [name for name in PINNED if not (chess / name).is_file()]


def install(source: Path | None, say: Callable[[str], None]) -> Path:
    """Install the weights into folder(), checking each against its pinned SHA-256: copied from
    source, a folder holding them or holding chess/ with them, or else downloaded from
    upstream's release, about 860 MB. say reports progress. Returns folder(); raises
    ValueError, leaving it as it was, when a file is missing or does not match."""
    target = folder() / "chess"
    with tempfile.TemporaryDirectory(prefix="pgnify-weights-") as scratch:
        staged = Path(scratch) / "chess"
        staged.mkdir()
        if source is not None:
            found = source / "chess" if (source / "chess").is_dir() else source
            for name in PINNED:
                if not (found / name).is_file():
                    raise ValueError(f"{found} has no {name}")
                shutil.copyfile(found / name, staged / name)
        else:
            archive = Path(scratch) / "models.zip"
            say(f"Downloading {RELEASE}, about 860 MB.")
            with urllib.request.urlopen(RELEASE) as response, archive.open("wb") as file:
                shutil.copyfileobj(response, file)
            with zipfile.ZipFile(archive) as zipped:
                for name in PINNED:
                    with (
                        zipped.open(ZIP_FOLDER + name) as member,
                        (staged / name).open("wb") as file,
                    ):
                        shutil.copyfileobj(member, file)
        for name, expected in PINNED.items():
            if _sha256(staged / name) != expected:
                raise ValueError(f"{name} does not match the SHA-256 pgnify pins for it")
        target.mkdir(parents=True, exist_ok=True)
        for name in PINNED:
            shutil.move(staged / name, target / name)
    say(f"The diagram reader's weights are in {target.parent}.")
    return target.parent


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()
