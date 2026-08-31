#!/usr/bin/env python3
"""Install generated PNGs as TGA texture overrides.

Usage: python3 tools/install.py [--gamedir game/id1] <png-or-dir> ...
       python3 tools/install.py --selftest
Requires: Pillow  (pip install Pillow)
"""

import json
import os
import struct
import sys
import tempfile

from PIL import Image

MANIFEST = os.path.join("tools", "extracted", "manifest.json")


def load_manifest():
    with open(MANIFEST) as f:
        return {e["name"]: e for e in json.load(f)}


def write_tga(path, img):
    """Write an uncompressed bottom-up TGA (engine contract)."""
    img = img.convert("RGBA")
    has_alpha = min(img.getchannel("A").getdata()) < 255
    img = img.transpose(Image.FLIP_TOP_BOTTOM)
    if has_alpha:
        channels = 4
        r, g, b, a = img.split()
        ordered = Image.merge("RGBA", (b, g, r, a))
    else:
        channels = 3
        r, g, b, _ = img.split()
        ordered = Image.merge("RGB", (b, g, r))
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0,
                         img.width, img.height, channels * 8, 0)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(header)
        f.write(ordered.tobytes())


def check(entry, w, h):
    """Return (ok, expectation string) for the class invariant."""
    if entry["category"] == "gfx":
        return (w == entry["width"] and h == entry["height"],
                "%dx%d exact" % (entry["width"], entry["height"]))
    return (w * entry["height"] == h * entry["width"],
            "aspect %d:%d" % (entry["width"], entry["height"]))


def install_one(png_path, entries, gamedir):
    stem = os.path.splitext(os.path.basename(png_path))[0]
    entry = entries.get(stem)
    if entry is None:
        return "unknown", stem
    img = Image.open(png_path)
    ok, expect = check(entry, img.width, img.height)
    if not ok:
        return "rejected", "%s: %dx%d, expected %s" % (
            stem, img.width, img.height, expect)
    dest = os.path.join(gamedir, entry["override_path"])
    write_tga(dest, img)
    return "installed", dest


def selftest():
    """Synthetic round-trip: PNG -> TGA bytes match the engine
    contract; invariant rejections fire."""
    with tempfile.TemporaryDirectory() as tmp:
        entries = {
            "walltest": {"name": "walltest", "category": "brush",
                         "width": 4, "height": 2,
                         "override_path": "textures/walltest.tga"},
            "pictest": {"name": "pictest", "category": "gfx",
                        "width": 4, "height": 2,
                        "override_path": "gfx/pictest.tga"},
        }
        img = Image.new("RGB", (4, 2))
        img.putpixel((0, 0), (10, 20, 30))
        png = os.path.join(tmp, "walltest.png")
        img.save(png)

        status, dest = install_one(png, entries, tmp)
        assert status == "installed", status
        data = open(dest, "rb").read()
        assert len(data) == 18 + 4 * 2 * 3, len(data)
        (idl, cmt, ityp, cmi, cml, cms, xo, yo, w, h, bpp, attr) = \
            struct.unpack("<BBBHHBHHHHBB", data[:18])
        assert (ityp, bpp, attr, w, h) == (2, 24, 0, 4, 2)
        # bottom-up: first pixel row in file = bottom image row.
        # putpixel wrote top-left (10,20,30); stored BGR at the
        # start of row index h-1 (= row 1 of 2 bottom-up rows is
        # image row 1, all black); the last row stored is image
        # row 0 with our pixel.
        assert data[-12:] == bytes([30, 20, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0])

        # aspect mismatch rejected (8x2 vs 4x2)
        bad = Image.new("RGB", (8, 2))
        badpng = os.path.join(tmp, "walltest.png")
        bad.save(badpng)
        status, msg = install_one(badpng, entries, tmp)
        assert status == "rejected" and "aspect" in msg, (status, msg)

        # gfx exact-size mismatch rejected (4x4 vs 4x2)
        bad2 = Image.new("RGB", (4, 4))
        badpng2 = os.path.join(tmp, "pictest.png")
        bad2.save(badpng2)
        status, msg = install_one(badpng2, entries, tmp)
        assert status == "rejected" and "exact" in msg, (status, msg)

        # unknown stem reported
        status, msg = install_one(os.path.join(tmp, "nope.png"), entries, tmp)
        assert status == "unknown"

        # palette-mode PNG with a transparent index still gets 32-bit
        pal = Image.new("P", (4, 2))
        pal.putpalette([i % 256 for i in range(768)])
        pal.info["transparency"] = 1
        pal.putpixel((0, 0), 1)
        palpng = os.path.join(tmp, "walltest.png")
        pal.save(palpng)
        status, dest = install_one(palpng, entries, tmp)
        assert status == "installed", status
        data = open(dest, "rb").read()
        assert struct.unpack("<BBBHHBHHHHBB", data[:18])[10] == 32, \
            "palette transparency lost"

    print("selftest OK")


def main():
    args = sys.argv[1:]
    if args[:1] == ["--selftest"]:
        selftest()
        return
    gamedir = os.path.join("game", "id1")
    if args[:1] == ["--gamedir"]:
        if len(args) < 2:
            sys.exit(__doc__)
        gamedir, args = args[1], args[2:]
    if not args:
        sys.exit(__doc__)
    if not os.path.exists(MANIFEST):
        sys.exit("no manifest at %s — run tools/extract.py first" % MANIFEST)
    entries = load_manifest()

    pngs = []
    for arg in args:
        if os.path.isdir(arg):
            pngs += sorted(os.path.join(arg, f) for f in os.listdir(arg)
                           if f.lower().endswith(".png"))
        else:
            pngs.append(arg)

    counts = {"installed": 0, "rejected": 0, "unknown": 0}
    for png in pngs:
        status, detail = install_one(png, entries, gamedir)
        counts[status] += 1
        print("%-9s %s" % (status, detail))
    print("installed=%d rejected=%d unknown=%d" % (
        counts["installed"], counts["rejected"], counts["unknown"]))


if __name__ == "__main__":
    main()
