# AGENTS.md

Fork of [id-software/quake](https://github.com/id-software/quake) (the
1999 GPL release), simplified into a macOS Apple Silicon (arm64) port
for educational purposes only. Three binaries: `glquake` (`Quake/`),
`qwsv` and `glqwcl` (`QuakeWorld/`).

- Domain language and module layout: `CONTEXT.md`.
- Build oracle (must pass before claiming work done): `make clean &&
  make build-release build-server build-client` from the repo root.
  There are no tests, no lint, no CI — the build is the verification.
- Prereqs: Xcode CLT and SDL3 resolvable via pkg-config
  (`brew install sdl3 pkg-config`); without it the Makefile fails
  opaquely at parse time.
- `Quake/` and `QuakeWorld/` are separate trees: same-named files
  (e.g. `common/common.c`) are independent copies that may drift.
  Never assume a change to one applies to the other.
- Runtime smoke checks (need user game data): `make run` /
  `make run-server` / `make run-client`.
- Game data is user-supplied in `game/` (gitignored) — never commit
  `.pak` files or other assets from the commercial game. Loose TGA
  overrides under `game/id1/` (and `game/qw/` for QW) are game data
  too — same rule; the committed pipeline that produces them lives in
  `tools/` (decoupled, Pillow-only).
- Specs, plans, and the append-only Fixes Ledger:
  `docs/superpowers/`.
- License: GPL, see `gnu.txt`.
