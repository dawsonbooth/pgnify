"""Reading a picture of a chess diagram into a position, as Chess_diagram_to_FEN's
chess_diagram_to_fen.py does it at 3d751125357ebd47b91f6a8168e0b8d513fe7aae.

The steps are upstream's, unchanged: whether the picture holds a board, the board's
four corners and the warp to a square, which quarter turn the warped board is in,
the piece on each square, and whether the board is printed from Black's side. What
differs, for the recogniser:

- The models are loaded from a directory given, not from the script's own.
- They run on a Mac's GPU where there is one, where upstream runs them on the CPU,
  but for the board finder, which runs on the CPU as upstream's does.
- get_fen keeps what it computes on the way and upstream does not return: the
  position model's scores for each square, and the warped board's corners in the
  picture given.
- Only chess is read, with no demo and no plotting.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import torch
from PIL import Image
from torchvision.transforms import functional, v2

from . import common, consts
from .board_finder import get_quad
from .games import CHESS
from .models import ROTATIONS, BoardExistence, BoardQuad, BoardRec, ImageRotation, OrientationModel

device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

rotation_transforms = torch.nn.Sequential(
    v2.ToDtype(torch.float32),
    v2.Resize(
        size=(consts.BOARD_PIXEL_WIDTH, consts.BOARD_PIXEL_WIDTH),
        interpolation=v2.InterpolationMode.BICUBIC,
    ),
    common.MinMaxMeanNormalization(),
)
"""The rotation model's input, as its dataset module's default_transforms makes it."""

position_transforms = torch.nn.Sequential(
    v2.ToDtype(torch.float32),
    v2.Resize(
        size=consts.board_pixel_size(CHESS, consts.DEFAULT_TILE_SIZE),
        interpolation=v2.InterpolationMode.BICUBIC,
    ),
    common.MinMaxMeanNormalization(),
)
"""The position model's input, as its dataset module's get_default_transforms makes it."""


@dataclass(frozen=True)
class Models:
    existence: BoardExistence
    bbox: BoardQuad
    image_rotation: ImageRotation
    fen: BoardRec
    orientation: OrientationModel

    @classmethod
    def load(cls, directory: Path) -> Models:
        """Upstream's models for chess, from the one file of each kind somewhere under
        `directory`, as release 1.0's models.zip unpacks."""

        def loaded(model: torch.nn.Module, kind: str) -> torch.nn.Module:
            found = sorted(directory.rglob(f"chess/best_model_{kind}_*.pth"))
            if len(found) != 1:
                raise FileNotFoundError(f"{len(found)} chess {kind} models under {directory}")
            model.load_state_dict(torch.load(found[0], map_location=torch.device("cpu")))
            model.to(device)
            model.eval()
            return model

        return cls(
            existence=loaded(BoardExistence(), "existence"),
            bbox=loaded(BoardQuad(), "quad"),
            image_rotation=loaded(ImageRotation(), "image_rotation"),
            fen=loaded(BoardRec(game="chess"), "position"),
            orientation=loaded(OrientationModel(game="chess"), "orientation"),
        )


@torch.no_grad()
def check_for_board_existence(img: Image.Image, models: Models) -> bool:
    img_tensor = common.to_rgb_tensor(img)
    img_tensor = functional.resize(img_tensor, [consts.BBOX_IMAGE_SIZE, consts.BBOX_IMAGE_SIZE])
    img_tensor = img_tensor.to(device)
    img_tensor = common.MinMaxMeanNormalization()(img_tensor)

    output = models.existence(img_tensor.unsqueeze(0)).squeeze(0)

    return output.cpu().item() > 0.5


def _perspective_warp(img: Image.Image, corners_px: torch.Tensor, output_size: int) -> Image.Image:
    """Warp img so that the quad defined by corners_px maps to a square.

    corners_px: [4, 2] tensor of (x, y) pixel coords in img space.
    Corner order: TL, TR, BR, BL.
    """
    import numpy as np
    from skimage.transform import ProjectiveTransform

    dst = np.array(
        [(0, 0), (output_size, 0), (output_size, output_size), (0, output_size)],
        dtype=np.float64,
    )
    src = np.array(corners_px.tolist(), dtype=np.float64)

    t = ProjectiveTransform.from_estimate(dst, src)

    coeffs = t.params.flatten()[:8]
    warped = img.transform(
        (output_size, output_size),
        Image.PERSPECTIVE,
        coeffs.tolist(),
        Image.BICUBIC,
    )
    return warped


@dataclass
class WarpResult:
    image: Image.Image = None
    mask: torch.Tensor = None
    corners: torch.Tensor = None  # [4, 2] in BBOX_IMAGE_SIZE space
    corners_px: torch.Tensor = None
    """The corners warped from, in the pixels of the picture given."""


@torch.no_grad()
def warp_to_board(img: Image.Image, models: Models, max_num_tries=10) -> WarpResult:
    pad_factor = 0.05
    pad_x = img.width * pad_factor
    pad_y = img.height * pad_factor

    img = common.pad(img, pad_x, pad_y)
    # Where the top left of the picture worked on lies in the picture given.
    left, top = -int(pad_x), -int(pad_y)

    for _ in range(max_num_tries):
        if img.width == 0 or img.height == 0:
            return WarpResult()

        img_tensor = common.to_rgb_tensor(img).float()
        img_tensor = functional.resize(img_tensor, [consts.BBOX_IMAGE_SIZE, consts.BBOX_IMAGE_SIZE])
        mn, mx = img_tensor.min(), img_tensor.max()
        img_tensor = (img_tensor - mn) / (mx - mn) if mx > mn else torch.zeros_like(img_tensor)

        corners, mask = get_quad(models.bbox, img_tensor)
        if corners is None:
            return WarpResult(mask=mask, corners=corners)

        # Scale corners from BBOX_IMAGE_SIZE space to original image space
        x_factor = img.width / consts.BBOX_IMAGE_SIZE
        y_factor = img.height / consts.BBOX_IMAGE_SIZE
        corners_px = corners.clone()
        corners_px[:, 0] *= x_factor
        corners_px[:, 1] *= y_factor

        # Compute bounding box of quad
        xs = corners_px[:, 0]
        ys = corners_px[:, 1]
        x1, x2 = xs.min().item(), xs.max().item()
        y1, y2 = ys.min().item(), ys.max().item()
        quad_w = x2 - x1
        quad_h = y2 - y1

        # Accept if quad is big relative to image
        if quad_w / img.width > 0.7 and quad_h / img.height > 0.7:
            # Compute output size from average side lengths
            side_lengths = []
            for j in range(4):
                p1 = corners_px[j]
                p2 = corners_px[(j + 1) % 4]
                side_lengths.append((p2 - p1).norm().item())
            avg_side = sum(side_lengths) / len(side_lengths)
            output_size = max(32, int(avg_side))

            warped = _perspective_warp(img, corners_px, output_size)
            in_picture = corners_px + torch.tensor([left, top], dtype=corners_px.dtype)
            return WarpResult(image=warped, mask=mask, corners=corners, corners_px=in_picture)

        # Crop closer to the quad and retry
        x_addition = quad_w * 0.1
        y_addition = quad_h * 0.1
        crop_x1 = int(max(x1 - x_addition, 0))
        crop_y1 = int(max(y1 - y_addition, 0))
        crop_x2 = int(min(x2 + x_addition, img.width))
        crop_y2 = int(min(y2 + y_addition, img.height))

        img = img.crop((crop_x1, crop_y1, crop_x2, crop_y2))
        left, top = left + crop_x1, top + crop_y1

    return WarpResult()


@torch.no_grad()
def board_image_rotation(img: Image.Image, models: Models) -> int:
    input_img = common.to_rgb_tensor(img)
    input_img = rotation_transforms(input_img).to(device)
    pred = models.image_rotation(input_img.unsqueeze(0)).cpu().squeeze(0).argmax().item()
    return pred


@torch.no_grad()
def is_board_flipped(board: common.Position, models: Models, no_rotate_bias=0.2) -> bool:
    board_tensor = common.position_to_tensor(board)
    output = models.orientation(board_tensor.unsqueeze(0).to(device)).squeeze(0).cpu()

    return output.item() - no_rotate_bias > 0.5


@torch.no_grad()
def rotate_board(board: common.Position) -> common.Position:
    board_tensor = common.position_to_tensor(board)
    board_tensor = common.rotate_tensor_180(board_tensor, CHESS)
    return common.tensor_to_position(board_tensor, CHESS)


@torch.no_grad()
def get_board_from_cropped_img(
    img: Image.Image, models: Models
) -> tuple[common.Position | None, torch.Tensor | None]:
    """The position read from a warped board, and the position model's scores as it
    gives them, before they are clamped to [0, 1] to read the position, for each square
    from the top left, row by row."""
    min_size = 32
    if img.width < min_size or img.height < min_size:
        return None, None

    img = common.to_rgb_tensor(img).to(device)
    input = position_transforms(img)

    if input.isnan().any():
        return None, None

    scores = models.fen(input.unsqueeze(0)).squeeze(0)
    output = scores.clamp(0, 1)

    board = common.tensor_to_position(output.cpu(), CHESS)
    if board.occupied == 0:
        return None, scores.cpu()
    return board, scores.cpu()


@dataclass
class FenResult:
    fen: str = None
    cropped_image: Image.Image = None
    bbox_corners: torch.Tensor = None  # [4, 2] in BBOX_IMAGE_SIZE space
    corners_px: torch.Tensor = None
    """The warped board's corners in the picture given, top left first as warped."""
    image_rotation_angle: int = None
    board_is_flipped: bool = None
    scores: torch.Tensor = None
    """The position model's scores, [64, 13], unclamped, for the board as warped and
    turned upright, before any turn for a board printed from Black's side."""


def get_fen(img: Image.Image, models: Models, auto_rotate_image=True, auto_rotate_board=True):
    """The position in a picture of a chess diagram, as upstream's get_fen reads it, or
    None if it finds no board."""
    img = img.convert("RGB")

    if not check_for_board_existence(img, models):
        return None

    result = FenResult()

    warp = warp_to_board(img, models)
    result.bbox_corners = warp.corners
    result.corners_px = warp.corners_px
    if warp.image is None:
        return result

    # Detect rotation on the warped board crop
    result.image_rotation_angle = board_image_rotation(warp.image, models)

    if auto_rotate_image:
        cropped = warp.image.rotate(-ROTATIONS[result.image_rotation_angle], expand=True)
    else:
        cropped = warp.image

    result.cropped_image = cropped
    board, result.scores = get_board_from_cropped_img(result.cropped_image, models)

    if board is not None:
        result.board_is_flipped = is_board_flipped(board, models)

        if auto_rotate_board and result.board_is_flipped:
            board = rotate_board(board)

        result.fen = board.fen()

    return result
