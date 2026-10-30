import cv2
import numpy as np
import torch

from . import consts

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _order_corners_tl_tr_br_bl(pts: np.ndarray) -> np.ndarray:
    """Sort 4 points into TL, TR, BR, BL order."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([
        pts[d.argmax()],   # TR: largest x-y
        pts[s.argmin()],   # TL: smallest x+y
        pts[d.argmin()],   # BL: smallest x-y
        pts[s.argmax()],   # BR: largest x+y
    ], dtype=np.float32)


def mask_to_corners(mask: torch.Tensor, threshold: float = 0.5) -> torch.Tensor | None:
    """Extract 4 quad corners from a mask [H, W] using contour approximation.

    Returns [4, 2] tensor of (x, y) pixel coordinates in order TL, TR, BR, BL,
    or None if the mask is empty.
    """
    mask_np = (mask > threshold).cpu().numpy().astype(np.uint8)

    contours, _ = cv2.findContours(mask_np, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # Keep only the largest contour to ignore noise/specs
    largest_contour = max(contours, key=cv2.contourArea)

    # Binary search for epsilon that yields exactly 4 points
    peri = cv2.arcLength(largest_contour, True)
    min_eps, max_eps = 0.0, 0.2
    best_approx = None

    for _ in range(20):
        eps = ((min_eps + max_eps) / 2.0) * peri
        approx = cv2.approxPolyDP(largest_contour, eps, True)

        if len(approx) == 4:
            best_approx = approx
            break
        elif len(approx) > 4:
            min_eps = (min_eps + max_eps) / 2.0
        else:
            max_eps = (min_eps + max_eps) / 2.0

    # Fallback: minimum area rotated rectangle
    if best_approx is None:
        rect = cv2.minAreaRect(largest_contour)
        best_approx = cv2.boxPoints(rect)

    pts = _order_corners_tl_tr_br_bl(best_approx.reshape(4, 2))
    return torch.from_numpy(pts)


def get_quad(model, img: torch.Tensor):
    """Run inference and return 4 corner points, or None.

    Accepts an image tensor in [0, 1] range with shape [C, H, W].
    Returns corners as [4, 2] tensor in BBOX_IMAGE_SIZE pixel coordinates.
    Corner order: TL, TR, BR, BL.
    """
    model.eval()
    model.to(device)
    with torch.no_grad():
        assert (
            len(img.shape) == 3
        ), "Need input to be of shape [C, H, W] but is: " + str(img.shape)
        assert img.shape[0] == 3, "Channel dimension must be 3 (RGB)"

        logits = model(img.unsqueeze(0).to(device))  # [1, 1, H, W]
        mask = torch.sigmoid(logits).squeeze(0).squeeze(0).cpu()  # [H, W]

        corners = mask_to_corners(mask)

    return corners, mask
