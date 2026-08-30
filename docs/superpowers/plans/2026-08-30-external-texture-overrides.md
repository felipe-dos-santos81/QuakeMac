# External Texture Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Loose TGA files in the game directory override every GL
texture class (brush, alias skins, sprite frames, pics) in glquake
and glqwcl, fed by a decoupled `tools/` extraction/install pipeline.

**Architecture:** One `GL_TryLoadExternalTexture` helper per tree
(gl_draw.c) soft-fails on any problem and returns 0; six call seams
per tree fall back to the untouched 8-bit path. Loose files resolve
through the existing `COM_FindFile` directory search; upload reuses
`GL_Upload32`. `tools/extract.py` parses pak/BSP/WAD2/MDL/SPR into
PNG + manifest; `tools/install.py` validates user-generated PNGs
against the manifest and writes engine-acceptable TGA.

**Tech Stack:** C (engine, both trees), Python 3 + Pillow (tools),
TGA as the on-disk override format.

**Spec:** `docs/superpowers/specs/2026-08-30-external-texture-overrides-design.md`

## Global Constraints

- **Build oracle** — `make clean && make build-release build-server build-client` from the repo root must exit 0 (AGENTS.md). There are no tests, no lint — the build plus runtime proofs are the verification.
- **Warnings allowed, errors zero** — never build with `-Werror`.
- **Two-tree isolation** — `Quake/` and `QuakeWorld/client/` are separate copies; never include or compile across trees (CONTEXT.md). The same change is written twice.
- **No behavior change without an override file present** — every seam falls through to the exact original code path when the helper returns 0.
- **Never commit game data or derivatives** — `game/` and `tools/extracted/` stay untracked (AGENTS.md rule extended to extracted PNGs, which derive from commercial paks).
- **Tools dependency: Pillow only**, everything else Python stdlib.
- **TGA contract** — uncompressed type 2, 24/32 bpp, bottom-up origin (attributes bit5 = 0), exact file length `18 + w*h*(bpp/8)`. The engine accepts nothing else; `tools/install.py` is the producer.
- **Ledger** — engine touch points are recorded in the Fixes Ledger of `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` in Task 5.

---

### Task 1: Texture extractor (`tools/extract.py`)

**Files:**
- Create: `tools/extract.py`
- Modify: `.gitignore` (add `tools/extracted/`)
- Test: run against the user's `game/id1/` paks; manifest + PNGs are the evidence

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `tools/extracted/{textures,gfx,models,sprites}/*.png` and `tools/extracted/manifest.json` — entries `{name, category, width, height, override_path}` — consumed verbatim by Task 2's installer. Category values: `brush`, `gfx`, `skin`, `sprite`.

- [ ] **Step 1: Add the gitignore entry**

Append to `.gitignore` (repo root):

```
tools/extracted/
```

- [ ] **Step 2: Write `tools/extract.py`**

```python
#!/usr/bin/env python3
"""Extract Quake textures from pak files to PNG + manifest.json.

Usage: python3 tools/extract.py [gamedir]     (default: game/id1)
Output: tools/extracted/{textures,gfx,models,sprites}/, manifest.json
Requires: Pillow  (pip install Pillow)
"""

import json
import os
import struct
import sys

from PIL import Image

BSP_VERSION = 29
LUMP_TEXTURES = 2
TYP_PALETTE = 64
TYP_QTEX = 65
TYP_QPIC = 66
TYP_MIPTEX = 68


def read_paks(gamedir):
    """Return {path: bytes} for every file across pak0..pakN."""
    files = {}
    i = 0
    while True:
        path = os.path.join(gamedir, "pak%d.pak" % i)
        if not os.path.exists(path):
            break
        with open(path, "rb") as f:
            ident, dirofs, dirsize = struct.unpack("<4sii", f.read(12))
            if ident != b"PACK":
                sys.exit("%s: not a pak file" % path)
            f.seek(dirofs)
            for _ in range(dirsize // 64):
                raw, filepos, filelen = struct.unpack("<56sii", f.read(64))
                name = raw.split(b"\0")[0].decode("ascii")
                here = f.tell()
                f.seek(filepos)
                files[name] = f.read(filelen)
                f.seek(here)
        i += 1
    if i == 0:
        sys.exit("no pak files found in %s" % gamedir)
    return files


def wad_entries(wad):
    """Return {name: (filepos, size, type)} for a WAD2 blob."""
    ident, nlumps, infotableofs = struct.unpack("<4sii", wad[:12])
    if ident != b"WAD2":
        sys.exit("gfx.wad: not a WAD2 file")
    entries = {}
    for off in range(infotableofs, infotableofs + nlumps * 32, 32):
        filepos, disksize, size, typ = struct.unpack("<iiiB", wad[off:off + 13])
        name = wad[off + 16:off + 32].split(b"\0")[0].decode("ascii").lower()
        entries[name] = (filepos, size, typ)
    return entries


def palette_from_wad(wad, entries):
    filepos, size, typ = entries.get("palette", (None, 0, None))
    if filepos is None or size < 768 or typ != TYP_PALETTE:
        sys.exit("gfx.wad: palette lump missing")
    return wad[filepos:filepos + 768]


def save_png(path, w, h, pixels, palette, transparent_index=None):
    img = Image.frombytes("P", (w, h), pixels)
    img.putpalette(list(palette))
    if transparent_index is not None:
        img = img.convert("RGBA")
        img.putalpha(Image.frombytes(
            "L", (w, h),
            bytes(0 if p == transparent_index else 255 for p in pixels)))
    else:
        img = img.convert("RGB")
    img.save(path)


def parse_miptex(blob, base):
    """Return (name, w, h, mip0_pixels) from a miptex at blob[base]."""
    name = blob[base:base + 16].split(b"\0")[0].decode("ascii", "replace")
    w, h = struct.unpack_from("<ii", blob, base + 16)
    mipofs = struct.unpack_from("<i", blob, base + 24)[0]
    if w <= 0 or h <= 0 or mipofs <= 0 or base + mipofs + w * h > len(blob):
        return None
    return name, w, h, blob[base + mipofs:base + mipofs + w * h]


def bsp_textures(bsp):
    if len(bsp) < 4 + 15 * 8:
        return []
    version = struct.unpack_from("<i", bsp, 0)[0]
    if version != BSP_VERSION:
        return []
    lumpofs, lumplen = struct.unpack_from("<ii", bsp, 4 + LUMP_TEXTURES * 8)
    if lumplen < 4 or lumpofs <= 0 or lumpofs + lumplen > len(bsp):
        return []
    num = struct.unpack_from("<i", bsp, lumpofs)[0]
    num = min(num, (lumplen - 4) // 4)
    out = []
    for i in range(num):
        dataofs = struct.unpack_from("<i", bsp, lumpofs + 4 + i * 4)[0]
        if dataofs == -1 or lumpofs + dataofs + 40 > len(bsp):
            continue
        tex = parse_miptex(bsp, lumpofs + dataofs)
        if tex:
            out.append(tex)
    return out


def mdl_skins(mdl):
    """Return [(slot, sub_or_None, pixels)] — sub set for skin groups.
    Bounds-checked: a truncated file yields a prefix of the skins."""
    ident, version = struct.unpack_from("<4si", mdl, 0)
    if ident != b"IDPO" or version != 6 or len(mdl) < 84:
        return []
    numskins, skinwidth, skinheight = struct.unpack_from("<iii", mdl, 48)
    s = skinwidth * skinheight
    if numskins <= 0 or s <= 0:
        return []
    skins = []
    off = 84  # sizeof(mdl_t)
    for i in range(numskins):
        if off + 4 > len(mdl):
            return skins
        skintype = struct.unpack_from("<i", mdl, off)[0]
        off += 4
        if skintype == 0:  # ALIAS_SKIN_SINGLE
            if off + s > len(mdl):
                return skins
            skins.append((i, None, mdl[off:off + s]))
            off += s
        else:  # ALIAS_SKIN_GROUP
            if off + 4 > len(mdl):
                return skins
            groupskins = struct.unpack_from("<i", mdl, off)[0]
            off += 4
            if groupskins <= 0 or off + groupskins * 4 > len(mdl):
                return skins
            off += groupskins * 4  # intervals
            for j in range(groupskins):
                if off + s > len(mdl):
                    return skins
                skins.append((i, j, mdl[off:off + s]))
                off += s
    return skins


def spr_frames(spr):
    """Return [(framenum, w, h, pixels)] — group frames use the
    engine's framenum*100+j numbering (Mod_LoadSpriteGroup).
    Bounds-checked: a truncated file yields a prefix of the frames."""
    if len(spr) < 36:
        return []
    ident, version = struct.unpack_from("<4si", spr, 0)
    if ident != b"IDSP" or version != 1:
        return []
    numframes = struct.unpack_from("<i", spr, 24)[0]
    if numframes <= 0:
        return []
    frames = []
    off = 36  # sizeof(dsprite_t)
    for fnum in range(numframes):
        if off + 4 > len(spr):
            return frames
        ftype = struct.unpack_from("<i", spr, off)[0]
        off += 4
        if ftype == 0:  # SPR_SINGLE
            if off + 16 > len(spr):
                return frames
            w, h = struct.unpack_from("<ii", spr, off + 8)
            off += 16
            if w <= 0 or h <= 0 or off + w * h > len(spr):
                return frames
            frames.append((fnum, w, h, spr[off:off + w * h]))
            off += w * h
        else:  # SPR_GROUP
            if off + 4 > len(spr):
                return frames
            groupframes = struct.unpack_from("<i", spr, off)[0]
            off += 4
            if groupframes <= 0 or off + groupframes * 4 > len(spr):
                return frames
            off += groupframes * 4
            for j in range(groupframes):
                if off + 16 > len(spr):
                    return frames
                w, h = struct.unpack_from("<ii", spr, off + 8)
                off += 16
                if w <= 0 or h <= 0 or off + w * h > len(spr):
                    return frames
                frames.append((fnum * 100 + j, w, h, spr[off:off + w * h]))
                off += w * h
    return frames


def main():
    gamedir = sys.argv[1] if len(sys.argv) > 1 else os.path.join("game", "id1")
    outroot = os.path.join("tools", "extracted")
    for sub in ("textures", "gfx", "models", "sprites"):
        os.makedirs(os.path.join(outroot, sub), exist_ok=True)

    files = read_paks(gamedir)
    if "gfx.wad" not in files:
        sys.exit("gfx.wad not found in %s" % gamedir)
    wad = files["gfx.wad"]
    entries = wad_entries(wad)
    palette = palette_from_wad(wad, entries)

    manifest = []
    collisions = []

    # brush textures from map BSPs (dedup by name, first wins)
    seen = {}
    for path in sorted(files):
        if not (path.startswith("maps/") and path.endswith(".bsp")):
            continue
        for name, w, h, pixels in bsp_textures(files[path]):
            if not name or name.startswith("sky"):
                continue  # sky runs through R_InitSky; not overridable
            if name in seen:
                if seen[name] != pixels:
                    collisions.append(name)
                continue
            seen[name] = pixels
            save_png(os.path.join(outroot, "textures", name + ".png"),
                     w, h, pixels, palette)
            manifest.append({"name": name, "category": "brush",
                             "width": w, "height": h,
                             "override_path": "textures/%s.tga" % name})

    # gfx.wad lumps (menu/HUD graphics; index 255 is transparent)
    for lname in sorted(entries):
        filepos, size, typ = entries[lname]
        if typ == TYP_MIPTEX:
            tex = parse_miptex(wad, filepos)
            if not tex:
                continue
            _, w, h, pixels = tex
            save_png(os.path.join(outroot, "gfx", lname + ".png"),
                     w, h, pixels, palette)
            manifest.append({"name": lname, "category": "gfx",
                             "width": w, "height": h,
                             "override_path": "gfx/%s.tga" % lname})
        elif typ == TYP_QPIC:
            w, h = struct.unpack_from("<ii", wad, filepos)
            if w <= 0 or h <= 0 or filepos + 8 + w * h > len(wad):
                continue
            save_png(os.path.join(outroot, "gfx", lname + ".png"),
                     w, h, wad[filepos + 8:filepos + 8 + w * h],
                     palette, transparent_index=255)
            manifest.append({"name": lname, "category": "gfx",
                             "width": w, "height": h,
                             "override_path": "gfx/%s.tga" % lname})
        # TYP_PALETTE / TYP_QTEX / others: not pics, skipped

    # alias model skins and sprite frames.
    # name = flattened PNG stem (/ and . become _ so mdl and spr never
    # collide); override_path keeps the extension, matching the
    # engine's identifiers ("progs/s_light.mdl_0.tga" vs
    # "progs/s_light.spr_0.tga")
    for path in sorted(files):
        if path.startswith("progs/") and path.endswith(".mdl"):
            flat = path.replace("/", "_").replace(".", "_")
            if len(files[path]) < 52:
                continue
            _, skinwidth, skinheight = struct.unpack_from("<iii",
                                                          files[path], 48)
            for slot, sub, pixels in mdl_skins(files[path]):
                suffix = "%d_%d" % (slot, sub) if sub is not None else "%d" % slot
                save_png(os.path.join(outroot, "models",
                                      "%s_%s.png" % (flat, suffix)),
                         skinwidth, skinheight, pixels, palette)
                manifest.append({"name": "%s_%s" % (flat, suffix),
                                 "category": "skin",
                                 "width": skinwidth, "height": skinheight,
                                 "override_path": "%s_%s.tga" % (path, suffix)})
        elif path.startswith("progs/") and path.endswith(".spr"):
            flat = path.replace("/", "_").replace(".", "_")
            for fnum, w, h, pixels in spr_frames(files[path]):
                save_png(os.path.join(outroot, "sprites",
                                      "%s_%d.png" % (flat, fnum)),
                         w, h, pixels, palette, transparent_index=255)
                manifest.append({"name": "%s_%d" % (flat, fnum),
                                 "category": "sprite",
                                 "width": w, "height": h,
                                 "override_path": "%s_%d.tga" % (path, fnum)})

    with open(os.path.join(outroot, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    counts = {}
    for e in manifest:
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    print("extracted %d textures -> %s" % (len(manifest), outroot))
    for cat in ("brush", "gfx", "skin", "sprite"):
        print("  %-6s %d" % (cat, counts.get(cat, 0)))
    if collisions:
        print("name collisions with different pixels (first kept):")
        for c in sorted(set(collisions)):
            print("  " + c)


if __name__ == "__main__":
    main()
```

Note the manifest fields: brush/gfx `name` values are bare
(`wall01`, `conchars`); skin/sprite `name` values are flattened
PNG stems with `/` and `.` mapped to `_` (`progs_soldier_mdl_0`,
`progs_s_light_spr_0`, group skins `..._0_1`, sprite group frames
`..._201`) so `install.py` can match by file stem and mdl/spr never
collide. `override_path` keeps the full model name WITH extension
plus the engine's own suffix scheme (`%s_%i` / `%s_%i_%i` on
`loadmodel->name`), so `progs/s_light.mdl_0.tga` and
`progs/s_light.spr_0.tga` stay distinct exactly like the engine's
texture identifiers. PNG file names equal `<name>.png`.

- [ ] **Step 3: Install Pillow if needed**

Run: `python3 -c "import PIL" 2>/dev/null || pip3 install Pillow`
Expected: import succeeds afterwards.

- [ ] **Step 4: Run the extractor**

Run: `python3 tools/extract.py` (repo root)
Expected: exit 0; `tools/extracted/` contains the four subdirs,
`manifest.json`, and PNGs; printed counts are plausible for the
registered game (brush in the hundreds, gfx ~100, skins and sprite
frames non-zero). Zero entries in any category means a parser bug —
investigate before continuing.

- [ ] **Step 5: Spot-check three PNGs**

Open `tools/extracted/textures/wall01.png` (or another manifest
brush entry), `tools/extracted/gfx/conchars.png`, and any
`models/*.png`. Expected: recognizable Quake art at the sizes
recorded in the manifest (wall01 is 64x64 in the stock game; trust
the manifest if the pak differs).

- [ ] **Step 6: Commit**

```bash
git add tools/extract.py .gitignore
git commit -m "Add texture extractor (tools/extract.py): pak/BSP/WAD2/MDL/SPR to PNG + manifest"
```

---

### Task 2: Override installer (`tools/install.py`) + tools README

**Files:**
- Create: `tools/install.py`, `tools/README.md`
- Test: built-in `--selftest` plus one real round-trip install

**Interfaces:**
- Consumes: Task 1's `tools/extracted/manifest.json` entries `{name, category, width, height, override_path}`
- Produces: TGA files at `game/id1/<override_path>` in the exact byte contract the engine helper accepts (type 2, 24/32 bpp, bottom-up, attributes = 0, length `18 + w*h*(bpp/8)`)

- [ ] **Step 1: Write `tools/install.py`**

```python
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
    img = img.transpose(Image.FLIP_TOP_BOTTOM)
    if img.mode == "RGBA" and min(img.getchannel("A").getdata()) < 255:
        channels = 4
        r, g, b, a = img.split()
        ordered = Image.merge("RGBA", (b, g, r, a))
    else:
        channels = 3
        r, g, b = img.convert("RGB").split()
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

    print("selftest OK")


def main():
    args = sys.argv[1:]
    if args[:1] == ["--selftest"]:
        selftest()
        return
    gamedir = os.path.join("game", "id1")
    if args[:2] == ["--gamedir"]:
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
```

- [ ] **Step 2: Run the selftest**

Run: `python3 tools/install.py --selftest`
Expected: `selftest OK`, exit 0. Any assertion failure means the
TGA writer or the invariant checks drifted from the engine contract
— fix before touching the engine.

- [ ] **Step 3: Real round-trip install**

Pick one extracted original and install it unchanged:

```bash
python3 tools/install.py tools/extracted/textures/wall01.png
```

(Use any brush PNG the manifest actually contains.) Expected: one
`installed` line pointing at `game/id1/textures/<name>.tga`, exit 0.
Then verify the file satisfies the engine contract:

```bash
python3 - <<'EOF'
import struct
d = open("game/id1/textures/wall01.tga","rb").read()
h = struct.unpack("<BBBHHBHHHHBB", d[:18])
assert h[2] == 2 and h[10] in (24, 32) and h[11] == 0
assert len(d) == 18 + h[8] * h[9] * (h[10] // 8)
print("tga contract OK", h[8], "x", h[9], h[10], "bpp")
EOF
```

Expected: `tga contract OK` with the manifest's dimensions. Delete
the file afterwards (`rm game/id1/textures/wall01.tga`) so Task 3's
build-and-run proof starts clean.

- [ ] **Step 4: Write `tools/README.md`**

```markdown
# tools/

Texture pipeline for the external-override feature. Decoupled from
the engine; the game never reads anything here.

## Pipeline

1. `python3 tools/extract.py [gamedir]` — reads `pak*.pak`
   (default `game/id1`), writes every brush texture, gfx.wad pic,
   model skin, and sprite frame as PNG into `tools/extracted/`,
   plus `manifest.json` (name, category, original size, override
   path). Output is git-ignored: it derives from commercial game
   data.
2. Regenerate the PNGs you care about with any image tool. Keep
   the exact stem name (`wall01.png`, `progs_soldier_mdl_0.png`, ...).
3. `python3 tools/install.py <png-or-dir> ...` — validates each
   image against the manifest and writes a TGA override into
   `game/id1/<override_path>`. `--gamedir game/qw` targets the QW
   directory instead.

## Invariants

- `gfx` overrides must match the original pixel size exactly (menu
  and HUD layout is pixel-exact).
- Brush, skin, and sprite overrides may be any resolution with the
  same aspect ratio as the original.
- Overrides on disk are uncompressed bottom-up TGA, 24 or 32 bpp —
  the only shape the engine accepts (`GL_TryLoadExternalTexture`).
  Alpha is used only when the PNG has non-opaque pixels.

## Exclusions (not extracted / not overridable)

- `sky*` brush textures — separate sky pipeline (`R_InitSky`).
- `progs/player.mdl` skins — the renderer rebuilds that texture at
  runtime for shirt/pants recoloring.
- `gfx/menuplyr.lmp` — same recoloring mechanism.

## Where overrides live

`game/id1/textures/...`, `game/id1/gfx/...`,
`game/id1/progs/...`. Both glquake and glqwcl mount id1, so one
set serves both; a file in `game/qw/` wins for glqwcl.
`gl_externaltextures 0` disables lookups for subsequently loaded
textures.
```

- [ ] **Step 5: Commit**

```bash
git add tools/install.py tools/README.md
git commit -m "Add override installer (tools/install.py) and tools README"
```

---

### Task 3: Engine support in the Quake tree

**Files:**
- Modify: `Quake/render/gl_draw.c` (cvar + helper + Draw_Init cvar registration + `Draw_PicFromWad` + `Draw_CachePic` + charset)
- Modify: `Quake/render/gl_model.c` (brush, alias skins, sprite seams + path builder)
- Modify: `Quake/render/glquake.h` (prototype + extern cvar)
- Test: `make build-release`; then the Task 5 runtime proofs

**Interfaces:**
- Consumes: nothing from other tasks at compile time; at runtime consumes TGA files laid down by Task 2's installer
- Produces: `int GL_TryLoadExternalTexture (char *identifier, char *path, int orig_w, int orig_h, qboolean exact_size, qboolean mipmap, qboolean alpha)` — returns a GL texture number, or 0 to mean "fall back to the original path". `cvar_t gl_externaltextures` (default "1").

- [ ] **Step 1: Declare in `Quake/render/glquake.h`**

Next to the existing `GL_LoadTexture` declaration (glquake.h:38-40) add:

```c
extern cvar_t gl_externaltextures;
int GL_TryLoadExternalTexture (char *identifier, char *path,
	int orig_w, int orig_h, qboolean exact_size,
	qboolean mipmap, qboolean alpha);
```

- [ ] **Step 2: Add the cvar and helper to `Quake/render/gl_draw.c`**

After the `gl_picmip` definition (line 35) add:

```c
cvar_t gl_externaltextures = {"gl_externaltextures", "1"};
```

In `Draw_Init`, next to `Cvar_RegisterVariable (&gl_picmip);`
(gl_draw.c:382), add:

```c
	Cvar_RegisterVariable (&gl_externaltextures);
```

After `GL_LoadPicTexture` (gl_draw.c:1277-1280) add the helper:

```c
/*
=====================
GL_TryLoadExternalTexture

Loads a 32-bit RGBA override texture through the normal COM
filesystem (loose files or paks). Accepts only uncompressed
bottom-up 24/32-bit TGA whose exact byte length matches the
header; anything else — absent file, foreign format, truncation,
dimension-invariant violation — is a soft failure: warn where
useful and return 0 so the caller keeps its original path.
Registered under the original dimensions so cache lookups by the
8-bit path stay coherent.
=====================
*/
int GL_TryLoadExternalTexture (char *identifier, char *path,
	int orig_w, int orig_h, qboolean exact_size,
	qboolean mipmap, qboolean alpha)
{
	byte		*buf;
	unsigned	*rgba;
	gltexture_t	*glt;
	int			i, x, y, w, h, bpp, channels, len;

	if (!gl_externaltextures.value)
		return 0;

	if (identifier[0])
	{
		for (i = 0, glt = gltextures; i < numgltextures; i++, glt++)
			if (!strcmp (identifier, glt->identifier))
				return glt->texnum;
	}

	buf = COM_LoadFile (path, 0);
	if (!buf)
		return 0;
	len = com_filesize;

	if (len < 18 || buf[1] != 0 || buf[2] != 2 || (buf[17] & 0x20))
		goto reject;
	w = buf[12] | (buf[13] << 8);
	h = buf[14] | (buf[15] << 8);
	bpp = buf[16];
	if ((bpp != 24 && bpp != 32) || w < 1 || h < 1)
		goto reject;
	if (len != 18 + w * h * (bpp / 8))
		goto reject;

	if (exact_size)
	{
		if (w != orig_w || h != orig_h)
		{
			Con_Printf ("External %s: %dx%d, expected %dx%d\n",
				path, w, h, orig_w, orig_h);
			goto reject;
		}
	}
	else if (w * orig_h != h * orig_w)
	{
		Con_Printf ("External %s: aspect mismatch (%dx%d vs %dx%d)\n",
			path, w, h, orig_w, orig_h);
		goto reject;
	}

	rgba = malloc (w * h * 4);
	if (!rgba)
		goto reject;

	channels = bpp / 8;
	for (y = 0; y < h; y++)
	{
		byte		*src = buf + 18 + (h - 1 - y) * w * channels;
		unsigned	*dst = rgba + y * w;

		for (x = 0; x < w; x++, src += channels)
		{
			unsigned	a = (channels == 4) ? src[3] : 255;

			dst[x] = src[2] | (src[1] << 8) | (src[0] << 16) | (a << 24);
		}
	}

	glt = &gltextures[numgltextures];
	numgltextures++;
	strcpy (glt->identifier, identifier);
	glt->texnum = texture_extension_number;
	glt->width = orig_w;
	glt->height = orig_h;
	glt->mipmap = mipmap;

	GL_Bind (texture_extension_number);
	GL_Upload32 (rgba, w, h, mipmap, alpha);
	texture_extension_number++;

	Con_Printf ("External texture: %s\n", path);

	free (rgba);
	Z_Free (buf);
	return texture_extension_number - 1;

reject:
	Z_Free (buf);
	return 0;
}
```

Check `#include <stdlib.h>` is present at the top of gl_draw.c for
`malloc`/`free` (it is not in every Quake file; if missing, add it
with the other system includes).

- [ ] **Step 3: Wire the pic seams in `Quake/render/gl_draw.c`**

`Draw_PicFromWad` (gl_draw.c:183): replace the body after
`gl = (glpic_t *)p->data;` so an override bypasses the 8-bit scrap
atlas (which cannot hold 32-bit pixels):

```c
	gl->texnum = GL_TryLoadExternalTexture ("", va ("gfx/%s.tga", name),
		p->width, p->height, true, false, true);
	if (gl->texnum)
	{
		gl->sl = 0;
		gl->sh = 1;
		gl->tl = 0;
		gl->th = 1;
		pic_count++;
		pic_texels += p->width * p->height;
		return p;
	}

	// load little ones into the scrap
	if (p->width < 64 && p->height < 64)
	{
		... original scrap block unchanged ...
	}
	else
	{
		... original full-texture block unchanged ...
	}
	return p;
```

`Draw_CachePic` (gl_draw.c:231): after the existing
`pic->pic.width/height` assignments, replace the
`gl->texnum = GL_LoadPicTexture (dat);` line:

```c
	gl = (glpic_t *)pic->pic.data;
	if (!strcmp (path, "gfx/menuplyr.lmp"))
	{	// runtime shirt/pants translation rewrites this texture;
		// an override would be clobbered
		gl->texnum = GL_LoadPicTexture (dat);
	}
	else
	{
		char	extpath[MAX_QPATH];

		strcpy (extpath, path);
		if (strlen (extpath) > 4 && !strcmp (extpath + strlen (extpath) - 4, ".lmp"))
			strcpy (extpath + strlen (extpath) - 4, ".tga");
		gl->texnum = GL_TryLoadExternalTexture ("", extpath,
			dat->width, dat->height, true, false, true);
		if (!gl->texnum)
			gl->texnum = GL_LoadPicTexture (dat);
	}
	gl->sl = 0;
	gl->sh = 1;
	gl->tl = 0;
	gl->th = 1;
```

Charset (console font): in `Draw_Init`, replace the
`char_texture = GL_LoadTexture ("charset", ...)` line
(gl_draw.c:401):

```c
	char_texture = GL_TryLoadExternalTexture ("charset", "gfx/conchars.tga",
		128, 128, true, false, true);
	if (!char_texture)
		char_texture = GL_LoadTexture ("charset", 128, 128, draw_chars, false, true);
```

- [ ] **Step 4: Wire the model seams in `Quake/render/gl_model.c`**

Add the path builder near the top of the file, after the
`loadname` definition (gl_model.c:31):

```c
/*
=============
Mod_ExternalTexturePath

Builds the override path from the full model name, extension kept
(collisions stay impossible because engine identifiers already
differ by extension): "progs/s_light.mdl" + "0" ->
"progs/s_light.mdl_0.tga"
=============
*/
static void Mod_ExternalTexturePath (char *out, char *suffix)
{
	strcpy (out, loadmodel->name);
	strcat (out, "_");
	strcat (out, suffix);
	strcat (out, ".tga");
}
```

Brush seam — `Mod_LoadTextures` (gl_model.c:389-396), replace the
`else` branch:

```c
		if (!Q_strncmp(mt->name,"sky",3))	
			R_InitSky (tx);
		else
		{
			tx->gl_texturenum = GL_TryLoadExternalTexture (mt->name,
				va ("textures/%s.tga", mt->name),
				tx->width, tx->height, false, true, false);
			if (!tx->gl_texturenum)
			{
				texture_mode = GL_LINEAR_MIPMAP_NEAREST; //_LINEAR;
				tx->gl_texturenum = GL_LoadTexture (mt->name, tx->width, tx->height, (byte *)(tx+1), true, false);
				texture_mode = GL_LINEAR;
			}
		}
```

Alias seams — `Mod_LoadAllSkins`. Add locals at the top of the
function next to `char name[32];`:

```c
	char	path[MAX_QPATH + 16];
	char	suffix[16];
```

Single skins (gl_model.c:1444-1450): replace the
`sprintf (name, "%s_%i", loadmodel->name, i);` block:

```c
			sprintf (name, "%s_%i", loadmodel->name, i);
			pheader->gl_texturenum[i][0] = 0;
			if (strcmp (loadmodel->name, "progs/player.mdl"))
			{	// player skin is rebuilt at runtime for color translation
				sprintf (suffix, "%i", i);
				Mod_ExternalTexturePath (path, suffix);
				pheader->gl_texturenum[i][0] = GL_TryLoadExternalTexture (
					name, path, pheader->skinwidth, pheader->skinheight,
					false, true, false);
			}
			if (!pheader->gl_texturenum[i][0])
			{
				pheader->gl_texturenum[i][0] =
				pheader->gl_texturenum[i][1] =
				pheader->gl_texturenum[i][2] =
				pheader->gl_texturenum[i][3] =
					GL_LoadTexture (name, pheader->skinwidth, 
					pheader->skinheight, (byte *)(pskintype + 1), true, false);
			}
			else
			{
				pheader->gl_texturenum[i][1] =
				pheader->gl_texturenum[i][2] =
				pheader->gl_texturenum[i][3] =
					pheader->gl_texturenum[i][0];
			}
```

Group skins (gl_model.c:1469-1472): replace the
`sprintf (name, "%s_%i_%i", loadmodel->name, i,j);` call block:

```c
					sprintf (name, "%s_%i_%i", loadmodel->name, i,j);
					sprintf (suffix, "%i_%i", i, j);
					Mod_ExternalTexturePath (path, suffix);
					pheader->gl_texturenum[i][j&3] = GL_TryLoadExternalTexture (
						name, path, pheader->skinwidth, pheader->skinheight,
						false, true, false);
					if (!pheader->gl_texturenum[i][j&3])
						pheader->gl_texturenum[i][j&3] = 
							GL_LoadTexture (name, pheader->skinwidth, 
							pheader->skinheight, (byte *)(pskintype), true, false);
```

(The `progs/player.mdl` exclusion is unnecessary here — the stock
player model has no skin groups — but the lookup is harmless if one
appears.)

Sprite seam — `Mod_LoadSpriteFrame` (gl_model.c:1689-1690): add
`char path[MAX_QPATH + 16]; char suffix[16];` to the locals and
replace the `sprintf (name, ...)` + `GL_LoadTexture` pair:

```c
	sprintf (name, "%s_%i", loadmodel->name, framenum);
	sprintf (suffix, "%i", framenum);
	Mod_ExternalTexturePath (path, suffix);
	pspriteframe->gl_texturenum = GL_TryLoadExternalTexture (name, path,
		width, height, false, true, true);
	if (!pspriteframe->gl_texturenum)
		pspriteframe->gl_texturenum = GL_LoadTexture (name, width, height, (byte *)(pinframe + 1), true, true);
```

- [ ] **Step 5: Build**

Run: `make build-release` (repo root)
Expected: exit 0. Fix compile errors only within the four files
above; warnings allowed.

- [ ] **Step 6: Commit**

```bash
git add Quake
git commit -m "External TGA texture overrides in the Quake tree (gl_externaltextures)"
```

---

### Task 4: Engine support in the QuakeWorld/client tree

**Files:**
- Modify: `QuakeWorld/client/render/gl_draw.c`, `QuakeWorld/client/render/gl_model.c`, `QuakeWorld/client/render/glquake.h`
- Test: `make build-client`

**Interfaces:**
- Consumes: nothing new at compile time; same runtime contract as Task 3
- Produces: identical behavior for glqwcl

- [ ] **Step 1: Mirror Task 3 exactly into the QuakeWorld tree**

Apply the same six code blocks to `QuakeWorld/client/render/`,
locating each seam by content (line numbers differ; found via the
exploration below — re-verify before editing):

| Edit | Location in QuakeWorld/client/render/ |
|---|---|
| cvar definition | gl_draw.c: after `cvar_t gl_picmip = ...` (line 33) |
| cvar registration | gl_draw.c: next to `Cvar_RegisterVariable (&gl_picmip);` (line 385) |
| helper | gl_draw.c: after `GL_LoadPicTexture` (find via `GL_LoadTexture` at line 1313) |
| declarations | glquake.h: next to the `GL_LoadTexture` prototype |
| charset | gl_draw.c `Draw_Init`: the `char_texture = GL_LoadTexture ("charset", ...)` line |
| `Draw_PicFromWad` | gl_draw.c:188 |
| `Draw_CachePic` | gl_draw.c:236 |
| path builder | gl_model.c: after `char loadname[32];` |
| brush seam | gl_model.c: the `GL_LoadTexture (mt->name, ...)` call (line 391) inside the `Mod_LoadTextures` sky `else` branch |
| alias single skins | gl_model.c `Mod_LoadAllSkins` (line 1436): the `sprintf (name, "%s_%i", loadmodel->name, i);` block (~1466) |
| alias group skins | same function, the `sprintf (name, "%s_%i_%i", ...)` block (~1491) |
| sprite seam | gl_model.c `Mod_LoadSpriteFrame` (line 1701): the `sprintf (name, "%s_%i", ...)` + `GL_LoadTexture` pair (~1729) |

The QW tree keeps `player_8bit_texels` for `progs/player.mdl`
(gl_model.c:1460-1465) — do not touch that block; the
`strcmp (loadmodel->name, "progs/player.mdl")` exclusion added in
Task 3 already leaves it on the untouched path.

Drift watch: if any seam's surrounding code differs from the Quake
tree in a way that changes semantics (different variable names,
extra logic), adapt the edit to this file — do not force the Quake
tree's text.

- [ ] **Step 2: Build**

Run: `make build-client` (repo root)
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add QuakeWorld
git commit -m "External TGA texture overrides in the QuakeWorld client tree (gl_externaltextures)"
```

---

### Task 5: Full verification + Fixes Ledger

**Files:**
- Modify: `docs/superpowers/plans/2026-08-29-quake-apple-silicon.md` (ledger entry)
- Test: the full gate set below

**Interfaces:**
- Consumes: Tasks 1-4 artifacts; user's `game/id1/` paks
- Produces: verified feature, ledger entry, final commit

- [ ] **Step 1: Build oracle**

Run: `make clean && make build-release build-server build-client` (repo root)
Expected: exit 0.

- [ ] **Step 2: Round-trip visual proof**

```bash
python3 tools/install.py tools/extracted/textures/wall01.png
make run
```

(Substitute a brush PNG the manifest actually contains if needed;
`make run` gates on `check-data-quake`.) In the running game:
`map start`. Expected in the terminal output: at least one
`External texture: textures/wall01.tga` line and no `aspect
mismatch` / `expected` warnings. In the window: wall01 surfaces
look exactly like the stock texture (the installed file is the
unmodified original, so visual identity is the correctness signal).
Quit the game.

- [ ] **Step 3: Override + negative proofs**

Create a wrong-aspect override and confirm graceful fallback:

```bash
python3 - <<'EOF'
from PIL import Image
img = Image.open("tools/extracted/textures/wall01.png")
img.resize((img.width * 2, img.height)).save("/tmp/wall01.png")
EOF
python3 tools/install.py /tmp/wall01.png   # expect: rejected (aspect)
```

Then truncate a good override and confirm the engine survives:

```bash
python3 tools/install.py tools/extracted/textures/wall01.png
cp game/id1/textures/wall01.tga /tmp/full.tga
head -c 100 /tmp/full.tga > game/id1/textures/wall01.tga
make run
```

Expected: game boots, `map start` renders with the stock texture,
no crash (the truncated file fails the helper's exact-length check
before decode). Restore and also test the cvar:

```bash
cp /tmp/full.tga game/id1/textures/wall01.tga
make run
```

In the game console: `map e1m1`, note the external load line, then
`gl_externaltextures 0`, `map e1m2` — expected: no external load
line for e1m2, stock textures. Quit.

- [ ] **Step 4: QuakeWorld proof**

```bash
make run-server    # terminal 1
make run-client    # terminal 2, then: connect localhost
```

Expected: glqwcl connects, spawns, and (with the wall01 override
still installed under `game/id1/textures/`) the console shows
`External texture:` lines — id1 is mounted by glqwcl, so the same
files apply. Clean disconnect and quit both.

- [ ] **Step 5: SIGKILL smoke protocol**

Run the 3-binary SIGKILL smoke protocol per CONTEXT.md (launch each
binary against the game data, SIGKILL it, check its output).
Expected: `Received signal` counts 0/0/0.

- [ ] **Step 6: Clean up test overrides**

```bash
rm -rf game/id1/textures /tmp/wall01.png /tmp/full.tga
```

- [ ] **Step 7: Ledger entry + commit**

Append to the Fixes Ledger section of
`docs/superpowers/plans/2026-08-29-quake-apple-silicon.md`:

```markdown
### External texture overrides (feature)
Commits: <task-3 sha>, <task-4 sha>. Spec:
docs/superpowers/specs/2026-08-30-external-texture-overrides-design.md.
New GL_TryLoadExternalTexture in both GL clients' gl_draw.c:
uncompressed bottom-up 24/32-bit TGA through the COM filesystem,
soft-failing on any malformed content; cvar gl_externaltextures
(default 1). Seams: brush (Mod_LoadTextures), alias skins and
sprite frames (gl_model.c), pics (Draw_PicFromWad/Draw_CachePic/
charset). Exclusions: sky*, progs/player.mdl skins, gfx/menuplyr.
Tools: tools/extract.py (pak/BSP/WAD2/MDL/SPR -> PNG + manifest),
tools/install.py (PNG -> validated TGA under game/id1/).
```

Fill in the two real commit SHAs, then:

```bash
git add docs/superpowers/plans/2026-08-29-quake-apple-silicon.md
git commit -m "Ledger: external texture overrides feature"
```
