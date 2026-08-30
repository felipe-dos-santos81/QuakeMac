# AGENTS.md

Fork of [id-software/quake](https://github.com/id-software/quake) (the
1999 GPL release), simplified into a macOS Apple Silicon (arm64) port
for educational purposes only. Three binaries: `glquake` (`Quake/`),
`qwsv` and `glqwcl` (`QuakeWorld/`).

- Domain language and module layout: `CONTEXT.md`.
- Build oracle (must pass before claiming work done): `make clean &&
  make build-release build-server build-client` from the repo root.
- Specs, plans, and the append-only Fixes Ledger:
  `docs/superpowers/`.
- Game data is user-supplied in `game/` (gitignored) — never commit
  `.pak` files or other assets from the commercial game.
- License: GPL, see `gnu.txt`.
