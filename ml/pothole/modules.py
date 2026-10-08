"""
modules.py
----------
Custom architecture modules for Phase 2 YOLO26l-seg modification:

  DSConv        — Dynamic Snake Convolution (drop-in Conv replacement)
  SimAM         — Simple Parameter-Free Attention Module
  C3k2WithSimAM — Wrapper: applies SimAM after any C3k2 block

References:
  DSConv  : "Dynamic Snake Convolution based on Topological Geometric
             Constraints for Tubular Structure Segmentation" (ICCV 2023)
             github.com/YaoleiQi/DSCNet
  SimAM   : "SimAM: A Simple, Parameter-Free Attention Module for
             Convolutional Neural Networks" (ICML 2021)
             github.com/ZjjConan/SimAM
  Paper   : arXiv 2505.04207 — YOLO + DSConv/SimAM/GELU for road distress
"""

import torch
import torch.nn as nn

# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def autopad(k, p=None, d=1):
    """Same-padding helper — mirrors Ultralytics autopad exactly."""
    if d > 1:
        k = d * (k - 1) + 1
    if p is None:
        p = k // 2
    return p


# ─────────────────────────────────────────────────────────────────────────────
# SimAM — Simple Parameter-Free Attention Module
# ─────────────────────────────────────────────────────────────────────────────

class SimAM(nn.Module):
    """
    Parameter-free 3-D attention (channel × height × width).

    Each neuron's importance is scored by how much it deviates from
    its spatial neighbourhood — high deviation (salient pothole pixel)
    gets a high weight; uniform asphalt texture is suppressed.

    Formula  (ZjjConan/SimAM):
        n     = H*W - 1
        d     = (x - mean(x))²
        v     = sum(d) / n          ← spatial variance proxy
        E_inv = d / (4*(v + λ)) + 0.5
        out   = x * sigmoid(E_inv)

    Zero extra parameters — adds negligible inference overhead.
    """

    def __init__(self, e_lambda: float = 1e-4):
        super().__init__()
        self.e_lambda = e_lambda

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, c, h, w = x.shape
        n     = h * w - 1
        d     = (x - x.mean(dim=[2, 3], keepdim=True)).pow(2)
        v     = d.sum(dim=[2, 3], keepdim=True) / n
        E_inv = d / (4 * (v + self.e_lambda)) + 0.5
        return x * torch.sigmoid(E_inv)

    def extra_repr(self):
        return f"e_lambda={self.e_lambda}"


# ─────────────────────────────────────────────────────────────────────────────
# C3k2WithSimAM — Transparent wrapper for in-place model surgery
# ─────────────────────────────────────────────────────────────────────────────

class C3k2WithSimAM(nn.Module):
    """
    Wraps an existing C3k2 module with a trailing SimAM attention pass.

    Used so we can swap C3k2 → C3k2WithSimAM in-place during model
    surgery (modify_model.py) without changing any layer indices or
    skip-connection references in the YOLO graph.

    forward: x → C3k2 → SimAM → output   (same shape as C3k2 alone)
    """

    def __init__(self, c3k2_module: nn.Module, e_lambda: float = 1e-4):
        super().__init__()
        self.c3k2  = c3k2_module
        self.simam = SimAM(e_lambda)

    def forward(self, x):
        return self.simam(self.c3k2(x))

    def extra_repr(self):
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# DSConv — Dynamic Snake Convolution
# ─────────────────────────────────────────────────────────────────────────────

class DSConv(nn.Module):
    """
    Dynamic Snake Convolution — drop-in replacement for Ultralytics Conv.

    Standard 3×3 convolutions sample a rigid square grid, which is a poor
    fit for winding, elongated pothole and crack geometries.  DSConv learns
    deformation offsets with a SNAKE CONTINUITY CONSTRAINT: the offsets for
    successive kernel positions are accumulated (cumsum), so the sampling
    points form a connected, contiguous path — not scattered arbitrarily
    the way generic Deformable Convolution (DCN) allows.

    Interface is identical to Ultralytics Conv so it can be swapped in-place:
        DSConv(c1, c2, k=3, s=1, p=None, g=1, d=1, act=True)

    Fallback: when k==1 there is nothing to "snake", so a standard
    pointwise conv is used (no deformation needed for 1×1 kernels).

    Requires: torchvision  (for deform_conv2d CUDA kernel)
    """

    def __init__(self, c1, c2, k=3, s=1, p=None, g=1, d=1, act=True):
        super().__init__()
        self.c1   = c1
        self.c2   = c2
        self.k    = k
        self.s    = s
        self.d    = d
        self.g    = g
        self.pad  = autopad(k, p, d)
        self.k1   = (k == 1)   # flag — skip deformation for 1×1 kernels

        if self.k1:
            # 1×1 kernel: plain conv (no deformation makes sense)
            self.conv = nn.Conv2d(c1, c2, 1, s, 0, groups=g, bias=False)
        else:
            # Learnable conv weights (same layout as nn.Conv2d)
            self.weight = nn.Parameter(torch.empty(c2, c1 // g, k, k))
            nn.init.kaiming_uniform_(self.weight, a=0, mode='fan_in',
                                     nonlinearity='relu')

            # Offset generator → 2*k*k channels (Δrow, Δcol per kernel point)
            # stride=s so output spatial dims match deform_conv2d output
            self.offset_conv = nn.Conv2d(
                c1, 2 * k * k,
                kernel_size=k, stride=s, padding=self.pad,
                dilation=d, bias=True
            )
            nn.init.zeros_(self.offset_conv.weight)
            nn.init.zeros_(self.offset_conv.bias)

            # Modulation mask → k*k channels (sigmoid-gated per kernel point)
            self.mask_conv = nn.Conv2d(
                c1, k * k,
                kernel_size=k, stride=s, padding=self.pad,
                dilation=d, bias=True
            )
            nn.init.zeros_(self.mask_conv.weight)
            nn.init.constant_(self.mask_conv.bias, 0.5)

        # BN + activation (GELU — Phase 2 requirement, replaces SiLU)
        self.bn  = nn.BatchNorm2d(c2)
        if act is True:
            self.act = nn.GELU()
        elif isinstance(act, nn.Module):
            self.act = act
        else:
            self.act = nn.Identity()

    # ── Snake constraint ──────────────────────────────────────────────────────

    def _snake_offsets(self, raw: torch.Tensor) -> torch.Tensor:
        """
        Enforce continuity by cumulatively summing offsets along the
        linearised kernel-position axis (dim=1).

        raw : (B, 2*k*k, H_out, W_out)
        Each pair (Δrow_i, Δcol_i) is relative to the PREVIOUS kernel
        point, not to the centre — so the kernel traces a connected path.
        """
        k2 = self.k * self.k
        drow = raw[:, :k2].cumsum(dim=1)   # accumulated row offsets
        dcol = raw[:, k2:].cumsum(dim=1)   # accumulated col offsets
        return torch.cat([drow, dcol], dim=1)   # (B, 2*k*k, H_out, W_out)

    # ── Forward ───────────────────────────────────────────────────────────────

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.k1:
            return self.act(self.bn(self.conv(x)))

        from torchvision.ops import deform_conv2d   # lazy import (avoids top-level dep check)

        offsets = self._snake_offsets(self.offset_conv(x))
        mask    = torch.sigmoid(self.mask_conv(x))

        out = deform_conv2d(
            input   = x,
            offset  = offsets,
            weight  = self.weight,
            bias    = None,
            stride  = self.s,
            padding = self.pad,
            dilation= self.d,
            mask    = mask,
        )
        return self.act(self.bn(out))

    def extra_repr(self):
        return (f"c1={self.c1}, c2={self.c2}, k={self.k}, "
                f"s={self.s}, pad={self.pad}, snake={'off' if self.k1 else 'on'}")
