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


def palette_from_files(files):
    # engine loads the palette from gfx/palette.lmp (host.c Host_Init),
    # not gfx.wad — registered pak0's gfx.wad has no palette lump
    pal = files.get("gfx/palette.lmp")
    if pal is None or len(pal) < 768:
        sys.exit("gfx/palette.lmp missing or too small")
    return pal[:768]


def save_png(path, w, h, pixels, palette, transparent_index=None):
    # Stay indexed: the PNG carries the original Quake palette so edits
    # can stay on-palette; install.py converts whatever it receives.
    img = Image.frombytes("P", (w, h), pixels)
    img.putpalette(list(palette))
    if transparent_index is not None:
        img.save(path, transparency=transparent_index)
    else:
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
    palette = palette_from_files(files)

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
            if tex:
                _, w, h, pixels = tex
            else:
                # engine reads conchars as raw pixels, no miptex header
                # (gl_draw.c W_GetLumpName); square lump = w == h
                w = h = int(size ** 0.5)
                if w * h != size:
                    continue
                pixels = wad[filepos:filepos + size]
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
            if len(files[path]) < 60:  # dims read at offset 48 needs 60 bytes
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
