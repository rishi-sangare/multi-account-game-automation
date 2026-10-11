"""Tiny image helpers (numpy + Pillow) so the build doesn't need the 110 MB OpenCV wheel."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image


def bgra_to_gray(img: np.ndarray) -> np.ndarray:
    """ITU-R 601 luma, same weights as OpenCV / Pillow 'L'."""
    b, g, r = img[..., 0].astype(np.float32), img[..., 1].astype(np.float32), img[..., 2].astype(np.float32)
    return 0.299 * r + 0.587 * g + 0.114 * b


def load_gray(path: Path) -> np.ndarray | None:
    try:
        return np.asarray(Image.open(path).convert("L"), dtype=np.float32)
    except OSError:
        return None


def resize_gray(img: np.ndarray, scale: float) -> np.ndarray:
    h, w = img.shape
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "L")
    im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BOX)
    return np.asarray(im, dtype=np.float32)


def save_png(path: Path, bgr_or_bgra: np.ndarray) -> None:
    Image.fromarray(np.ascontiguousarray(bgr_or_bgra[..., 2::-1])).save(path)


def png_bytes(bgr_or_bgra: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(bgr_or_bgra[..., 2::-1])).save(buf, format="PNG")
    return buf.getvalue()


def _window_sums(ii: np.ndarray, th: int, tw: int) -> np.ndarray:
    return ii[th:, tw:] - ii[:-th, tw:] - ii[th:, :-tw] + ii[:-th, :-tw]


def match_score(roi: np.ndarray, tmpl: np.ndarray) -> float:
    """Best normalised cross-correlation (TM_CCOEFF_NORMED) of `tmpl` inside `roi`, -1..1.

    Numerator by FFT correlation, denominators by integral images: O(N log N), small memory.
    """
    H, W = roi.shape
    th, tw = tmpl.shape
    if H < th or W < tw:
        return -1.0
    t0 = tmpl.astype(np.float64) - float(tmpl.mean())
    tnorm = float(np.sqrt((t0 * t0).sum()))
    if tnorm < 1e-6:
        return -1.0
    img = roi.astype(np.float64)
    spec = np.fft.rfft2(img) * np.fft.rfft2(t0[::-1, ::-1], s=(H, W))
    num = np.fft.irfft2(spec, s=(H, W))[th - 1:H, tw - 1:W]
    ii = np.pad(img, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    ii2 = np.pad(img * img, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    n = th * tw
    s1 = _window_sums(ii, th, tw)
    s2 = _window_sums(ii2, th, tw)
    var = np.maximum(s2 - s1 * s1 / n, 0.0)
    den = np.sqrt(var) * tnorm
    score = np.where(den > 1e-6, num / np.where(den > 1e-6, den, 1.0), 0.0)
    return float(score.max())
