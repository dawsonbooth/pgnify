# EPUB fixtures

## pocket-primer/

*The Pocket Endgame Primer* by Hester Quill, a short book written as a test fixture. The author is invented. The text, the stylesheet and the images are dedicated to the public domain under CC0 1.0.

The book is committed unzipped, so that a diff shows its text. `tests/conftest.py` zips it into an EPUB the same way every time, so its contents hash the same on every run.

It is an EPUB 2 package with a table of contents in `toc.ncx`, as many publishers' EPUBs are, and it has structures that real chess books use:

- a manifest in a different order from the spine;
- contents entries that point at a document, at a heading by its id, and at an anchor inside a heading;
- headings the contents do not list: in the markup, and marked by style alone (`p.subhead` in the font shorthand, `p.group` in percent), and a note title that its class makes bold and large but a style attribute makes small again;
- a chapter number in a paragraph of its own before the chapter title, and a heading split over two elements at one level;
- a cover drawn as an image inside SVG, a headed page of praise before the contents' first entry, a title page, an advertisement page the contents do not list, a contents page and an index;
- diagrams in PNG, JPEG, GIF and SVG, with captions, one with its caption in the same paragraph and one laid out in a table; a photograph and a cover that are not square, a small icon inside a paragraph and a symbol drawn in SVG;
- moves in bold paragraphs with English letters and with Unicode figurines in styled spans, a table of moves whose cells hold paragraphs, and a game in a `pre`;
- a named entity, `&nbsp;`, under an XHTML DTD that a reader need not load.

Every line of moves the book prints was played through with python-chess from the position its diagram shows. The diagrams are plain boards with a dot on each occupied square, not drawn for the diagram reader.
