"""Turns pixels into facts ("join_ready", "in_battle", "lobby").

A preset is compiled once per client size: ratio positions become client pixels, the minimal capture
box is computed, and template images are loaded and scaled. Evaluation is then a few numpy reads.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from . import imaging, paths
from .config import PixelCheck, Preset, Trigger

log = logging.getLogger("triggers")


@dataclass
class View:
    """A captured BGRA image plus the client coordinate of its top-left pixel."""
    img: np.ndarray
    ox: int
    oy: int

    def rgb_at(self, cx: int, cy: int) -> tuple[int, int, int] | None:
        x, y = cx - self.ox, cy - self.oy
        h, w = self.img.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return None
        patch = self.img[max(y - 1, 0):y + 2, max(x - 1, 0):x + 2, :3].reshape(-1, 3).mean(axis=0)
        b, g, r = patch
        return int(r), int(g), int(b)


def color_dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


@dataclass
class _CPixel:
    cx: int
    cy: int
    rgb: tuple[int, int, int]
    tol: int


@dataclass
class _CTemplate:
    box: tuple[int, int, int, int]            # client px l,t,r,b
    tmpl: np.ndarray | None                   # gray, already scaled
    thr: float


@dataclass
class CompiledPreset:
    preset: Preset
    cw: int
    ch: int
    pixels: dict[str, list[_CPixel]] = field(default_factory=dict)
    modes: dict[str, str] = field(default_factory=dict)
    templates: dict[str, _CTemplate] = field(default_factory=dict)
    negations: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    box: tuple[int, int, int, int] = (0, 0, 1, 1)

    def point_px(self, name: str) -> tuple[int, int]:
        p = self.preset.points[name]
        return self.ratio_px(p.x, p.y)

    def ratio_px(self, rx: float, ry: float) -> tuple[int, int]:
        return min(int(round(rx * self.cw)), self.cw - 1), min(int(round(ry * self.ch)), self.ch - 1)

    def evaluate(self, view: View | None) -> dict[str, bool]:
        out: dict[str, bool] = {}
        if view is None:
            return {n: False for n in self.order}
        for name in self.order:
            if name in self.pixels:
                hits = []
                for c in self.pixels[name]:
                    rgb = view.rgb_at(c.cx, c.cy)
                    hits.append(rgb is not None and color_dist(rgb, c.rgb) <= c.tol)
                out[name] = all(hits) if self.modes[name] == "all" else any(hits)
            elif name in self.templates:
                out[name] = self._match_template(self.templates[name], view)
            elif name in self.negations:
                out[name] = not out.get(self.negations[name], False)
        return out

    def _match_template(self, t: _CTemplate, view: View) -> bool:
        if t.tmpl is None:
            return False
        l, tp, r, b = t.box
        roi = view.img[tp - view.oy:b - view.oy, l - view.ox:r - view.ox]
        th, tw = t.tmpl.shape[:2]
        if roi.shape[0] < th or roi.shape[1] < tw:
            return False
        return imaging.match_score(imaging.bgra_to_gray(roi), t.tmpl) >= t.thr


def _check_px(cp: CompiledPreset, c: PixelCheck) -> tuple[int, int]:
    if c.at is not None:
        return cp.point_px(c.at)
    return cp.ratio_px(c.x, c.y)


def _load_template(trig: Trigger, cw: int, base_w: int) -> np.ndarray | None:
    path = paths.TEMPLATES_DIR / trig.img
    img = imaging.load_gray(path)
    if img is None:
        log.error("template image not found: %s", path)
        return None
    scale = cw / float(trig.img_client_w or base_w)
    if abs(scale - 1.0) > 0.01:
        img = imaging.resize_gray(img, scale)
    return img


def compile_preset(preset: Preset, cw: int, ch: int) -> CompiledPreset:
    cp = CompiledPreset(preset=preset, cw=cw, ch=ch)
    xs: list[int] = []
    ys: list[int] = []
    for name, trig in preset.triggers.items():
        if trig.type == "pixels":
            cp.pixels[name] = []
            cp.modes[name] = trig.mode
            for c in trig.checks:
                x, y = _check_px(cp, c)
                cp.pixels[name].append(_CPixel(x, y, tuple(c.rgb), c.tol))
                xs += [x - 2, x + 2]
                ys += [y - 2, y + 2]
        elif trig.type == "template":
            l, t, r, b = trig.region
            box = (int(l * cw), int(t * ch), int(r * cw), int(b * ch))
            cp.templates[name] = _CTemplate(box, _load_template(trig, cw, preset.base_resolution[0]), trig.thr)
            xs += [box[0], box[2]]
            ys += [box[1], box[3]]
    # base triggers first, negations last
    cp.order = [n for n, t in preset.triggers.items() if t.type != "not"] + \
               [n for n, t in preset.triggers.items() if t.type == "not"]
    cp.negations = {n: t.of for n, t in preset.triggers.items() if t.type == "not"}
    if xs:
        cp.box = (max(min(xs), 0), max(min(ys), 0), min(max(xs) + 1, cw), min(max(ys) + 1, ch))
    else:
        cp.box = (0, 0, 1, 1)
    return cp


class Debouncer:
    """A trigger counts as true only after `debounce` consecutive true frames."""

    def __init__(self, preset: Preset):
        self.need = {n: t.debounce for n, t in preset.triggers.items()}
        self.streak = {n: 0 for n in preset.triggers}

    def feed(self, raw: dict[str, bool]) -> dict[str, bool]:
        out = {}
        for n, v in raw.items():
            self.streak[n] = self.streak.get(n, 0) + 1 if v else 0
            out[n] = self.streak[n] >= self.need.get(n, 1)
        return out
