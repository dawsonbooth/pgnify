# How pgnify works

pgnify is an MCP toolkit for converting chess books into faithful PGN. It is not an AI agent: the user's AI assistant, in any client that speaks MCP, supplies the model and decides which tools to call. pgnify supplies mechanical tools, the conversion instructions, and checks that need no model. This page describes how those parts work and why.

## Principles

- **The model interprets; tools are mechanical.** The model finds the book's structure, decides what is a game, an exercise or a variation, reads notation and prose, and resolves ambiguity. Tools do only what can be checked mechanically: chess legality and PGN with python-chess, document access with PyMuPDF, reading a diagram with a trained recogniser, and exact copying. No tool interprets prose or cleans up notation.
- **Fidelity to the book.** A conversion keeps the book's chess knowledge, not only its positions: the moves, the variations, all its chess prose word for word, whether or not a position goes with it, and the pairing of each exercise with its solution. It invents nothing, and it marks and reports what stays uncertain.
- **Minimal.** No tool, option, heuristic or format is added before an evaluation shows the need for it.
- **Checks apart from the model's account.** pgnify reports what it can verify separately from what the model says it did, and never calls a conversion complete.

## The parts

- `pgnify serve`: the MCP server, local, over stdio.
- The conversion instructions: one text, delivered as an MCP prompt, as a skill and as plain text.
- `pgnify check` and `pgnify report`: checks of the result, without a model.
- The diagram reader, the optional extra `pgnify[diagrams]`.
- Source-backed spans, an experimental option of the server.

pgnify has no agent runner, model selection, provider integration, API keys, local inference, hosted service, remote transport or web interface, and it does not convert a whole book on its own.

## The server

### Boundaries

- `--books PATH` names one book, or a folder of books (PDF and EPUB) searched with its subfolders. The server reads nothing else, and fails at startup if the path does not exist.
- Hidden files and folders are skipped, and a symbolic link counts as a book only when it leads to a PDF or EPUB in the folder that is not hidden.
- The model names a book by its path within the folder. A name that starts with `/`, `\` or `~`, or with a drive, is refused without being repeated. A name otherwise matches a book exactly, or the one book whose name differs only in case, curly quotes or dashes, and a name not found makes the server look in the folder again, for a book added since it started.
- `--out DIR` is the one place the server writes: the PGN files under plain file names, their notes, and its working folder, `.pgnify/`. It never writes through a symbolic link, and refuses an output folder whose `.pgnify/` is or holds one.
- `write_pgn` replaces a PGN file only if it wrote that file in the same session from the same book, or if the model asks it to, and it never writes over a notes file pgnify did not write. Names are compared ignoring case, so that two names cannot reach one file.
- Each book has a folder, `.pgnify/books/<key>/`, for the games saved from it, the pictures extracted from it, and the pieces its figurines were read as. The key is the book's name in lower-case letters, digits and dashes, then the first 12 hex digits of the SHA-256 of its name and contents, so that two copies of a book, or a link and its target, never share one.
- Results give paths relative to the output folder, and error messages show `<books>`, `<out>` and `~` in place of the configured folders and the home folder, escaped forms included.
- The server makes no network connections.

These are pgnify's own checks, not a sandbox: the server and its diagram reader run with the user's rights.

### Tools

| Tool | What it does |
|---|---|
| `doc_info` | the page count, table of contents and metadata |
| `doc_text` | a range of pages' text, each picture marked where it sits, at most 20,000 characters a call |
| `doc_search` | every hit of a text, with its page, section, box and line |
| `doc_images` | extracts a page's pictures into the book's folder |
| `read_diagram` | reads a diagram's placement from an extracted picture, with doubtful squares |
| `validate_moves` | replays movetext from a position and says where it stops being legal |
| `check_fen` | checks and completes a FEN |
| `check_pgn` | checks PGN text: what python-chess reads, and what it drops |
| `build_pgn` | builds a game from a structured spec, checking every move, and can save it by name |
| `write_pgn` | writes saved games, or PGN text, to a file in the output folder, with the model's notes |

The tools that read a book, and those that save or write games, take `book`, which may be left out when there is only one. `validate_moves`, and `build_pgn` without a name, need no book even when there are several.

Some books print the pieces in their moves as pictures, figurines. `doc_text` marks each picture with an id, the same wherever the same picture appears, and the chess tools take a figurine by its id in a move and read its piece from legality across the moves given: the main line first, then each variation, each line up to its last figurine. A figurine stays open where legality cannot settle it, and the pieces read in a saved game carry over to the book's other games.

The tools' descriptions document their mechanics, such as how a spec is built and how a variation branches. What to keep from a book, and what counts as a variation, are the conversion instructions' to say. `build_pgn`'s spec is described in place rather than by a reference to a definition, since Ollama's tool format has no `$ref`. The server runs one call at a time, since PyMuPDF is not thread-safe and a model may send several calls at once.

### What the client is told

The server's MCP instructions name the books, say that the server writes only into its output folder, and point to the prompt `convert`, which gives the conversion instructions after a request to convert part of a book.

### The log

`.pgnify/log.jsonl` records every tool call: the time, the tool, its arguments, shortened, the book, and a summary of its result or its error. The summary keeps what `pgnify report` reads: the pages read, the hits of each search, the pictures extracted, every diagram reading with its doubts and problems, and every build and write with its errors and the name it saved under. It holds none of the model's comments or notes, only their sizes, and no page text. `.pgnify/outputs.json` records the book each PGN file was written from, from which saved games, whether it has notes, and when.

## The conversion instructions

There is one source, `src/pgnify/instructions.md`. It says what a conversion keeps: every game, exercise and position; each exercise's solution, wherever the book prints it; the book's commentary word for word, each passage once, at its place; and the rest of its chess prose, such as introductions, explanations, definitions and principles, in text-only games among the others, in the book's order. It leaves out only what is not about chess, such as the publisher's pages, unless the user asks for less. It asks for the book's characters as printed, and a line for each heading and list item. It says to follow the book's structure, its headings included, or an output convention the user asks for, such as one game for each exercise with its solution's moves. It defines the main line, the moves played in a game or the line the book plays out from a position, and a variation, and what is neither; gives the convention for a square the diagram reader doubts, which is never to choose a piece to make a game playable, but to leave the square empty and mark it in an `[Uncertain]` tag; and lists the headers. It says nothing of how to proceed or how to divide the work.

A text-only game is a game like any other to the tools: `build_pgn` builds it from a spec with a comment and no moves, and `pgnify report` compares its comment with the book. Having no FEN, it is not reported as a position without moves.

The model gets the text in one of three ways:

1. the MCP prompt `convert`, which takes `book` and `part`;
2. the skill `plugin/skills/pgnify-convert/SKILL.md`, in the Agent Skills format, packaged as a Claude Code plugin with a marketplace entry, or copied into another host's skills folder; it adds one line saying that it needs pgnify's server;
3. plain text, from `pgnify instructions`.

The plugin holds only the skill and registers no server, so users choose the books and output folders themselves. `tests/test_instructions.py` checks that all three give the same text.

## Checking and reporting

`pgnify check FILE.pgn [--book BOOK]` checks one PGN file, against its book when it is given. `pgnify report DIR [--images] [--coverage PAGES]` checks each PGN file in an output folder against the book `outputs.json` records for it, quotes the model's notes, adds for each book what the server's log shows across the PGN files written from it, and writes `report.md` and `report.json` into the folder. With `--images`, it also saves, for each doubtful game, the book's picture beside a drawing of the game's start.

The findings are of three kinds, never mixed, and a fourth is given only when asked for.

**Verified problems** are mechanical facts that need fixing:

- text that does not parse as PGN, or that python-chess would drop;
- an illegal move;
- a FEN tag that is not a legal, complete position;
- a missing result, a Result tag that differs from the result ending the moves, or a result the final position contradicts, such as 1-0 after Black mates.

**Possible issues** are observations a person has to judge. Those of a PGN file come from its text and its games:

- squares a game's Uncertain tag marks, and positions without moves, whose moves may be in the comments;
- comment text not found word for word in the book, which may be paraphrase, the model's own notes, labels, or, on a scan, its corrections of the text layer; each line of a comment is compared on its own, as a heading or list item ends a sentence, and one-word sentences, such as labels, are not compared. Word for word means the same words in the same order, with quotes, apostrophes, dashes and spacing made uniform, so a difference of typography alone is not reported. Where the layout leaves the book's words in doubt, the book's text is read in each way it may be meant, for this comparison only: a hyphen that ends a line as printed or joined to the next word, and on a typeset page also removed; the line after a picture, which may be a caption set inside a sentence, kept or left out. A hyphen inside a line must match;
- a passage of five words or more that a game's comments give more often than the book does;
- a diagram read with doubts or problems that one of the file's games starts from: a game whose start differs from the reading only on those squares or the game's uncertain ones.

Those of a book come from the server's log, of every session in the folder:

- a diagram read with doubts or problems that no game in the book's PGN files starts from;
- a diagram in which the reader found no board, and one read without doubts whose placement no game in the book's PGN files starts from or reaches;
- a picture extracted with `doc_images` and never read, the likeliest sign of a diagram left out. Pictures smaller than 64 pixels on a side are taken for figurines, and pictures covering 90% of their page for scanned pages, and neither is reported;
- a game built under a name and never saved;
- the tools' errors. Those that belong to no PGN file's book are reported apart.

The log records the book of each call, not the conversion or the session it served, so its findings belong to the book. The report matches a book's diagrams against the games of all the PGN files written from it in the folder, and says so. A diagram that some file reaches counts as found for the book, which does not show that any one conversion is complete.

**The model's own account** is the notes it gave `write_pgn`, quoted as they are and not verified.

**Possible omissions** are listed only when asked for, with `--coverage all` or `--coverage` and the pages of the part converted, such as `46-103`. Pages can be named only for a folder of one book's PGN files. `coverage.py` takes the book's text on those pages and the comments of all the book's PGN files, compared as the comments are compared with the book. Two adjustments help the comparison: a hyphen compares the same whether the next word follows on its line or not, and a figurine compares as the piece it stands for. Words with a digit in them are left out. The book's words go in stretches of five, and a stretch counts as held only as often as the comments give it. A passage the book gives twice and the comments once is therefore held at neither place, and both are listed as repeated. A comment shorter than a stretch holds its words where the book gives them in order. What the comments do not hold is listed in runs of 10 words or more, with pages and an excerpt. Shorter runs are counted, not listed. So is the book's furniture, such as navigation labels, citations and running heads: a run most of whose stretches the whole book gives three times or more, and no comment holds. The list shows textual coverage, not that what is held is in the right place, nor that what is missing was left out by mistake. Text of other parts of the book on the same pages, and a scan's misreadings, show up as possible omissions too.

A report without verified problems does not mean the conversion is complete or faithful: without a reference PGN to compare with, pgnify cannot tell whether every game, exercise, variation or passage was found. With `--coverage`, it shows whether the book's words are held, not where or how. Both commands exit with status 1 on a verified problem, and with status 2 when they cannot read their input; possible omissions change neither. `report.json` gives, for each PGN file, each game's label, start, plies, variations, comments and uncertain squares, and the findings, each with its game; and for each book, its PGN files, a summary of the log and the log's findings, and with `--coverage` the pages checked, the words held and the possible omissions.

## The diagram reader

The extra `pgnify[diagrams]` adds PyTorch, torchvision, OpenCV's headless build, scikit-image, numpy and Pillow; on macOS, pgnify with the extra takes 1.1 GB installed, against 249 MB without it. Chess_diagram_to_FEN's reading code (MIT) is vendored in `pgnify.recogniser`. `read_diagram` runs it in a separate process, `python -m pgnify.recogniser`, so that PyTorch's memory and any crash stay out of the server; the process loads the models at every reading, which takes about 4 s, and longer the first time.

Its weights, about 310 MB, are installed once by `pgnify setup` from upstream's release 1.0, or copied with `--from`, each checked against a SHA-256 pinned in `weights.py`, and kept in the user's cache. pgnify never ships them. Without the extra or the weights, `read_diagram` says which is missing and how to install it.

## Source-backed spans

With `pgnify serve --spans`, `doc_text` numbers each page's lines, and a comment given to `build_pgn` may name spans of them, by page, line and the words a span begins and ends with, which `build_pgn` copies exactly. A span that does not stand exactly where it says is an error, and nothing is copied. Spans save output only on long notes, and on a scan they copy the text layer's misreadings, which a model writing the words corrects, so they stay off by default until a run shows a model using them well.

## Dependencies and licence

- **python-chess** (GPL-3.0-or-later) decides legality and reads and writes PGN.
- **PyMuPDF** (AGPL-3.0, or Artifex's commercial licence) reads PDF and EPUB. pgnify requires 2.0 or later: 1.x, on MuPDF 1.28.2, squeezes an EPUB picture sized by a percentage of its height into the space left on its page, so that one book keeps only a third of its pictures, and MuPDF 1.28.3 to 1.28.5 fix memory-safety bugs in the code that reads a book. `tests/test_doc_tools.py` checks the layout.
- **mcp** (MIT) serves the tools, and **Typer** (MIT) the command line.

pgnify is under AGPL-3.0-or-later, which python-chess's GPL and PyMuPDF's AGPL both allow; a commercial PyMuPDF licence would not make pgnify permissive while it uses python-chess. The vendored reading code keeps its MIT licence. In the diagrams extra, OpenCV's wheels include FFmpeg, under the LGPL-2.1, and on Linux, PyTorch from PyPI brings NVIDIA's CUDA libraries, under NVIDIA's licences; pgnify distributes neither. `NOTICE` lists every dependency with its declared licence.

## Layout

```text
pyproject.toml                    # pgnify: script `pgnify`; extra `diagrams`; AGPL-3.0-or-later
src/pgnify/
  server.py                       # the MCP server: its log, prompt and instructions
  books.py outputs.py             # the books it reads, and the folder it writes
  chess_tools.py doc_tools.py diagram_tools.py spans.py
  report.py prose.py coverage.py  # check and report
  cli.py                          # pgnify serve | check | report | setup | instructions
  instructions.md instructions.py # the one source of the conversion instructions
  weights.py                      # the diagram reader's weights
  recogniser/                     # the diagram reader, which read_diagram runs
plugin/                           # .claude-plugin/plugin.json, skills/pgnify-convert/SKILL.md
.claude-plugin/marketplace.json
.github/workflows/ci.yml          # lint, types and tests on Linux and macOS
experiments/                      # the evaluation, not packaged
tests/
```

## The evaluation

`experiments/` measures conversions against truths: reference PGNs of parts of books, built and checked by hand, or, for a whole book, an inventory of its boards, tests and games. The books, the truths, the inventories and the runs stay local, under `data/local/` and `experiments/results/`, since they hold the books' words.

- `slices.toml` names each slice: the part of a book, the clipped source it is converted from, and its truth; or a whole book, by its source, and its inventory.
- `cut.py` cuts a clipped source, the files or pages of a book that hold a slice, so that a session sends a hosted model nothing else of the book.
- `session.py` opens an interactive Claude Code session on a Claude subscription, with Claude Sonnet 5.5 at high effort and pgnify's tools alone, on a slice's clipped source.
- `client_session.py` opens a session as a user would have it: pgnify installed with `uv tool install`, its server registered with `claude mcp add`, its skill from the plugin marketplace, and the whole book, of which the request names a part, or which it names whole. It opens a session only when the pgnify installed and the marketplace's skill are the checkout's, committed and pushed, so that a run tests what the checkout holds. Of Claude Code's own tools, the model has only those that plan and delegate work, so the book reaches it through pgnify alone. A watchdog guards the experiment against a runaway or hung session: it stops a session whose cost, its subagents' included, passes a safety ceiling, or that shows no activity for half an hour, but never one that is still working. The games such a session saved but did not write are kept beside it.
- `score_slice.py` scores a run's PGN alone against the truth, game by game: positions, main lines, the truth's variations kept or lost, commentary word for word and in place, and anything unsupported. It lists every fault for review against the model's final answer, and accounts for the session agent by agent, the advisor's share apart.
- `score_book.py` compares a whole-book run's PGN with the book's inventory: the boards each game starts from or reaches, the tests and their solutions, the named games, the commentary page by page, and work overwritten or lost between the session's agents. It shows what is missing, not that what is there is right: that takes a review by hand.
