"""Blend-Modi und Alpha-Compositing.

Alle Funktionen arbeiten auf float32-Arrays im Bereich [0, 1] mit Form (H, W, 3)
für Farbe. Das entspricht der Rechenweise von Photoshop (separable Blend-Modi).

Referenz-Formeln: W3C Compositing & Blending Level 1 — deckt sich mit Photoshop
für die klassischen Modi.
"""
from __future__ import annotations

import numpy as np

EPS = 1e-9


def _multiply(b, s):
    return b * s


def _screen(b, s):
    return b + s - b * s


def _overlay(b, s):
    return _hard_light(s, b)


def _darken(b, s):
    return np.minimum(b, s)


def _lighten(b, s):
    return np.maximum(b, s)


def _color_dodge(b, s):
    out = np.where(s >= 1.0, 1.0, np.minimum(1.0, b / (1.0 - s + EPS)))
    return np.where(b <= 0.0, 0.0, out)


def _color_burn(b, s):
    out = np.where(s <= 0.0, 0.0, 1.0 - np.minimum(1.0, (1.0 - b) / (s + EPS)))
    return np.where(b >= 1.0, 1.0, out)


def _hard_light(b, s):
    return np.where(s <= 0.5, _multiply(b, 2 * s), _screen(b, 2 * s - 1))


def _soft_light(b, s):
    d = np.where(b <= 0.25, ((16 * b - 12) * b + 4) * b, np.sqrt(b))
    return np.where(
        s <= 0.5,
        b - (1 - 2 * s) * b * (1 - b),
        b + (2 * s - 1) * (d - b),
    )


def _difference(b, s):
    return np.abs(b - s)


def _exclusion(b, s):
    return b + s - 2 * b * s


def _linear_dodge(b, s):  # "Add"
    return np.minimum(1.0, b + s)


def _linear_burn(b, s):
    return np.maximum(0.0, b + s - 1.0)


def _vivid_light(b, s):
    return np.where(s <= 0.5, _color_burn(b, 2 * s), _color_dodge(b, 2 * s - 1))


def _linear_light(b, s):
    return np.clip(b + 2 * s - 1, 0.0, 1.0)


def _pin_light(b, s):
    return np.where(s <= 0.5, _darken(b, 2 * s), _lighten(b, 2 * s - 1))


def _normal(b, s):
    return s


# Name -> Funktion. Namen bewusst wie in Photoshop (klein, mit Unterstrich).
BLEND_MODES = {
    "normal": _normal,
    "multiply": _multiply,
    "screen": _screen,
    "overlay": _overlay,
    "darken": _darken,
    "lighten": _lighten,
    "color_dodge": _color_dodge,
    "color_burn": _color_burn,
    "hard_light": _hard_light,
    "soft_light": _soft_light,
    "difference": _difference,
    "exclusion": _exclusion,
    "linear_dodge": _linear_dodge,
    "add": _linear_dodge,
    "linear_burn": _linear_burn,
    "vivid_light": _vivid_light,
    "linear_light": _linear_light,
    "pin_light": _pin_light,
}


def composite(base: np.ndarray, src: np.ndarray, mode: str, opacity: float) -> np.ndarray:
    """Komponiert src ÜBER base.

    base, src: float32 (H, W, 4) RGBA im Bereich [0, 1], gleiche Form.
    mode: Schlüssel aus BLEND_MODES.
    opacity: 0..1 (Deckkraft der Quell-Ebene).
    Rückgabe: float32 (H, W, 4).
    """
    if mode not in BLEND_MODES:
        raise ValueError(
            f"Unbekannter Blend-Modus '{mode}'. Verfügbar: {', '.join(sorted(BLEND_MODES))}"
        )

    bc, ba = base[..., :3], base[..., 3:4]
    sc, sa = src[..., :3], src[..., 3:4]
    sa = sa * opacity  # Deckkraft in den Quell-Alpha einrechnen

    blended = BLEND_MODES[mode](bc, sc)
    # Für Bereiche, in denen base transparent ist, wirkt der Blend wie "normal".
    mixed = (1 - ba) * sc + ba * blended

    out_a = sa + ba * (1 - sa)
    safe_a = np.where(out_a < EPS, 1.0, out_a)
    out_c = (sa * mixed + ba * (1 - sa) * bc) / safe_a

    out = np.concatenate([out_c, out_a], axis=-1)
    return np.clip(out, 0.0, 1.0).astype(np.float32)
