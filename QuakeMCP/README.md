# QuakeMCP

An MCP server that plays and inspects this repository's `glquake`. A C bridge
compiled into the engine exposes a token-authenticated loopback control
channel; the Python server supervises the process and turns it into 13 MCP
tools: lifecycle, bounded movement/view actions, rendered frames, telemetry,
guarded console/config access, and save/map operations.

The bridge is built only under `QUAKE_MCP=1`, behind `#ifdef QUAKE_MCP` or
no-op macro shims, so vanilla builds and `QuakeWorld/` stay unchanged.

## Layout

| Path | Contents |
|---|---|
| `bridge/` | C bridge in glquake (`q_mcp.c` socket/lease/actions, `q_mcp_input.c` input merge, `q_mcp_capture.c` framebuffer readback) |
| `src/quakemcp/` | Python server (`server.py` tools, `lifecycle.py` supervision, `engine.py` bridge client, `models.py` policy/types, `vision.py` image encoding) |
| `tests/` | `unit/` + `contract/` (no engine), `integration/` (real binary) |
| `docs/` | Hook inventory + op map (`engine-integration.md`), measured results (`acceptance-results.md`) |

## Requirements

- macOS/arm64 with Xcode CLT (`cc`, `make`) and SDL3
  (`brew install sdl3 pkg-config`) — see the root README.
- Python 3.12+ with `mcp==1.29.0` and `Pillow>=12.0` (pinned in
  `pyproject.toml`).
- Game data in `game/id1/pak0.pak` (never committed).

## Build

```
make clean && make build-release QUAKE_MCP=1     # -> Quake/build-macosx/glquake
```

`make clean` matters: switching between the vanilla and MCP variants does not
relink otherwise.

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

`quake_start` launches the engine itself — nothing else needs to be running.
`quake_attach(instance, port, token)` connects to an existing instrumented
instance.

## Quick session

```
quake_start(profile="local", port=28900)              # -> instance; stepped
quake_game(instance, operation="new_game")
ctl = quake_control(instance, operation="acquire")    # -> lease, epoch
quake_act(instance, forward=1.0, ticks=10, lease=ctl["lease"],
          epoch=ctl["epoch"], action_seq=1, action_id="a1")
quake_observe(instance)                               # PNG + state, same frame
quake_ui(instance, key="escape")                      # open the menu
quake_release(instance)
quake_stop(instance)
```

Reusing a lease means advancing `action_seq` (1, 2, 3, …) yourself; omitting
the lease lets `quake_act` acquire one lazily with sequence 1. Owned sessions
start stepped — switch with `quake_control(instance, operation="mode",
mode="realtime")`. `quake_act` and `quake_observe` return an image block plus
structured state, both carrying the same `frame`.

## Tools

| Tool | Purpose |
|---|---|
| `quake_start` / `quake_stop` | Launch or terminate an owned instance |
| `quake_attach` | Attach to an authorized instrumented instance |
| `quake_status` | Connection, capabilities, bridge state; action receipt by id |
| `quake_state` | Read-only state snapshot |
| `quake_observe` | Rendered frame + matching telemetry |
| `quake_act` | One bounded action (move/look/attack/jump/weapon) + completion report |
| `quake_game` | `new_game`, `restart`, `load_map`, `save`, `load`, `respawn`, `list_maps`, `list_saves` |
| `quake_ui` | Key/text input for menus, console, and message prompts |
| `quake_console` | One allowlisted console command with validated arguments |
| `quake_config` | Read/write curated input, view, audio, and `access_*` settings |
| `quake_control` | Acquire/release/detach the lease; set stepped/realtime mode |
| `quake_release` | Priority emergency stop: cancel actions, neutralize input |

## Protocol at a glance

- Every mutation carries `lease`, `epoch`, and `seq` (plus optional
  `action_id`, `world_generation`, `control_revision`); reads never do.
- Retrying an `action_id` replays the recorded result instead of executing
  twice. A frame dropped from the server's bounded receipt cache raises
  `FRAME_EXPIRED` — it is never re-shot. A spent sequence with no receipt is
  `RESULT_EXPIRED`.
- The bridge serves two connections (the call channel plus one control
  channel), so heartbeats and `release` stay serviceable while an `act` or
  `observe` reply is deferred; a third connection is closed.
- The lease expires after 2 s of silence; the server heartbeats every 500 ms.
- Stepped mode freezes the simulation between actions and advances exactly
  `ticks` steps; realtime mode runs normally and accepts `duration_ms`.

## Tests

```
make clean && make build-release QUAKE_MCP=1
python3 -m pytest QuakeMCP/tests/unit QuakeMCP/tests/contract   # no engine
python3 -m pytest QuakeMCP/tests/integration                    # real binary
```

Integration modules skip when `game/id1/pak0.pak` is missing (most also check
the MCP binary). In a git worktree, link the game data first
(`ln -s ../../game game`) and remove the link before committing.

## Safety

Loopback-only listener. The per-process token file is written `0600` and is
never logged or passed on a command line; every connection is
token-authenticated. Console access is an allowlist with typed argument
validators — never a raw command line — and settings are a curated list with
read-only `access_*` cvars. Engine logs go to `$TMPDIR/quakemcp-logs/`, never
the repo.

## Known limitations

- Intermission is unreachable from the tool surface: the progs write
  `svc_intermission` only on a level exit, and `map end` leaves the player
  dead (`intermission` false). Death and respawn are traced and supported —
  see the death-flow table in `docs/engine-integration.md`.
- Water movement and vision usability are partially verified; evidence in
  `docs/acceptance-results.md`.
- glquake only; the headless `qwsv` and `glqwcl` have no bridge.

## Documentation

- `docs/engine-integration.md` — hook sites, op map, capability matrix
- `docs/acceptance-results.md` — gates, measurements, known failures
- `../docs/superpowers/2026-09-14-quakemcp-conformance-design.md` — current
  design (refines `2026-09-13-quakemcp-design.md`); plan alongside it
- `AGENTS.md` — subproject rules for agents
