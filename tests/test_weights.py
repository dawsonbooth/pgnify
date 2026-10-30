import hashlib
from pathlib import Path

import pytest

from pgnify import weights


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, bytes]:
    """Two small files pinned in place of the real weights, and a cache for the test alone."""
    files = {"a.pth": b"first", "b.pth": b"second"}
    monkeypatch.setattr(
        weights, "PINNED", {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    )
    monkeypatch.setenv("PGNIFY_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("PGNIFY_WEIGHTS", raising=False)
    return files


def test_weights_from_a_folder_are_installed_when_they_match(
    pinned: dict[str, bytes], tmp_path: Path
) -> None:
    source = tmp_path / "source" / "chess"
    source.mkdir(parents=True)
    for name, data in pinned.items():
        (source / name).write_bytes(data)
    assert weights.missing() == ["a.pth", "b.pth"]
    installed = weights.install(source.parent, say=lambda _: None)
    assert installed == weights.folder() == tmp_path / "cache" / "models" / "chess-diagram-to-fen"
    assert weights.missing() == []


def test_a_file_that_does_not_match_installs_nothing(
    pinned: dict[str, bytes], tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.pth").write_bytes(pinned["a.pth"])
    (source / "b.pth").write_bytes(b"tampered")
    with pytest.raises(ValueError, match=r"b\.pth does not match"):
        weights.install(source, say=lambda _: None)
    assert weights.missing() == ["a.pth", "b.pth"]


def test_a_missing_file_installs_nothing(pinned: dict[str, bytes], tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.pth").write_bytes(pinned["a.pth"])
    with pytest.raises(ValueError, match=r"has no b\.pth"):
        weights.install(source, say=lambda _: None)
    assert weights.missing() == ["a.pth", "b.pth"]


def test_pgnify_weights_names_a_folder_already_holding_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PGNIFY_WEIGHTS", str(tmp_path / "mine"))
    assert weights.folder() == tmp_path / "mine"
