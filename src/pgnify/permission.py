"""What the person allows, when `pgnify serve` is given no --books or no --out.

The server asks through the MCP client, with the SDK's resolvers, the first time the assistant
names a book to read or a folder to write PGN files in, and remembers each yes by the real path
until the session ends. Only the person's answer in the client allows anything, never the
assistant's words. A decline, or a yes with the box unticked, refuses the path for the rest of the
session, and a cancel refuses the call alone.

A resolver only reads this state and returns the question, or a plain value when none is needed,
since the SDK may run it more than once for a call. The tool then settles the answer here, once,
and the tool alone raises."""

import os
import sys
import unicodedata
from pathlib import Path
from typing import Any

from mcp.server.elicitation import AcceptedElicitation, CancelledElicitation, DeclinedElicitation
from mcp.types import ClientCapabilities
from pydantic import BaseModel, Field

# What to do next after a refusal, by whether the person may be asked again.
_AGAIN = "Ask the person whether to try again."
_ELSE = (
    "Ask the person which book or folder to use instead; paths inside a refused folder stay "
    "refused."
)
# The Unicode categories of control, formatting and line and paragraph separator characters.
_UNSHOWN = ("Cc", "Cf", "Zl", "Zp")


class Allow(BaseModel):
    """The form the person fills in: one box, ticked to begin with, since the SDK refuses an
    empty form that comes back accepted without content."""

    allow: bool = Field(default=True, title="Allow")


class CannotAsk:
    """What a resolver returns when a question is needed and the client has not declared form
    elicitation, so that the tool can say so."""


CANNOT_ASK = CannotAsk()


def can_ask(capabilities: ClientCapabilities | None) -> bool:
    """Whether the client declared form elicitation: a bare `elicitation: {}` counts, as the SDK
    counts it, and a client that declares URL elicitation alone does not."""
    elicitation = capabilities.elicitation if capabilities else None
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


def shown(path: Path) -> str:
    """The path with ~ for the home folder, as the person is asked about it."""
    home = Path.home()
    if home != Path(home.anchor) and path.is_relative_to(home):
        return "~" if path == home else f"~{os.sep}{path.relative_to(home)}"
    return str(path)


def shown_with_real(given: Path, real: Path) -> str:
    """The real path first, which is what the person allows, and, only when a link or a .. makes
    it differ, the path the assistant gave after it."""
    if given == real:
        return shown(real)
    return f"{shown(real)}\n\nThe assistant gave the path as {shown(given)}."


def full_path(text: str, what: str) -> Path:
    """The path text names, ~ expanded; ValueError unless it is absolute or starts with ~."""
    if not text.strip():
        raise ValueError(f"give {what} as a full path, absolute or starting with ~")
    # A line break or direction mark in the path could change how the question reads to the person.
    if any(unicodedata.category(char) in _UNSHOWN for char in text):
        raise ValueError(
            f"give {what} as a plain path, without control, formatting or line-separator characters"
        )
    try:
        path = Path(text).expanduser()
    except (RuntimeError, OSError):  # no home folder for a ~name
        path = Path(text)
    if not path.is_absolute():
        raise ValueError(f"give {what} as a full path, absolute or starting with ~")
    return path


def folded(path: Path) -> Path:
    """The path as a refusal keeps it: in Unicode form NFC, and, where the file system ignores
    case, in one case, so that no other spelling of a refused path is asked about again."""
    text = unicodedata.normalize("NFC", str(path))
    return Path(text.casefold() if sys.platform in ("darwin", "win32") else text)


class Allowances:
    """The real paths the person allowed or refused in this session. A path is covered when the
    person allowed it, or, for folders, a folder it is in."""

    def __init__(self, verb: str, flag: str, nested: bool = False):
        # What pgnify would do with a path, such as "read", the server's flag that allows paths
        # without asking, and whether a yes covers the folders inside too.
        self.verb, self.flag, self.nested = verb, flag, nested
        self.allowed: set[Path] = set()
        self.refused: set[Path] = set()  # folded

    def covers(self, path: Path) -> bool:
        """Whether the person allowed path itself or, for folders, a folder it is in. Another
        spelling of an allowed path is asked about again."""
        if path in self.allowed:
            return True
        # A snapshot, as a tool may allow a folder while a resolver, in another thread, looks.
        return self.nested and any(path.is_relative_to(folder) for folder in tuple(self.allowed))

    def refuses(self, path: Path) -> bool:
        """Whether the person refused path, or, for folders, a folder it is in, in any spelling
        that names the same path on a file system that ignores case or Unicode forms."""
        spelled = folded(path)
        refused = tuple(self.refused)
        return spelled in refused or (
            self.nested and any(spelled.is_relative_to(folder) for folder in refused)
        )

    def asks_about(self, path: Path) -> bool:
        """Whether the person has yet to say anything of path."""
        return not self.refuses(path) and not self.covers(path)

    def settle(self, path: Path, answer: Any) -> str | None:
        """Record what the person answered about path, and return why pgnify may not use it now,
        or None when it may. answer is the outcome the resolver gave the tool: a form, or the
        plain value that stands for no question."""
        place = shown(path)
        # A refusal wins over a yes for a folder above the path, given later.
        if self.refuses(path):
            return self._refusal(
                place, "they refused it, or a folder it is in, earlier in this session", _ELSE
            )
        if self.covers(path):
            return None
        if isinstance(answer, DeclinedElicitation):
            self.refused.add(folded(path))
            return self._refusal(
                place,
                "they declined, or their client declined without showing them the question, and "
                "pgnify does not ask again in this session",
                _ELSE,
            )
        if isinstance(answer, CancelledElicitation):
            return self._refusal(
                place, "they cancelled the question, or their client did without showing it", _AGAIN
            )
        data = answer.data if isinstance(answer, AcceptedElicitation) else None
        if isinstance(data, Allow):
            if data.allow:
                self.allowed.add(path)
                return None
            self.refused.add(folded(path))
            return self._refusal(
                place,
                "they left the box unticked, and pgnify does not ask again in this session",
                _ELSE,
            )
        if isinstance(data, CannotAsk):
            return (
                f"pgnify cannot ask the person whether to {self.verb} {place}, as the client "
                f"has not declared form elicitation: ask the person to start `pgnify serve` with "
                f"{self.flag}, or to use a client that can show questions"
            )
        return f"pgnify could not ask the person about {place} in this call: call the tool again"

    def _refusal(self, place: str, why: str, next_step: str) -> str:
        return f"the person did not allow pgnify to {self.verb} {place}: {why}. {next_step}"
