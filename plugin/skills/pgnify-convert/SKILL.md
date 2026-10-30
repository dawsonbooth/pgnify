---
name: pgnify-convert
description: Convert part of a chess book (PDF or EPUB) into faithful PGN with the pgnify MCP server's tools, keeping its games, exercises and solutions, its variations, those given in prose included, and its commentary word for word. Use when the user asks to turn games, exercises or commentary from a chess book into PGN and pgnify's tools are connected.
---

This skill needs pgnify's MCP server, connected with the books it may read and the folder it may write: see https://github.com/dawsonbooth/pgnify#connect-it-to-your-assistant.

# Converting a chess book to PGN with pgnify

pgnify's tools read the book, check every move, and write the PGN. These instructions say what a faithful conversion keeps; how to go about it, and how to divide the work, is yours to decide.

Keep everything chess-relevant the book gives for the part you are asked to convert, faithfully and in the book's order:

- each game, exercise and position, with its starting position, its moves and its variations;
- each exercise's solution, wherever the book prints it, in full;
- the book's words about them, word for word, as comments where they belong: hints, solution commentary, commentary on moves and variations. Give each passage once, at its place; don't repeat it, or put it in other words, in a variation's comment unless the book itself repeats it.

Follow the book's structure: what it presents as a game, an exercise or a solution, and how it numbers them. Where the user asks for an output convention, such as one game for each exercise with its solution's moves, or a numbering for Round, follow it. Where they ask for none, choose one that keeps everything above, and say which in your notes.

A variation is play the book presents as an alternative to a move of the game or of another variation: a condition ("if 31...Kg7, then 32 Qe5+"), a recommendation ("better was 34 Rd1"), a choice ("33 Rb1 or 33 Rc1"), a refutation ("38...Ra6 fails to 39 Bxa6"). It may be a single move or a short line, and the book may give it in its prose rather than set apart as moves; a move given in words, such as "if he castles queenside", counts when only one legal move fits. A comparison ("no better than the quiet 35 a3"), a threat, a plan, play after a move the book does not name, and a move mentioned only in passing are not variations: their words stay in the comments. Where you cannot tell, leave the words in the comments and say so in your notes.

A diagram's position is what the diagram shows. Where the diagram reader doubts a square and neither the book's text nor legality settles it, never choose a piece for it to make a game playable: keep the piece the reader found there if the book's moves are legal with it, otherwise leave the square empty, and name each such square in a tag, as [Uncertain "e2 h4"], and in your notes. If the book's moves cannot be played even so, keep them in the comments as the book prints them.

Headers: Event, the book's title; Site, Round, White and Black as the book gives them, else "?"; Date as the book gives it, else "????.??.??"; Result, as the book gives it, else "*"; SetUp "1" and FEN for a game that does not start from the initial position; Annotator, the book's author, where the comments are theirs. Add any other tags the user asks for.

Never invent what the book does not give. When you write the PGN, give write_pgn your notes: what you could not resolve, what you left out and why, and the conventions you chose; say the same in your final answer. pgnify reports what it can check separately from your notes.
