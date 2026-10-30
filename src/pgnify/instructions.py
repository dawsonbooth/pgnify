"""The conversion instructions: one text, instructions.md, which the MCP prompt `convert`, the
skill in plugin/skills/pgnify-convert/ and `pgnify instructions` all give word for word."""

from importlib.resources import files


def text() -> str:
    return files("pgnify").joinpath("instructions.md").read_text(encoding="utf-8")


def request(book: str, part: str, conventions: str | None = None) -> str:
    """A request to convert part of book, with any output conventions the user asks for, and the
    instructions after it."""
    asked = f"Convert {part} of the book {book} into PGN, with pgnify's tools."
    if conventions:
        asked += f"\n\nOutput conventions: {conventions}"
    return f"{asked}\n\n{text()}"
