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
