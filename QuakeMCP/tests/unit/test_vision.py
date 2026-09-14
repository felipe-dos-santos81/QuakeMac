"""Task 7 unit tests: frame encoding transforms and telemetry policy."""
import os
import sys
from hashlib import sha1
from io import BytesIO

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from quakemcp import vision


def _gradient(w, h):
    """Distinct RGB per coordinate; stored bottom-up like glReadPixels."""
    rows = []
    for y in range(h - 1, -1, -1):
        row = bytearray()
        for x in range(w):
            row += bytes(((x * 4) % 256, (y * 8) % 256, 77))
        rows.append(bytes(row))
    return b"".join(rows)


def test_flip_round_trip_and_report():
    w, h = 16, 8
    raw = _gradient(w, h)
    enc, rep = vision.encode_frame(raw, w, h)
    assert enc[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(BytesIO(enc))
    assert img.format == "PNG"
    assert img.size == (w, h)
    px = img.load()
    for y in range(h):
        for x in range(w):
            assert px[x, y] == ((x * 4) % 256, (y * 8) % 256, 77)
    assert rep == {
        "src_w": w, "src_h": h, "out_w": w, "out_h": h, "crop": [],
        "scale": 1.0, "encoding": "png",
        "frame_hash": sha1(raw).hexdigest(),
    }


def test_resize_math_and_no_upscale():
    raw = bytes(2560 * 1440 * 3)
    enc, rep = vision.encode_frame(raw, 2560, 1440)
    assert (rep["out_w"], rep["out_h"]) == (1280, 720)
    assert rep["scale"] == 0.5
    assert Image.open(BytesIO(enc)).size == (1280, 720)

    raw = bytes(100 * 50 * 3)
    enc, rep = vision.encode_frame(raw, 100, 50)
    assert (rep["out_w"], rep["out_h"]) == (100, 50)
    assert rep["scale"] == 1.0


def test_oversize_raises_with_alternatives():
    raw = os.urandom(1024 * 1024 * 3)  # noise: PNG cannot compress it
    with pytest.raises(vision.ImageTooLarge) as e:
        vision.encode_frame(raw, 1024, 1024)
    assert "longest_edge" in str(e.value)
    assert "jpeg" in str(e.value)


def test_crop_validation():
    w, h = 64, 32
    raw = _gradient(w, h)
    hud = [0, 24, 64, 8]  # bottom strip, top-down coordinates

    with pytest.raises(vision.BadCrop):
        vision.encode_frame(raw, w, h, crop=[0, 0, 999, 10])
    with pytest.raises(vision.BadCrop):
        vision.encode_frame(raw, w, h, crop=[1, 2, 3])
    # drops the HUD without the explicit flag
    with pytest.raises(vision.BadCrop):
        vision.encode_frame(raw, w, h, crop=[0, 0, 64, 20], hud_rect=hud)
    enc, rep = vision.encode_frame(raw, w, h, crop=[0, 0, 64, 20],
                                   hud_rect=hud, allow_hud_crop=True)
    assert rep["crop"] == [0, 0, 64, 20]
    assert Image.open(BytesIO(enc)).size == (64, 20)
    # keeping the HUD is fine
    enc, rep = vision.encode_frame(raw, w, h, crop=[0, 0, 64, 32],
                                   hud_rect=hud)
    assert rep["crop"] == [0, 0, 64, 32]


def test_telemetry_policy():
    payload = {"frame": 7, "health": 100, "ammo": 25, "dead": False}
    assert vision.apply_telemetry(payload, "hud") == payload
    stripped = vision.apply_telemetry(payload, "pixels_only")
    assert "health" not in stripped
    assert "ammo" not in stripped
    assert "dead" not in stripped
    assert stripped["frame"] == 7
    with pytest.raises(ValueError):
        vision.apply_telemetry(payload, "everything")


def test_bad_length_rejected():
    with pytest.raises(ValueError):
        vision.encode_frame(b"\x00" * 10, 4, 4)
    with pytest.raises(ValueError):
        vision.encode_frame(b"\x00" * (4 * 4 * 3), 4, 4, fmt="webp")


def test_error_messages_carry_codes():
    raw = _gradient(8, 8)
    with pytest.raises(vision.BadCrop) as e:
        vision.encode_frame(raw, 8, 8, crop=[0, 0, 9, 1])
    assert str(e.value).startswith("INVALID_CONTEXT: "), e.value

    with pytest.raises(vision.BadCrop) as e:
        vision.encode_frame(raw, 8, 8, crop=[0, 0, 8, 4],
                            hud_rect=[0, 6, 8, 2])
    assert str(e.value).startswith("POLICY_DENIED: "), e.value

    with pytest.raises(ValueError) as e:
        vision.encode_frame(raw, 8, 8, fmt="gif")
    assert str(e.value).startswith("INVALID_CONTEXT: "), e.value

    with pytest.raises(vision.ImageTooLarge) as e:
        vision.encode_frame(os.urandom(1024 * 1024 * 3), 1024, 1024)
    assert str(e.value).startswith("POLICY_DENIED: "), e.value
