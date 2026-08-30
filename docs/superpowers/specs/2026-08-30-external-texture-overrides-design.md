# External Texture Overrides — Design

**Date:** 2026-08-30
**Status:** Approved (brainstorming session)
**Scope:** `glquake` (Quake/) and `glqwcl` (QuakeWorld/client/). `qwsv`
renders nothing.

## Goal

Let loose image files in the game directory override every texture
class the GL renderer uses — brush textures, alias model skins,
sprite frames, and pics (gfx.wad + .lmp) — so user-generated
"realistic" replacements can be dropped in without touching pak
files. A decoupled `tools/` folder extracts originals as PNGs for
the user to regenerate with external AI tooling, and installs the
results as engine-readable TGA overrides.

Generation itself is out of scope (user generates manually).

## Architecture

One shared helper per tree, four call seams per tree, zero new
filesystem code.

- `COM_FindFile` (common/common.c:1362) already resolves loose files
  under the game directory in addition to pak contents — override
  files are placed loose, so `COM_OpenFile` finds them as-is.
- `GL_Upload32` (render/gl_draw.c:1003) already handles 32-bit RGBA
  upload, non-power-of-2 resampling, `gl_max_size` clamping, and
  mipmap generation.
- No PNG decoder is added to the engine. Overrides are uncompressed
  TGA (type 2), the format the engine natively understands
  (LoadTGA precedent at render/gl_warp.c:488). Format conversion is
  a tool-side concern (`tools/install.py`).

### New helper (both trees, in `gl_draw.c`)

```c
int GL_TryLoadExternalTexture (char *identifier, char *path,
                               int orig_w, int orig_h,
                               qboolean exact_size,
                               qboolean mipmap, qboolean alpha);
```

Returns a GL texture number, or 0 on any failure. Declared in
`glquake.h`. Behavior:

1. `gl_externaltextures.value == 0` → return 0 silently.
2. `COM_OpenFile` the path; absent → return 0 silently (normal case).
3. Read whole file into a `malloc` buffer, `COM_CloseFile`, then
   validate in memory.
4. Validate: length ≥ 18; `image_type == 2` (uncompressed);
   `colormap_type == 0`; `pixel_size` ∈ {24, 32}; `width`, `height`
   > 0; **exact length** == `18 + w*h*(bpp/8)`. RLE/truncated/foreign
   content is rejected before any decode.
5. Dimension invariant: `exact_size ? (w == orig_w && h == orig_h)`
   : `(w*orig_h == h*orig_w)` (same aspect). Violation → warn, 0.
6. Convert bottom-up BGR(A) → top-down RGBA in place; `GL_Bind` a
   fresh `texture_extension_number`; `GL_Upload32`; register a
   `gltextures` cache entry **under the original identifier with the
   original dimensions** (so a later `GL_LoadTexture` hit for the
   same identifier matches instead of tripping the cache-mismatch
   `Sys_Error` at gl_draw.c:1247); `Con_Printf` one line; return
   texnum.

Every failure is `Con_Printf` + return 0. The helper never calls
`Sys_Error` on content. This is the deliberate deviation from
reusing `LoadTGA` (gl_warp.c:488): that function hard-errors on
format surprises, which is wrong for optional user content. The
decode duplicated here is a bounded channel-swap row copy, not a
second parser.

### Call seams (identical shape in both trees)

| Class | Seam | Override path | Invariant | mipmap / alpha |
|---|---|---|---|---|
| Brush | `Mod_LoadTextures`, gl_model.c:394 (the non-sky branch) | `textures/<name>.tga` | aspect | true / false |
| Alias skin (single) | `Mod_LoadAllSkins`, gl_model.c:1444 | `<model name>_<i>.tga` (extension kept, e.g. `progs/s_light.mdl_0.tga`) | aspect | true / false |
| Alias skin (group) | gl_model.c:1469 | `<model name>_<i>_<j>.tga` (extension kept) | aspect | true / false |
| Sprite frame | `Mod_LoadSpriteFrame`, gl_model.c:1689 | `<sprite name>_<framenum>.tga` (extension kept, e.g. `progs/s_light.spr_0.tga`) | aspect | true / true |
| Pics from lmp | `Draw_CachePic`, gl_draw.c:231 | path minus `.lmp`, plus `.tga` (e.g. `gfx/conback.tga`) | exact w/h | false / true |
| Pics from wad | `Draw_PicFromWad`, gl_draw.c:183 | `gfx/<lumpname>.tga` (e.g. `gfx/conchars.tga`) | exact w/h | false / true |

Notes per seam:

- **Brush/skin/sprite identifiers stay exactly what the 8-bit path
  uses** (`mt->name`, `"%s_%i"` / `"%s_%i_%i"` from `loadmodel->name`,
  `"%s_%i"`), so the `gltextures` cache behaves identically.
  Override *paths* are the identifier plus `.tga` for skins/sprites
  — i.e. the full `loadmodel->name` with its extension kept
  (`progs/s_light.mdl_0.tga` vs `progs/s_light.spr_0.tga`), which
  stays collision-free because engine identifiers already differ by
  extension. Brush overrides keep the `textures/` prefix. One small
  local path builder in gl_model.c constructs them.
- **Skin texel copy is untouched**: `pheader->texels` (8-bit) stays
  populated because player-color translation consumes it.
- **Pics replace only the GL texture.** The original lump/wad data
  still loads and keeps its roles: `pic.width/height` (menu layout is
  pixel-exact), CPU-side bytes (`draw_chars` conback baking,
  `menuplyr_pixels`), translation source. Small wad pics (scrap
  atlas path, gl_draw.c:192) take the full-texture path when an
  override exists — the scrap atlas is 8-bit shared storage.
- **Exclusions** (seam skips lookup, documented in tools README):
  - `progs/player.mdl` skins — the renderer rebuilds that texture
    from 8-bit texels for shirt/pants translation; an override would
    be overwritten or break recoloring.
  - `gfx/menuplyr.lmp` — same translation mechanism.
  - `sky*` brush textures — `R_InitSky` is a separate sky pipeline;
    the seam only covers the non-sky branch.
- Sky textures are excluded from the extractor manifest too, with a
  note.

### Cvar

`gl_externaltextures`, default `"1"`, registered in `R_Init`
(gl_rmisc.c) alongside the other `gl_*` cvars, both trees. Toggling
off at runtime affects subsequently loaded textures only (matches
every other `gl_*` texture cvar in this engine — no cache teardown).

## Tools (`tools/`, committed; output git-ignored)

Dependency: Pillow (`pip install Pillow`) for PNG I/O. Pak/BSP/WAD/
MDL/SPR parsing is stdlib `struct`.

### `tools/extract.py [pakdir]`

Defaults to `game/id1`. Reads `pak0.pak`…`pakN.pak`, extracts:

- **Brush textures** — `maps/*.bsp`, BSP29 `LUMP_TEXTURES` miptex
  entries, colored with the `palette` lump from `gfx.wad` in the
  same paks. Dedup by name (first wins; differing-pixel collisions
  listed in the manifest report). `sky*` skipped.
- **gfx.wad lumps** — WAD2 directory walk; miptex-style and pic-style
  lumps → PNG.
- **Alias skins** — `progs/*.mdl`: MDL header walk (handles skin
  groups), one PNG per skin.
- **Sprite frames** — `progs/*.spr`: frame and frame-group walk, one
  PNG per frame.

Output: `tools/extracted/{textures,gfx,models,sprites}/*.png` plus
`tools/extracted/manifest.json` — one entry per texture:
`{name, category, width, height, override_path}`. Prints a summary
(counts per category, collision notes).

### `tools/install.py <png-or-directory>`

Matches input PNG stem against `manifest.json`, then:

- **Validates** the class invariant (exact w/h for gfx; same aspect
  otherwise). Rejection messages name the expected shape.
- Writes uncompressed 24/32-bit bottom-up TGA (attribute bit5 = 0)
  to `game/id1/<override_path>` — exactly the byte layout the helper
  accepts.
- Batch mode: point at a directory of generated images; prints an
  installed/rejected/unknown report.

Overrides go to `game/id1/` because both `glquake` and `glqwcl`
mount id1 (CONTEXT.md), so one set serves both binaries. A file in
`game/qw/` wins over id1 for glqwcl (search order).

### `tools/README.md`

Usage, conventions (aspect rule, exact-size rule, TGA 24/32
uncompressed), exclusions and why (`player.mdl`, `menuplyr`, sky),
where files land, how glqwcl finds them.

## File placement and git

- Committed: engine edits (both trees), `tools/extract.py`,
  `tools/install.py`, `tools/README.md`, this spec.
- `.gitignore` additions: `tools/extracted/` (extracted PNGs are
  derivatives of commercial game data — same rule as `game/`,
  AGENTS.md).
- Override TGAs live under `game/id1/` — already git-ignored.

## Error handling summary

| Failure | Response |
|---|---|
| `gl_externaltextures 0` | skip silently |
| override file absent | silent fallback (normal case) |
| malformed / RLE / truncated TGA | warn, fallback (rejected pre-decode by exact size check) |
| dimension invariant violated | warn once with expected vs got, fallback |
| unknown PNG stem at install time | skip with report line |
| invariant violated at install time | reject with expected shape |

No crash path reachable from file content.

## Verification

1. **Build oracle**: `make clean && make build-release build-server
   build-client` from repo root — exit 0 (AGENTS.md).
2. **Extract proof**: run `tools/extract.py`; manifest counts sane
   per category; spot-check PNGs against known originals.
3. **Round-trip proof** (correctness gate): install an unmodified
   extracted PNG; `make run`, `map start`; console prints the
   external load line; rendering visually identical to no-override.
4. **Override proof**: nearest-4x upscaled texture installs, loads,
   renders crisper; a wrong-aspect TGA is warned + falls back; a
   truncated TGA is warned + falls back; `gl_externaltextures 0`
   restores originals for newly loaded maps.
5. **QW proof**: glqwcl against local qwsv shows the same overrides
   from `game/id1/`.
6. **Smoke**: 3-binary SIGKILL protocol, `Received signal` counts
   0/0/0 (CONTEXT.md).

## Out of scope

- Texture generation (user-provided images).
- QuakeWorld server (no rendering).
- Sky override (`R_InitSky` pipeline).
- Per-map texture search paths, pk3-style archives, PNG-in-engine.
