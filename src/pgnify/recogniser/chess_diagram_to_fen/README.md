# chess_diagram_to_fen

The reading half of [Chess_diagram_to_FEN](https://github.com/tsoj/Chess_diagram_to_FEN) at commit `3d751125357ebd47b91f6a8168e0b8d513fe7aae`, under its MIT licence in `LICENSE`. `pgnify setup` installs its release 1.0 weights; pgnify never ships them.

- `common.py`, `games.py` and `consts.py` are upstream's `src/` modules, and `board_finder.py` is its `src/bounding_box/inference.py`. Only their imports change, to relative ones, except the one inside `common.py`'s `get_image`, which still names upstream's board generator. That generator is not vendored, and nothing here calls `get_image`.
- `models.py` holds upstream's five models, from its `src/*/model.py`, and `ROTATIONS`, from its rotation dataset module. Upstream starts four of the models from pretrained torchvision weights and then loads its own weights over them; here they are built without the pretrained ones, so that nothing is downloaded.
- `reading.py` is upstream's `chess_diagram_to_fen.py`, cut to reading chess. It loads the models from a folder it is given, and runs them on Apple's GPU (MPS) when there is one, except the board finder, which stays on the CPU; upstream runs everything on the CPU. It also returns what get_fen computes and upstream drops: the position model's scores for each square, and the board's corners in the picture. pgnify added `from __future__ import annotations`, without which Python 3.13 evaluates `Models.load`'s annotation of its own class too early.
- `models.py` and `reading.py` are reformatted. pgnify's linting and type checking skip this package.

Training, drawing boards and the demo are left out, and with them most of upstream's dependencies, including cairosvg and matplotlib.
