"""pgnify: an MCP toolkit for converting chess books into faithful PGN. The user's AI assistant
reads and interprets the book; pgnify's tools read the book, check every move and write the PGN,
and `pgnify check` and `pgnify report` check the result without a model."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pgnify")
except PackageNotFoundError:  # run from a source tree that was never installed
    __version__ = "0.0.0"
