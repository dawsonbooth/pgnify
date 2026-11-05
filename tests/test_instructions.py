"""The conversion instructions are one text, given word for word by the MCP prompt, the skill and
`pgnify instructions`."""

import pytest
from support import ROOT

from pgnify import instructions

SKILL = ROOT / "plugin" / "skills" / "pgnify-convert" / "SKILL.md"


def test_the_skill_gives_the_instructions_word_for_word() -> None:
    skill = SKILL.read_text()
    assert skill.startswith("---\nname: pgnify-convert\ndescription: ")
    assert skill.endswith(instructions.text())


def test_the_instructions_leave_the_output_convention_to_the_user() -> None:
    assert "Where the user asks for an output convention" in instructions.text()


# Each rule a scanned book's validation showed a model needs: two-bar marks read as one-bar from
# a whole page; notes lost where they ran on at the top of a column; assessments in words made
# into marks; and a claim that nothing was missing when four paragraphs were.
@pytest.mark.parametrize(
    "rule",
    [
        "read every evaluation mark from a close-up",
        "text at the top of a column or a page may continue",
        "assessment the book gives in words stays in its words",
        "or the whole part you were asked for, complete, audit it against its pages",
        "which material you checked against the pages, while converting or in the audit",
        "nothing is missing only where the audit shows it",
    ],
    ids=[
        "marks-from-close-ups",
        "column-continuations",
        "no-marks-from-words",
        "audit",
        "checked-apart-from-converted",
        "claims",
    ],
)
def test_the_instructions_keep_the_rules_a_scanned_books_validation_showed_were_needed(
    rule: str,
) -> None:
    assert rule in instructions.text()


def test_a_request_puts_the_part_book_and_conventions_before_the_instructions() -> None:
    asked = instructions.request("pocket-primer.epub", "Chapter 1", "one game for each exercise")
    assert asked.startswith("Convert Chapter 1 of the book pocket-primer.epub into PGN")
    assert "Output conventions: one game for each exercise" in asked
    assert asked.endswith(instructions.text())
