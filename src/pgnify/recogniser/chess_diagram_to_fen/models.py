"""Chess_diagram_to_FEN's five models, from its src/*/model.py, as they are at
3d751125357ebd47b91f6a8168e0b8d513fe7aae, with the quarter turns the rotation model
tells apart, from its dataset module.

Upstream builds four of the models on torchvision's pretrained weights, downloading
them, and then loads its own over them, which give every parameter. Here each is built
without them, so that nothing is downloaded. The modules' `__main__` blocks are left
out, and the code is reformatted.
"""

import torch
import torch.nn as nn
import torchvision.models as models
from torchvision.models.segmentation import lraspp_mobilenet_v3_large
from torchvision.models.segmentation.lraspp import LRASPPHead

from . import consts
from .games import GameSpec, get_game

ROTATIONS = [0, 90, 180, 270]
"""The quarter turns the rotation model tells apart, from its dataset module."""


class BoardExistence(nn.Module):
    def __init__(self):
        super().__init__()

        self.model = models.regnet_x_800mf(weights=None)
        self.model.fc = torch.nn.LazyLinear(out_features=1)

        # print(self.model)
        # assert False

    def forward(self, x):
        return self.model(x)


class BoardQuad(nn.Module):
    def __init__(self):
        super().__init__()

        self.model = lraspp_mobilenet_v3_large(weights=None, weights_backbone=None)
        self.model.classifier = LRASPPHead(40, 960, 1, 128)

    def forward(self, x):
        return self.model(x)["out"]  # [B, 1, H, W] raw logits


class ImageRotation(nn.Module):
    def __init__(self):
        super().__init__()

        self.model = models.regnet_x_800mf(weights=None)
        self.model.fc = torch.nn.LazyLinear(out_features=len(ROTATIONS))

        # print(self.model)
        # assert False

    def forward(self, x):
        return self.model(x)


class OrientationModel(nn.Module):
    def __init__(self, game: str | GameSpec = "chess"):
        super().__init__()
        spec = get_game(game)
        self.board_rows = spec.board_rows
        self.board_cols = spec.board_cols
        in_channels = len(spec.piece_symbols) + 1

        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.Conv2d(64, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),
        )

        self.head = nn.Sequential(
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(32, 1),
        )

    def forward(self, x):
        batch = x.size(0)
        # x: [batch, num_squares, channels] -> [batch, channels, rows, cols]
        x = x.view(batch, self.board_rows, self.board_cols, -1)
        x = x.permute(0, 3, 1, 2)
        x = self.conv(x)
        x = x.view(batch, -1)
        x = self.head(x)
        return x


TILE_EMBED_DIM = 512
FULL_EMBED_DIM = 512


def get_tile_model():
    result = models.convnext_tiny(weights=None)
    result.classifier = nn.Sequential(
        torch.nn.Flatten(start_dim=1, end_dim=-1),
        torch.nn.Linear(in_features=768, out_features=TILE_EMBED_DIM),
        nn.ReLU(),
    )
    return result


def get_full_img_model():
    result = models.convnext_tiny(weights=None)
    result.classifier = nn.Sequential(
        torch.nn.Flatten(start_dim=1, end_dim=-1),
        torch.nn.Linear(in_features=768, out_features=FULL_EMBED_DIM),
        nn.ReLU(),
    )
    return result


class BoardRec(nn.Module):
    def __init__(self, game: str, tile_size: int = consts.DEFAULT_TILE_SIZE, dropout: float = 0.1):
        super().__init__()
        self.game = get_game(game)
        self.tile_size = tile_size
        self.board_h, self.board_w = consts.board_pixel_size(self.game, tile_size)
        self.num_squares = self.game.num_squares
        self.out_channels = len(self.game.piece_symbols) + 1

        self.tile = get_tile_model()
        self.full = get_full_img_model()

        # Positional embeddings added to tile features before concatenation
        self.tile_pos_embed = nn.Parameter(torch.zeros(1, self.num_squares, TILE_EMBED_DIM))
        nn.init.trunc_normal_(self.tile_pos_embed, std=0.02)

        # Projection from concatenated features to attention dimension
        self.embed_dim = 512
        self.pre_attn_dense = nn.Sequential(
            nn.Linear(TILE_EMBED_DIM + FULL_EMBED_DIM, self.embed_dim),
            nn.ReLU(),
        )

        # Transformer block: Attention → Add&Norm → FFN → Add&Norm
        self.attention = nn.MultiheadAttention(
            embed_dim=self.embed_dim,
            num_heads=8,
            dropout=dropout,
            batch_first=True,
        )
        self.attn_norm = nn.LayerNorm(self.embed_dim)
        self.ffn = nn.Sequential(
            nn.Linear(self.embed_dim, self.embed_dim * 4),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(self.embed_dim * 4, self.embed_dim),
            nn.Dropout(dropout),
        )
        self.ffn_norm = nn.LayerNorm(self.embed_dim)

        # Classification head
        self.dense = nn.Sequential(
            nn.Linear(self.embed_dim, 768),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(768, 512),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(512, self.out_channels),
        )

    def forward(self, img):
        batch_size, ch, h, w = img.shape

        assert h == self.board_h
        assert w == self.board_w
        assert ch == 3

        x = img
        x = x.unfold(2, self.tile_size, self.tile_size)
        x = x.unfold(3, self.tile_size, self.tile_size)
        x = x.permute(0, 2, 3, 1, 4, 5)

        x = x.reshape(
            batch_size * self.game.board_rows * self.game.board_cols,
            ch,
            self.tile_size,
            self.tile_size,
        )

        x = self.tile(x)
        x = x.reshape(batch_size, self.num_squares, -1)

        # Add positional embeddings to tile features so each square has a unique identity
        x = x + self.tile_pos_embed

        z = self.full(img)
        z = z.reshape(batch_size, 1, -1)
        z = z.expand(-1, self.num_squares, -1)

        # Concatenate position-aware tile features with global image features
        x = torch.cat((x, z), dim=-1)

        # Project to attention dimension
        x = self.pre_attn_dense(x)

        # Transformer block
        attn_out, _ = self.attention(x, x, x)
        x = self.attn_norm(x + attn_out)

        ffn_out = self.ffn(x)
        x = self.ffn_norm(x + ffn_out)

        # Per-square classification
        x = x.reshape(batch_size * self.num_squares, -1)
        x = self.dense(x)
        x = x.reshape(batch_size, self.num_squares, self.out_channels)

        return x
