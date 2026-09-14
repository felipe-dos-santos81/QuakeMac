"""Observation image encoding (Task 7).

Source frames arrive as raw bottom-up RGB straight from the engine's
glReadPixels readback (q_mcp_capture.c). Pillow flips them, optionally
crops and aspect-preserving downscales, then encodes PNG (default) or
JPEG. Image bytes stay in memory and travel as MCP image content, never
as disk files.

Crop rectangles are in top-down source-image coordinates. A crop that
would drop the HUD strip is rejected unless the caller says so
explicitly; the HUD is never removed silently.
"""
from hashlib import sha1
from io import BytesIO

from PIL import Image

MAX_ENCODED_BYTES = 2 * 1024 * 1024
JPEG_QUALITY = 85

# Gameplay telemetry the pixels_only policy strips (design section 7).
GAMEPLAY_TELEMETRY_KEYS = ("health", "ammo", "dead")

ENCODINGS = ("png", "jpeg")


class ImageTooLarge(ValueError):
    """Encoded frame exceeds the transport cap."""


class BadCrop(ValueError):
    """Crop rectangle is invalid or would silently drop the HUD."""


def apply_telemetry(payload, mode):
    """Return the observation payload under the telemetry policy.

    hud: authoritative gameplay values as captured.
    pixels_only: gameplay telemetry and derived flags are removed.
    """
    if mode not in ("hud", "pixels_only"):
        raise ValueError("INVALID_CONTEXT: telemetry must be hud|pixels_only")
    out = dict(payload)
    if mode == "pixels_only":
        for key in GAMEPLAY_TELEMETRY_KEYS:
            out.pop(key, None)
    return out


def _validate_crop(rect, w, h, hud_rect, allow_hud_crop):
    if not (isinstance(rect, (list, tuple)) and len(rect) == 4
            and all(isinstance(v, int) and not isinstance(v, bool)
                    for v in rect)):
        raise BadCrop("INVALID_CONTEXT: crop must be four integers x,y,w,h")
    x, y, cw, ch = rect
    if cw <= 0 or ch <= 0 or x < 0 or y < 0 or x + cw > w or y + ch > h:
        raise BadCrop("INVALID_CONTEXT: crop %r outside the %dx%d frame"
                      % (list(rect), w, h))
    if hud_rect and not allow_hud_crop:
        hx, hy, hw, hh = hud_rect
        if hw > 0 and hh > 0:
            keeps_hud = (x <= hx and y <= hy
                         and x + cw >= hx + hw and y + ch >= hy + hh)
            if not keeps_hud:
                raise BadCrop(
                    "POLICY_DENIED: crop removes the HUD strip; pass "
                    "allow_hud_crop=True to crop it out explicitly")


def encode_frame(rgb, w, h, longest_edge=1280, fmt="png", crop=None,
                 hud_rect=None, allow_hud_crop=False):
    """Encode one raw frame. Returns (encoded_bytes, transform_report)."""
    if not isinstance(rgb, (bytes, bytearray)) or len(rgb) != w * h * 3:
        raise ValueError("INVALID_CONTEXT: rgb must be exactly %d bytes"
                         % (w * h * 3))
    if fmt not in ENCODINGS:
        raise ValueError("INVALID_CONTEXT: unsupported encoding %r" % (fmt,))
    if longest_edge < 1:
        raise ValueError("INVALID_CONTEXT: longest_edge must be positive")

    img = Image.frombytes("RGB", (w, h), bytes(rgb))
    img = img.transpose(Image.FLIP_TOP_BOTTOM)  # bottom-up -> top-down

    crop_used = []
    if crop:
        rect = tuple(crop)
        _validate_crop(rect, w, h, hud_rect, allow_hud_crop)
        img = img.crop((rect[0], rect[1],
                        rect[0] + rect[2], rect[1] + rect[3]))
        crop_used = list(rect)

    cw, ch = img.size
    img.thumbnail((longest_edge, longest_edge), Image.LANCZOS)  # no upscale
    out_w, out_h = img.size

    buf = BytesIO()
    if fmt == "png":
        img.save(buf, format="PNG")
    else:
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    encoded = buf.getvalue()
    if len(encoded) > MAX_ENCODED_BYTES:
        raise ImageTooLarge(
            "POLICY_DENIED: encoded %s frame is %d bytes (cap %d): reduce "
            "longest_edge, use jpeg, or crop"
            % (fmt, len(encoded), MAX_ENCODED_BYTES))

    report = {
        "src_w": w,
        "src_h": h,
        "out_w": out_w,
        "out_h": out_h,
        "crop": crop_used,
        "scale": out_w / float(cw),
        "encoding": fmt,
        "frame_hash": sha1(bytes(rgb)).hexdigest(),
    }
    return encoded, report
