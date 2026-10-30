# On a fresh clone: `just sync`, then `just check`.

set shell := ["bash", "-uc"]

# Show the recipes.
default:
    @just --list

# Install the package and the dev tools, keeping the diagrams extra if it is installed.
sync:
    uv sync --inexact

# Lint, format check, type check, tests.
check: lint typecheck test

# Lint and check formatting.
lint:
    uv run ruff check
    uv run ruff format --check

# Format and fix what ruff can.
fmt:
    uv run ruff format
    uv run ruff check --fix

# Type check.
typecheck:
    uv run ty check

# Run the tests; `just test -m live` runs the ones that read diagrams.
test *args:
    uv run pytest {{ args }}

# Install the diagram reader and its weights: downloads 860 MB, or copies them with --from DIR.
models *args:
    uv sync --inexact --extra diagrams
    uv run pgnify setup {{ args }}
