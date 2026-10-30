# pgnify

pgnify lets the AI assistant you already use convert chess books, in PDF or EPUB, into Portable Game Notation (PGN).

It is a [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server. Your assistant reads the book and decides what it holds. pgnify's tools read the book's pages and diagrams, check every move with [python-chess](https://python-chess.readthedocs.io) and write the PGN. Its conversion instructions ask for every game, exercise and solution, the variations the book gives in its prose, and the book's commentary word for word. Afterwards, `pgnify report` checks the result without a model and lists what it verified and what a person should look at.

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

To update pgnify, run `uv tool upgrade pgnify`. It installs the repository's latest version, with the diagram reader if you installed it.

## Connect it to your assistant

Choose what pgnify reads and where it writes:

- `--books`: a book, or a folder of books. pgnify reads nothing else.
- `--out`: a folder for pgnify's output alone. pgnify writes nothing anywhere else.

### Claude Code

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

### Other MCP clients

Add a stdio server whose command is `pgnify serve --books PATH --out DIR`.

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

  The plugin holds only the skill, `pgnify-convert`. It does not register the server, so you always choose the folders yourself. For another assistant that reads skills, copy this repository's `plugin/skills/pgnify-convert/` into its skills folder.
- **As plain text**: `pgnify instructions` prints them, to paste into any assistant.

Then ask for the part you want, for instance: "Convert Chapter 5 of Workbook.epub, the rook forks with their solutions, into PGN." You can also ask for an output convention, such as one game for each exercise with its solution's moves, or a numbering for the Round tag. Without one, the assistant follows the book's structure and says in its notes which convention it chose.

Ask for one part at a time, such as a chapter or a section. The largest part tried so far is a chapter of 42 exercises and 6 examples, converted in one session.

## Output files

In the `--out` folder:

- `NAME.pgn`: the games. Unless the assistant gives another name, NAME is the book's.
- `NAME.notes.md`, when the assistant gave notes: the model's own account of what it could not resolve, what it left out and the conventions it chose.
- `.pgnify/`: pgnify's working folder. It holds a log of every tool call and, in a folder for each book, the pictures the server extracted, the games it saved and the pieces it read the book's figurines as.

## Check the result

`pgnify report` checks an output folder, and `pgnify check` checks one PGN file, against its book if you give it:

```sh
pgnify report ~/Chess/PGN            # writes report.md and report.json there
pgnify report ~/Chess/PGN --images   # also saves each doubtful diagram beside its game's position
pgnify check games.pgn --book Workbook.epub
```

The report sorts its findings into three kinds:

- **Verified problems**: mechanical facts that need fixing, such as an illegal move, a starting position (FEN tag) that isn't legal, a missing result or one the game contradicts, or text a PGN reader would drop.
- **Possible issues**: observations for a person to judge.
  - In each PGN file: squares marked uncertain, a position given without moves, comment text not found word for word in the book, a passage given more than once, and a game that starts from a diagram read with doubts.
  - For each book, from the server's log of every session in the folder: a diagram whose position no game in the book's PGN files reaches, a diagram extracted but never read, a game the assistant built and never saved, and the tools' errors. A diagram counts as found when any of those files reaches it, so these findings show what the files hold together, not that any one conversion is complete.
- **The model's own account**: its notes, quoted as it gave them and not verified.

A report without verified problems does not mean the conversion is complete or faithful. Without a reference PGN to compare with, pgnify cannot tell whether every game, exercise, variation or passage of the book was found; it reports only what it can check. The assistant's notes prove nothing either way: they can say nothing is missing when something is. Compare the PGN with the book where it matters.

`pgnify check` and `pgnify report` exit with status 1 when they find a verified problem, and with status 2 when they cannot read their input.

## Security considerations

pgnify checks every path the assistant gives it, but it is not a sandbox:

- **What pgnify reads**: only the PDF and EPUB files at `--books`, in that folder and its subfolders. It skips hidden files and folders, and any link that leads outside the folder, to a hidden file, or to anything but a PDF or EPUB. The assistant names a book by its path within the folder, never by an absolute path.
- **What it writes**: only into `--out`: PGN and notes files under plain file names, and its working folder `.pgnify/`. It never writes through a link or over a notes file it didn't write. It replaces a PGN file only if it wrote that file in the same session from the same book, or if the assistant asks it to.
- **Books kept separate**: each book's saved games, extracted pictures and figurine readings go in a folder of their own, so two books served together never share them.
- **Paths in errors**: the tools' error messages show `<books>`, `<out>` and `~` in place of your folders' paths.
- **Network**: the server makes no network connections. `pgnify setup` downloads the weights from GitHub only when you run it without `--from`.
- **Your rights**: the server and its diagram reader run as you, so a flaw in pgnify, or in a library it uses to read a book, could read or change any file you can. Point `--books` at a folder that holds only books, point `--out` at a folder for pgnify alone, and give it only files you would open in any other reader.
- **What your assistant sees**: whatever the tools return goes to your assistant, and through it to its model provider: the text of the pages it reads and the lines its searches find. pgnify's tools return no pictures, only the positions the diagram reader reads from them, though an assistant with file tools could open the pictures pgnify extracts into `--out`.

## Books and copyright

Use pgnify on books you own, for your own study. A PGN made with it holds the book's commentary word for word, so treat it as you would a copy of the book, and don't share it unless you have the right to. pgnify never downloads, shares or publishes books. The only book in this repository is a short book written as a test fixture and dedicated to the public domain.

## Experimental: copying comments from the book

A span is a passage of the book's text, named by its page and line numbers. With `pgnify serve --spans`, `doc_text` numbers each page's lines, and `build_pgn` can copy a comment from the book by its spans, so that the model need not retype it. The option is off by default until a run shows a model using it well. On a scanned book, a span copies whatever misreadings the scan's text layer has.

## Commands

| Command | What it does |
|---|---|
| `pgnify serve --books PATH --out DIR [--spans]` | runs the MCP server on stdin and stdout |
| `pgnify report DIR [--images]` | reports on a conversion's output folder |
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
