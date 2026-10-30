"""The conversion instructions are one text, given word for word by the MCP prompt, the skill and
`pgnify instructions`."""

from support import ROOT

from pgnify import instructions

SKILL = ROOT / "plugin" / "skills" / "pgnify-convert" / "SKILL.md"


def test_the_skill_gives_the_instructions_word_for_word() -> None:
    skill = SKILL.read_text()
    assert skill.startswith("---\nname: pgnify-convert\ndescription: ")
    assert skill.endswith(instructions.text())


def test_the_instructions_leave_the_output_convention_to_the_user() -> None:
    assert "Where the user asks for an output convention" in instructions.text()


def test_a_request_puts_the_part_book_and_conventions_before_the_instructions() -> None:
    asked = instructions.request("pocket-primer.epub", "Chapter 1", "one game for each exercise")
    assert asked.startswith("Convert Chapter 1 of the book pocket-primer.epub into PGN")
    assert "Output conventions: one game for each exercise" in asked
    assert asked.endswith(instructions.text())
