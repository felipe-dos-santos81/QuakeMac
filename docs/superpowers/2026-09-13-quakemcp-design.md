# Design: QuakeMCP — MCP control + vision for glquake

- **Date:** 2026-09-13
- **Status:** Approved (design); implementation plan to follow via writing-plans
- **Scope (locked):** `glquake` only. Sidecar + socket stub. Full v1 control
  (console+cvars, input injection, lifecycle+state). Streaming vision
  5–10 fps @ ≤640px. Python MCP SDK sidecar in `QuakeMCP/`.

## 1. Background

The engine has no external control surface: commands flow through
`Cbuf_AddText`/`Cmd_ExecuteString`, cvars via `Cvar_*`, input via
`Key_Event`/`IN_*` (SDL3 pump in `Quake/platform/in_sdl.c`), frames via the
OpenGL renderer (`glReadPixels` path already proven by `SCR_ScreenShot_f` in
`Quake/render/gl_screen.c`, which writes TGA via `COM_WriteFile`).
`Host_Frame` in `Quake/host.c` orchestrates every module each frame.
There is no `QuakeMCP/` yet. MCP spec (via Context7): stdio transport,
`tools/list` + `tools/call` over JSON-RPC, vision as base64 `ImageContent`.

## 2. Goals and non-goals

**Goals**

- `QuakeMCP/` Python sidecar (MCP stdio server) that fully drives one running
  `glquake`: console/cvars, key/mouse/button injection, lifecycle
  (`map`/`save`/`load`/`quit` via existing commands), state read, and
  streaming vision for agent grounding.
- Minimal C touch: one new module, default-off, localhost-only.

**Non-goals**

- `QuakeWorld/` (`qwsv`, `glqwcl`): untouched in v1.
- New console commands, renderer changes, netcode changes, auth/multi-user.
- Full-res 30 fps streaming; audio capture; process management beyond
  existing console commands.

## 3. Architecture

```
MCP client <-stdio/JSON-RPC-> QuakeMCP/server.py <-TCP localhost:28900-> glquake
                                                                          `- Quake/mcp/mcp_bridge.c (new, polled once per Host_Frame)
```

- `Quake/mcp/` — new module (`mcp_bridge.c/.h`). Owns socket accept/poll,
  line-JSON dispatch into `Cbuf_AddText` / `Cvar_*` / `Key_Event`, state read,
  `glReadPixels` capture. Wiring: +2 lines in `Makefile`
  (`QUAKE_CORE_OBJS` or `QUAKE_PLATFORM_OBJS`), +1 poll call in `Host_Frame`.
  `Quake/` tree only.
- `QuakeMCP/` — `server.py` (MCP stdio), `engine_link.py` (TCP client +
  validation + throttle), `tools_*.py`. Deps: `mcp`, `Pillow`. Frames never
  touch `game/`.
- Safety: localhost-only bind, single client, disabled by default
  (`-mcp_port` / `mcp_enabled 0`). `access_mouseonly 0` paths stay
  byte-identical to vanilla. Vision throttled with drop-if-busy; never blocks
  the game loop.

## 4. Components / MCP tools (7)

| Tool | Maps to |
|---|---|
| `quake.exec {text}` | `Cbuf_AddText`; returns console tail |
| `quake.cvar {get\|set}` | `Cvar_*` read/write |
| `quake.key {key, down}` | `Key_Event` |
| `quake.look {dx,dy}` + `quake.buttons {...}` | existing `IN_*` motion/button paths |
| `quake.state {}` | pose, health, ammo, map, `cls.state`/`signon` as JSON |
| `quake.screenshot {w=640}` | single PNG base64 `ImageContent` |
| `quake.stream {fps=5-10, w=640}` / `quake.stream_stop` | throttled frames, server-side drop-if-busy |

No new engine console commands in v1; the bridge reuses the existing
`Cmd_`/`Cvar_` surface.

## 5. Data flow

- Control: `tools/call` -> `engine_link.py` validates -> TCP line-JSON ->
  bridge dispatches in `Host_Frame` -> `Cbuf_`/`Key_Event` -> result +
  console tail back over the same socket -> `CallToolResult`
  (`isError:true` on engine-side errors, never a protocol error).
- Vision: bridge `glReadPixels` post-`SCR_UpdateScreen`, downscale to ≤640px,
  encode PNG (still) / JPEG (stream) in Python via Pillow -> base64
  `ImageContent`. Throttle 100–200 ms; drop frames when busy.
- Lifecycle only via existing commands (`map`, `save`, `load`, `quit`); the
  bridge never kills the process.

## 6. Errors, safety, invariants

- Transport: non-blocking ~50 ms poll; malformed JSON -> `isError:true`;
  engine never aborts on bridge errors.
- Game: unknown command/cvar -> engine error string as tool error; injection
  outside `ca_connected` rejected except menu-safe commands.
- Invariants: `access_mouseonly 0` = vanilla input paths; `game/` user
  territory untouched (no paks/saves committed); `QuakeWorld/` untouched;
  `Draw_FillAlpha`/`Draw_StringAlpha` pattern untouched.
- Vision failure (GL stall, 0-size) -> text error, no retry storm.

## 7. Testing / gates

- Build oracle stays green:
  `make clean && make build-release build-server build-client` exits 0
  (bridge compiles into `glquake` only, off by default).
- New checks: `python3 -m py_compile QuakeMCP/*.py`; MCP `tools/list`
  returns 7 tools; `quake.exec help` round-trip; `quake.screenshot` returns
  a valid PNG; 5 fps stream for 10 s with no game-loop abort and 0
  `Received signal` lines in the 3-binary SIGKILL smoke protocol.
- Fixes Ledger entry appended per repo convention.
