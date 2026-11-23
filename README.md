# pgnify

pgnify lets the AI assistant you already use convert chess books, in PDF or EPUB, into Portable Game Notation (PGN).

It is a [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server. Your assistant reads the book and decides what it holds. pgnify's tools read the book's pages and diagrams, check every move with [python-chess](https://python-chess.readthedocs.io) and write the PGN. Its conversion instructions ask for every game, exercise and solution, the variations the book gives in its prose, and all the book's chess prose word for word: its commentary on the games, and its introductions, explanations and definitions, in games that hold text alone. You can ask for less, such as the games alone. Afterwards, `pgnify report` checks the result without a model and lists what it verified and what a person should look at.

pgnify runs no model and needs no API key: the model is your assistant's.

pgnify is alpha software. It is not on PyPI yet, so you install it from this repository.

## Install pgnify

You need [uv](https://docs.astral.sh/uv/).

```sh
uv tool install "pgnify @ git+https://github.com/dawsonbooth/pgnify"
```

This installs the `pgnify` command, about 250 MB.

To read diagrams too, install pgnify with its diagram reader instead, about 1.1 GB with PyTorch, then fetch the reader's weights once:

```sh
uv tool install "pgnify[diagrams] @ git+https://github.com/dawsonbooth/pgnify"
pgnify setup    # downloads 860 MB once, and keeps 310 MB in ~/.cache/pgnify
```

`pgnify setup --from DIR` copies the weights from a folder that already holds them, instead of downloading them. Either way, pgnify checks each file against the SHA-256 hash it pins. On Linux, PyTorch from PyPI also installs NVIDIA's CUDA libraries, several gigabytes more; [PyTorch's installation guide](https://pytorch.org/get-started/locally/) shows how to install a CPU-only build instead.

Without the diagram reader or its weights, the other tools still work, and `read_diagram` tells the assistant what is missing.

To read a scanned page as text, for an assistant whose model cannot see pictures, install [Tesseract](https://github.com/tesseract-ocr/tesseract), an OCR program that runs on your computer: `brew install tesseract` on macOS, `sudo apt install tesseract-ocr` on Debian or Ubuntu, or the [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) on Windows. pgnify runs it when the assistant asks for OCR, and says how to install it when it is missing.

To update pgnify, run `uv tool upgrade pgnify`. It installs the repository's latest version, with the diagram reader if you installed it.

## Connect it to your assistant

Register pgnify with no flags, as `pgnify serve`. The assistant then gives pgnify the full path of each book and each PGN file, such as `~/Chess/Books/Workbook.epub` or `~/Chess/PGN/Silman/Part Four.pgn`. The first time it names a book, your client shows you a question:

```text
Allow pgnify to read this book until this session ends?

~/Chess/Books/Workbook.epub
```

The first time it names a folder to save in:

```text
Allow pgnify to write PGN files in this folder, and in folders
inside it, until this session ends?

~/Chess/PGN/Silman

pgnify will create the folder if it is missing.
```

Without `--out`, pgnify also asks before it replaces a file that this session didn't write from the same book, when the assistant asks to replace it. A session lasts until your client stops pgnify, as when you quit Claude Code. Each question has an **Allow** box, ticked to begin with. Your answers work like this:

- **Yes**: allows that book, that folder and the folders inside it, or replacing that file, until the session ends.
- **No, or yes with the box unticked**: refuses that path, and anything inside it, for the rest of the session, without asking again.
- **Closing the question**: refuses only that one request.

The questions about a book or a folder show the path with any links followed and, when it differs, the path the assistant gave. pgnify refuses, without asking, a path that holds line breaks or other invisible characters.

These flags set pgnify's folders, and with them it asks nothing. With only one, it asks only about the other:

- `--books`: a book, or a folder of books. pgnify reads nothing else, and the assistant names a book by its path within the folder.
- `--out`: a folder for pgnify's output alone. pgnify writes nothing anywhere else, and the assistant names a file by its path within the folder, such as `Silman/Part Four`.

A run with nobody to answer, such as a script, needs both flags. Claude Code's print mode (`claude -p`) and its VS Code extension answer pgnify's questions without showing them, so pgnify refuses. For the extension this is a known issue, [anthropics/claude-code#98256](https://github.com/anthropics/claude-code/issues/98256); set both folders there until it is fixed. If a client doesn't support questions, pgnify's error names the flag to start it with.

### Claude Code

Register pgnify so that it asks you about each book and folder:

```sh
claude mcp add --scope user pgnify -- pgnify serve
```

Or set both folders:

```sh
claude mcp add --scope user pgnify -- pgnify serve --books ~/Chess/Books --out ~/Chess/PGN
```

### Claude Desktop

Add pgnify to `claude_desktop_config.json`, in `~/Library/Application Support/Claude/` on macOS and `%APPDATA%\Claude\` on Windows. Use full paths, including the path to `pgnify`, which is in the folder `uv tool dir --bin` prints:

```json
{
  "mcpServers": {
    "pgnify": {
      "command": "/Users/you/.local/bin/pgnify",
      "args": ["serve", "--books", "/Users/you/Chess/Books", "--out", "/Users/you/Chess/PGN"]
    }
  }
}
```

This example sets both folders. To be asked instead, use `"args": ["serve"]`. pgnify's questions haven't been tried in Claude Desktop yet.

### Other MCP clients

Add a stdio server whose command is `pgnify serve`. Add `--books PATH` and `--out DIR` to be asked nothing.

If the client says the server failed to start, run the same command in a terminal: pgnify prints why it stopped, such as `there is no PDF or EPUB at …`. To try the tools by hand, run the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector), which needs Node.js:

```sh
npx @modelcontextprotocol/inspector pgnify serve --books ~/Chess/Books --out ~/Chess/PGN
```

## Give it the instructions

The conversion instructions are one text, and your assistant can get them in three ways:

- **As the server's prompt**: the MCP prompt `convert` takes the book and the part to convert. In Claude Code it is the command `/mcp__pgnify__convert`; other clients that support prompts list it under pgnify.
- **As a skill**: in Claude Code, run:

  ```text
  /plugin marketplace add dawsonbooth/pgnify
  /plugin install pgnify@pgnify
  ```

  The plugin holds only the skill, `pgnify-convert`. It does not register the server: register it as [Connect it to your assistant](#connect-it-to-your-assistant) shows. For another assistant that reads skills, copy this repository's `plugin/skills/pgnify-convert/` into its skills folder.
- **As plain text**: `pgnify instructions` prints them, to paste into any assistant.

Then ask for the part you want, for instance: "Convert Chapter 5 of Workbook.epub, the rook forks with their solutions, into PGN." You can also ask for an output convention, such as one game for each exercise with its solution's moves, or a numbering for the Round tag. Without one, the assistant follows the book's structure and says in its notes which convention it chose.

Ask for one part at a time, such as a chapter or a section. The largest part tried so far is a chapter of 42 exercises and 6 examples, converted in one session.

## Book formats, and what depends on the model

pgnify supports a format when its tools give your assistant everything a conversion needs: the book's text, its pages as pictures, its diagrams read as positions, and a legality check of every move. That much is pgnify's, and it is the same whatever the model. How faithful a conversion turns out also depends on the model and the client: whether the model reads small marks and figurines correctly, uses what the book itself explains, such as its key to symbols, follows the conversion instructions and checks its own work. Two models given the same tools can convert the same pages differently, and `pgnify report` checks only part of the result.

Tried so far, with Claude Sonnet 5.5 in Claude Code: EPUBs, one of them whole; born-digital PDFs, whether their diagrams are set in a chess font or are pictures; and scanned PDFs, whether their text layer is a machine reading or they have none. Each kind of PDF was tried on part of a book, not yet on a whole one. The misses that recurred were the model's: letters in its comments where the book prints figurines, as a PDF's text layer may give them; a variation the book gives in its prose kept as words; and on scans, an evaluation mark read as its look-alike, ∓ for ⩱, and now and then a move read as another legal one.

[The benchmark](docs/benchmark.md) compares Claude models, effort levels and workflows by faithfulness and cost on three book extracts. Sonnet 5.5 at medium effort was never measurably less faithful than at high effort, and cost less. No model, effort or workflow was reliably more faithful than Sonnet 5.5 at high effort. All left faults, so check the result.

### Assistants that see pictures, and those that read only text

pgnify supports two kinds of assistant, and says which it has tried:

- **An assistant whose model reads pictures** reads a scanned page, its moves, figurines and evaluation marks, from the pictures `doc_page` shows, and each diagram with `read_diagram`. Every format above was tried this way.
- **An assistant whose model reads only text** gets what local tools can read without a picture. With the diagram reader and Tesseract installed:
  - `doc_text` with `ocr` gives Tesseract's reading of each page as printed, named as a machine reading and never as the book's text, for a scan without a text layer or one whose text seems garbled. On the pages tried, it read 98 or 99 words of prose in 100 exactly and kept the order of two columns; it leaves out the boards it finds, so that their pieces are not read as letters.
  - `read_diagram` with a page finds the boards printed in a frame on that page and reads each one, with the text printed beside it, such as an exercise's number, whose move it is, and the players.

  What such an assistant cannot rely on:

  - **OCR misses and corrupts chess notation.** Tesseract reads no figurines, evaluation marks or other chess signs, and can misread or drop an exercise's number or another short label. The moves of a scanned book come out partly unread or wrong, and legality alone cannot tell which.
  - **Leaving a board out of an OCR reading does not mean the text around it was all read.**
  - **OCR text has none of the text layer's checks.** `pgnify report` compares the comments with a page's text layer, never with an OCR reading, and no span copies from one. The page itself stays available: `doc_page` shows it as printed, for an assistant or a person who can look.
  - **Board finding has limits.** It finds only boards printed in a frame, not a board without one or printed in part. The places it lists as unread can include a frame that holds no board, such as a photograph's. The text it gives beside a board can be incomplete: a caption further than a third of the board's side away is not given, and on a scan Tesseract can cut a caption or miss a label.
  - **Chess fonts stay codes.** A book whose diagrams are set in a chess font has its boards found and read, but the figurines in its moves stay the font's codes.

  So a text-only assistant may be unable to settle a scanned book's notation without a model or a tool that reads it from the page. Its support for scanned books is partial, and no text-only assistant has converted a book yet.

## Output files

The assistant chooses where each PGN file goes, and pgnify adds `.pgn` if the name lacks it:

- **Without `--out`**: the assistant gives the file's full path, in a folder you allowed.
- **With `--out`**: it gives a path within that folder, such as `Silman/Part Four`, or gives none, and the file takes the book's name, at the top of the folder.

pgnify writes these files:

- `file_name.pgn`: the games.
- `file_name.notes.md`, beside it when the assistant gives notes: the model's own account of what it could not resolve, what it left out and the conventions it chose.
- The working folder: `.pgnify/` inside `--out`, or `~/.local/state/pgnify/` without `--out`, which several sessions can share. It holds:
  - a log of every tool call;
  - the book each PGN file came from;
  - a folder for each book, with the pictures the server extracted, the pages it showed, the games it saved and the pieces it read the book's figurines as.

Without `--out`, games saved by name with `build_pgn` belong to the session that saved them, so after a restart the assistant must build them again. PGN files already written are unaffected.

## Check the result

`pgnify report` checks an output folder, and `pgnify check` checks one PGN file, against its book if you give it:

```sh
pgnify report ~/Chess/PGN            # writes report.md and report.json there
pgnify report ~/Chess/PGN --images   # also saves each doubtful diagram beside its game's position
pgnify report ~/Chess/PGN --coverage all     # also lists the book's text no comment holds
pgnify report ~/Chess/PGN --coverage 46-103  # the same, for the pages you asked to convert
pgnify check games.pgn --book Workbook.epub
```

The report checks the PGN files at the top of the folder and the ones pgnify wrote in its subfolders. It reads pgnify's log and its record of the book each file came from, in the folder's `.pgnify/` and in `~/.local/state/pgnify/`. A book's findings cover every session in those logs that converted it. The report lists tool errors that belong to no PGN file only from the folder's `.pgnify/` log, since pgnify's own log holds every session's calls.

The report sorts its findings into four kinds:

- **Verified problems**: mechanical facts that need fixing, such as an illegal move, a starting position (FEN tag) that isn't legal, a missing result or one the game contradicts, or text a PGN reader would drop.
- **Representation limitations**: what PGN cannot hold as the book gives it, kept another way. A diagram that is no position, such as one without kings, keeps its placement in a Diagram tag, since no game can start from it. The report names the picture of the book that the diagram reader read as that placement, which shows the limitation is the book's, not the conversion's. Where no reading has the placement, nothing shows that the book prints it so, and the report gives a possible issue instead.
  - A viewer that opens such a game does not draw its diagram. The Diagram tag is pgnify's own, and viewers do not know it: chess.js and chessops, lichess's library, load the game as a text-only game and keep the tag only as a header. The game's comments then sit over the initial position, which every game without a FEN starts from. To see the diagram, set up the tag's placement, FEN's first field, in a board editor, or look at the book's picture.
  - A FEN tag that keeps such a diagram is a limitation too, where the diagram reader's reading backs it, but viewers that need a legal position will not open its game at all, as chess.js and chessops do not.
- **Possible issues**: observations for a person to judge.
  - In each PGN file: squares marked uncertain, a position given without moves, comment text not found word for word in the book, a passage given more than once, and a game that starts from a diagram read with doubts.
  - Where the book's text layer appears to be a machine reading of scanned pages, because its file names a capture or OCR tool or most of its pages with text lie over a picture of the page, comment text not found in it is of lower confidence: a faithful comment differs from such a layer wherever the layer misreads the page. `report.md` counts these apart, by game, with examples, and `report.json` keeps every one. A book whose file has no text layer at all is not compared.
  - For each book, from pgnify's logs of every session that converted it, in the folder's `.pgnify/` and in `~/.local/state/pgnify/`: a diagram whose position no game in the book's PGN files reaches, a diagram extracted but never read, a game the assistant built and never saved, and the tools' errors. A diagram counts as found when any of those files reaches it, so these findings show what the files hold together, not that any one conversion is complete.
- **The model's own account**: its notes, quoted as it gave them and not verified.

For a game's position, the report keeps three cases apart:

- **A position a viewer can show and play from**: the initial position, or a legal position given with SetUp and FEN.
- **A diagram that is no position, as the book prints it**: a representation limitation, where a reading of the book's picture backs it. Viewers open its game, but without the diagram.
- **A position the conversion got wrong**: a verified problem when its FEN tag is no position and no reading of the book backs it, and otherwise a possible issue, such as a Diagram tag that no reading backs, a diagram of the book that no game reaches, or a game that starts from a diagram read with doubts.

With `--coverage`, the report also lists **possible omissions**: the book's text, on every page or on the pages you name, that no comment of its PGN files holds word for word, in runs of 10 words or more, each with its pages and an excerpt. Name the pages of the part you asked for, as `doc_text` numbers them, so that a chapter's conversion is not held to the rest of the book. The list shows textual coverage only: words a comment holds may still be at the wrong move, and words left out may have been left out on purpose, such as the publisher's pages. Shorter runs, mostly headings, captions and labels, and text the book repeats, such as navigation labels, are counted, not listed. A passage the book gives twice and the comments once is listed at both places, since nothing tells which one they keep. Other parts of the book printed on the same pages show up too, and so do a scan's misreadings. A page without text, such as a scanned page without a text layer, gives nothing to compare: the report names it as not assessed, neither held nor missing.

A report without verified problems does not mean the conversion is complete or faithful. Without a reference PGN to compare with, pgnify cannot tell whether every game, exercise, variation or passage of the book was found; it reports only what it can check. The assistant's notes prove nothing either way: they can say nothing is missing when something is. Compare the PGN with the book where it matters.

`pgnify check` and `pgnify report` exit with status 1 when they find a verified problem, and with status 2 when they cannot read their input.

## Security considerations

pgnify checks every path the assistant gives it, but it is not a sandbox:

- **What pgnify reads**:
  - **Without `--books`**: each book you allow, named by its full path. pgnify refuses a relative path, or a file that isn't a PDF or EPUB, without asking. It asks about the file the path leads to once links are followed, so a link to a book you allowed counts as that book.
  - **With `--books`**: only the PDF and EPUB files in that folder and its subfolders, named by their path within it. pgnify asks nothing. It skips hidden files and folders, and any link that leads outside the folder, to a hidden file, or to anything but a PDF or EPUB.
- **What it writes**: PGN and notes files, and its working folder.
  - **Without `--out`**: only in folders you allow and the folders inside them, and in pgnify's own folder. When a path passes through a link, pgnify asks about the folder the link leads to.
  - **With `--out`**: only inside that folder, in folders with plain names and never through a link. pgnify asks nothing.
  - **Either way**: it never writes over a notes file it didn't write. It replaces a PGN file only if it wrote that file in this session from the same book, or if the assistant asks it to; without `--out`, it asks you before such a replace.
- **Only you can allow access**: where no flag is given, pgnify reads or writes a path only after your yes in the client, never because of anything the assistant writes. A refused request reads no book, writes no PGN or notes file and creates no folder.
- **pgnify's own folder**: sessions without `--out` share `~/.local/state/pgnify/`. It holds the pictures of the pages pgnify showed or extracted, the games the assistant saved and a log of every call, so keep it as private as the books.
- **Books kept separate**: each book's saved games, pictures and figurine readings go in a folder of their own, so two books served together never share them.
- **Paths in errors**: the tools' error messages show `<books>` and `<out>` for the folders `--books` and `--out` set, and `~` for your home folder.
- **Network**: the server makes no network connections. `pgnify setup` downloads the weights from GitHub only when you run it without `--from`.
- **Your rights**: the server, its diagram reader and Tesseract run as you, so a flaw in pgnify, or in a library it uses to read a book, could read or change any file you can. Allow only books and folders you mean it to use, and point `--books` and `--out` at folders that hold only books, or are for pgnify alone. Give it only files you would open in any other reader.
- **What your assistant sees**: whatever the tools return goes to your assistant, and through it to its model provider: the text of the pages it reads, the lines its searches find, and the pictures of the pages `doc_page` shows it, which it asks for to read a scanned page or a diagram set in a chess font. `doc_images` returns no pictures, only their paths, though an assistant with file tools could open the pictures pgnify saves in its working folder.

## Books and copyright

Use pgnify on books you own, for your own study. A PGN made with it holds the book's chess prose word for word, so treat it as you would a copy of the book, and don't share it unless you have the right to. pgnify never downloads, shares or publishes books. The only book in this repository is a short book written as a test fixture and dedicated to the public domain.

## Experimental: copying comments from the book

A span is a passage of the book's text, named by its page and line numbers. With `pgnify serve --spans`, `doc_text` numbers each page's lines, and `build_pgn` can copy a comment from the book by its spans, so that the model need not retype it. The option is off by default until a run shows a model using it well. On a scanned book, a span copies whatever misreadings the scan's text layer has. An OCR reading has no line numbers, so no span copies from it.

## Commands

| Command | What it does |
|---|---|
| `pgnify serve [--books PATH] [--out DIR] [--spans]` | runs the MCP server on stdin and stdout, and asks you about any book or folder that `--books` or `--out` doesn't set |
| `pgnify report DIR [--images] [--coverage PAGES]` | checks the PGN files in a folder that pgnify wrote to |
| `pgnify check FILE.pgn [--book BOOK]` | checks one PGN file |
| `pgnify setup [--from DIR]` | installs the diagram reader's weights |
| `pgnify instructions` | prints the conversion instructions |
| `pgnify --version` | prints the version |

`PGNIFY_CACHE` changes where `pgnify setup` keeps the weights, by default `~/.cache/pgnify`. `PGNIFY_WEIGHTS` names another folder for them instead, which holds them in its `chess/` subfolder.

## Development

```sh
git clone https://github.com/dawsonbooth/pgnify && cd pgnify
just sync            # install the package and the dev tools
just check           # ruff, ty and the tests
just models          # the diagram reader and its weights; just models --from DIR copies them
just test -m live    # the tests that read diagrams
```

- `docs/architecture.md`: how pgnify works, and why.
- `experiments/`: the evaluation, which runs an assistant through the packaged server on parts of books and scores the PGN against reference PGNs. It finds the books through `data/local/manifest.toml`, whose shape `data/local/manifest.example.toml` shows. The books, the reference PGNs and the runs are not in this repository.

## Licence

pgnify is free software under the GNU Affero General Public License, version 3 or later (`LICENSE`). It is under the AGPL because it uses PyMuPDF (AGPL-3.0) and python-chess (GPL-3.0-or-later). The diagram reader includes code from [Chess_diagram_to_FEN](https://github.com/tsoj/Chess_diagram_to_FEN), under the MIT licence; its weights are downloaded from that project's release and never distributed with pgnify. `NOTICE` lists the third-party code and the dependencies with their licences.
