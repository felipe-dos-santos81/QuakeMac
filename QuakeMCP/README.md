# QuakeMCP

An MCP (Model Context Protocol) server that plays and inspects this
repository's `glquake`. A small C bridge compiled into the engine exposes a
token-authenticated loopback control channel; the Python server supervises the
process and turns that channel into 13 MCP tools: lifecycle, bounded movement
and view actions, rendered frames, state telemetry, guarded console/config
access, and save/map operations.

The bridge is compiled into the engine only under `QUAKE_MCP=1`; every hook
is guarded by `#ifdef QUAKE_MCP` or by a macro shim that compiles it out of
vanilla builds, so the plain `make build-release` binary is unchanged.
QuakeWorld is untouched.

## Layout

| Path | Contents |
|---|---|
| `bridge/` | C bridge compiled into glquake (`q_mcp.c` socket/lease/actions, `q_mcp_input.c` input merge, `q_mcp_capture.c` framebuffer readback) |
| `src/quakemcp/` | Python server (`server.py` tools, `lifecycle.py` supervision, `engine.py` bridge client, `models.py` types, `vision.py` image encoding) |
| `tests/` | `unit/` + `contract/` (no engine) and `integration/` (launch the real binary) |
| `docs/` | Hook inventory + capability matrix (`engine-integration.md`), measured results (`acceptance-results.md`) |

## Requirements

- macOS/arm64 with Xcode Command Line Tools (`cc`, `make`), SDL3
  (`brew install sdl3 pkg-config`) — same as the root README
- Python 3.12+ with `mcp==1.29.0` and `Pillow>=12.0`
- Game data in `game/` (`game/id1/pak0.pak`)

## Build

```
make clean && make build-release QUAKE_MCP=1     # → Quake/build-macosx/glquake
```

`make clean` matters: without it, switching between the vanilla and MCP
variants does not relink and you get the stale binary.

## Run

Start the stdio server from the repository root:

```
PYTHONPATH=QuakeMCP/src python3 -m quakemcp.server
```

MCP client configuration:

```json
{
  "mcpServers": {
    "quake": {
      "command": "python3",
      "args": ["-m", "quakemcp.server"],
      "cwd": "<repo>",
      "env": {"PYTHONPATH": "<repo>/QuakeMCP/src"}
    }
  }
}
```

The server launches the engine itself (`quake_start`), so nothing else needs
to be running. `quake_attach` connects to an existing instrumented instance
given its port and token file contents.

## Quick session

```
quake_start(profile="local", port=28900)                 # -> instance; stepped
quake_game(instance, operation="new_game")
ctl = quake_control(instance, operation="acquire")       # -> lease, epoch
quake_act(instance, forward=1.0, ticks=10, lease=ctl["lease"],
          epoch=ctl["epoch"], action_seq=1, action_id="a1")
quake_observe(instance)                                  # PNG + state, same frame
quake_ui(instance, key="escape")                         # open the menu
quake_release(instance)
quake_stop(instance)
```

Reusing a lease means advancing `action_seq` (1, 2, 3, …) yourself; omitting
the lease lets `quake_act` acquire one lazily and default the sequence to 1.
An owned session starts in stepped mode; switch with
`quake_control(instance, operation="mode", mode="realtime")`.

`quake_act` and `quake_observe` return an image block plus structured state;
both carry the same `frame`, so pixels and telemetry match.

## Tools

| Tool | Purpose |
|---|---|
| `quake_start` / `quake_stop` | Launch or terminate an owned instance |
| `quake_attach` | Attach to an authorized instrumented instance |
| `quake_status` | Connection, instance, capabilities and bridge state; action receipt by id |
| `quake_state` | Read-only state snapshot |
| `quake_observe` | Rendered frame + matching telemetry |
| `quake_act` | One bounded action (move/look/attack/jump/weapon), with completion observation |
| `quake_game` | `new_game`, `restart`, `load_map`, `save`, `load`, `respawn`, `list_maps`, `list_saves` |
| `quake_ui` | Key/text input for menus, console and message prompts |
| `quake_console` | One allowlisted console command with validated arguments |
| `quake_config` | Read/write curated input, view, audio and `access_*` settings |
| `quake_control` | Acquire/release/detach the controller lease; set stepped/realtime mode |
| `quake_release` | Priority emergency stop: cancel actions, neutralize input |

## Control and timing

- The bridge serializes mutations through a controller lease (2 s expiry,
  heartbeated every 500 ms by the server). Actions carry a `lease`, an optional
  `action_id`, and a monotonic `action_seq`; mutating tools also accept
  `epoch` and pinned `world_generation`/`control_revision` preconditions.
  Retrying an `action_id` returns the recorded result instead of executing
  twice. Expiry is uniform: beats land on a second connection while an
  `act`/`observe` reply is deferred, and an expired lease interrupts the
  action instead of letting it run to its wall cap.
- The bridge serves two connections at most — the call channel plus one
  control channel — so heartbeat and `release` stay serviceable while a reply
  is deferred; a third connection is closed.
- Owned sessions start in stepped mode; it freezes the simulation between
  actions and advances exactly `ticks` steps. Realtime mode runs normally and
  accepts `duration_ms`.
- MCP input never touches the physical attack/jump bits; acquiring the lease
  refuses while those buttons are held.

## Tests

```
make clean && make build-release QUAKE_MCP=1
python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract   # no engine
python3 -m pytest QuakeMCP/tests/integration                    # real binary
```

Integration tests skip when `game/id1/pak0.pak` or the MCP build is missing.
In a git worktree, link the game data first (`ln -s ../../game game`) and
remove the link before any commit.

## Safety model

Loopback-only listener; the per-process token file is written `0600` and is
never logged or passed on a command line. At most two concurrent connections
(the call and one control channel), each token-authenticated. Console
access is an allowlist of commands with typed argument validators, never a
raw command line; settings are a curated list with read-only access cvars.
Engine logs go to `$TMPDIR/quakemcp-logs/`, never the repo.

## Known limitations

- Intermission is unreachable from the tool surface: the progs write
  `svc_intermission` only on a level exit, and `map end` leaves the player
  dead (`intermission` false). Death and respawn are traced and supported —
  `kill` through the console allowlist, `quake_game respawn` for a dead
  player; see the death-flow table in `docs/engine-integration.md`.
- Water movement and vision usability are partially verified; details and
  evidence in `docs/acceptance-results.md`.
- macOS/glquake only; the headless `qwsv` and `glqwcl` have no bridge.

## Documentation

- `docs/engine-integration.md` — hook sites, op map, capability matrix
- `docs/acceptance-results.md` — gates, measurements, known failures
- `../docs/superpowers/2026-09-13-quakemcp-design.md` and
  `2026-09-13-quakemcp-plan.md` — design spec and implementation plan
