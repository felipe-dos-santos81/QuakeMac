# QuakeMCP: game control and vision specification

**Date:** 2026-09-13  
**Status:** Proposed design; not implemented or approved  
**Location:** `QuakeMCP/` at the game repository root

## 1. Review basis and scope

The supplied `2026-08-14-code-reorg-design.md` describes reorganizing `~/code` repositories, not Quake functionality. It remains unchanged. No Quake codebase or existing Quake specification was supplied; this document is a separate proposed specification, not a verified patch against an existing game implementation.

**Assumption:** Quake means original Quake 1, with permission to modify the existing engine. Use its current language, build system and supported platforms; do not replace the engine merely to add MCP. Original id Software sources provide reference integration points, not proof that the user's fork has identical files. Context7 was consulted for the official MCP Python SDK and Quake; original engine sources were checked separately because retrieved Quake examples mixed QuakeWorld and original-client behavior. [S1-S7]

**Goal:** An MCP client can start or attach to Quake, perform every normal single-player interaction, observe actual rendered images, and repeat an observe-act-observe loop without desktop automation. "Full control" includes gameplay, menus, dialogs, supported settings, map/session lifecycle and saves; it does not mean unrestricted operating-system access.

Version 1 targets a rendered local single-player session. QuakeWorld, other Quake titles, arbitrary unmodified binaries, dedicated-server vision and remote multiplayer are not claimed. Add them only through separately tested adapters/capabilities.

## 2. Review findings and decisions

| Gap or risk | Required specification change |
|---|---|
| No Quake requirements in the attachment | Establish this explicit baseline; preserve the unrelated approved document. |
| Console execution mistaken for full control | Add engine-level movement, aiming, button, impulse and UI input integration. |
| An image path mistaken for vision | Return actual MCP image content plus synchronized observation metadata. |
| Input continues while a model thinks or disconnects | Bound every action; provide stepped execution, an engine-side watchdog and emergency release. |
| Old frames or state mismatched with actions | Correlate action completion, simulation state and render frame before returning. |
| Blocking menus or dialogs freeze automation | Service bridge control and observation outside ordinary gameplay frames. |
| "Vision" silently means privileged state | Label telemetry separately; image interpretation belongs to a vision-capable client or an explicitly configured vision provider. |

**Recommended approach:** A small C bridge compiled into the existing engine plus an out-of-process Python MCP server. Desktop key injection would introduce focus and operating-system dependencies; a console-only adapter would leave synchronization and rendered-frame access unresolved. Keep those alternatives out of the primary implementation.

## 3. Architecture and ownership

The data path is: **MCP client -> Python MCP server -> authenticated local IPC -> engine main-thread bridge -> game input/simulation -> renderer -> immutable observation -> MCP result**.

The MCP server owns schemas, lifecycle supervision, request validation and image encoding. The bridge owns input arbitration, simulation scheduling and snapshot production. Only the engine/render thread touches game state or graphics APIs. Encoding and network writes operate on copied buffers and must not block that thread.

Planned layout, not files implemented by this specification:

```text
QuakeMCP/
  SPEC.md
  README.md
  pyproject.toml
  src/quakemcp/
    server.py          # MCP registration and transport
    models.py          # shared request/result validation
    engine.py          # local bridge client and request correlation
    lifecycle.py       # configured launch/attach/stop supervision
    vision.py          # frame encoding, resizing and metadata
  bridge/
    q_mcp.h
    q_mcp.c            # protocol, queue, ownership and watchdog
    q_mcp_input.c      # gameplay and UI adapters
    q_mcp_capture.c    # renderer-specific snapshot adapters
  tests/
    unit/
    contract/
    integration/
  docs/
    engine-integration.md
    acceptance-results.md
```

Reuse existing project components where suitable. Keep new implementation in `QuakeMCP/`; only minimal hook calls and build registrations belong elsewhere. Compile the bridge behind `QUAKE_MCP`, disabled by default. Build registration in the root `Makefile`: bridge sources under `QuakeMCP/bridge/` need one new pattern rule plus a build-dir `mkdir` target (existing rules only cover `Quake/` module dirs and the tree root), with `-DQUAKE_MCP` wired into the glquake `CFLAGS` when enabled. A normal game build must not require Python, open a control endpoint, or change input behavior.

Use the official MCP Python SDK rather than hand-writing MCP. Pin the chosen released SDK and dependencies in the project's lockfile; verify its actual API rather than copying version-dependent attribute names from examples. [S1]

### Reference hook map (verified against this fork)

| Engine responsibility | Fork hook | Integration requirement |
|---|---|---|
| Movement, angles, firing, jump and impulses | `Quake/client/cl_input.c`: `CL_BaseMove` (283), `CL_SendMove` (332); attack/jump bits + impulse serialized (364-375); `+use` registered (437) | Inject from separate MCP input state in `CL_SendCmd` (`Quake/client/cl_main.c:673`) after `IN_Move`, before `CL_SendMove`; movement alone does not cover buttons or impulses. [S4] |
| Simulation scheduling | `Quake/host.c`: `_Host_Frame` (644), `Host_FilterTime` (506), `Host_ServerFrame` (565) | Pump control even when simulation is not advancing; identify actual completed simulation steps. `MCP_Poll` must run before the `Host_FilterTime` early-return (658-659), which precedes `Sys_SendKeyEvents` (666) — otherwise filtered frames stall the 100 ms release path. Client frames are otherwise unthrottled in the `sys_unix.c` outer loop (395-421); the cap is `host_maxfps` (default 72). [S5] |
| Menus, console and dialogs | `Quake/client/keys.c`: `Key_Event` (599); `key_dest_t` (`keys.h:121`); `Quake/render/gl_screen.c`: `SCR_ModalMessage` (739) | Route UI input by `key_dest`. The modal key-wait loop (754-758) pumps `Sys_SendKeyEvents` only, so the bridge socket poll needs a one-line hook there (or a poll inside the SDL pump path), or `quake_ui` input starves during dialogs. `Key_Event` injection flows through the existing modal pump. [S6-S7] |
| Mouse-only arbitration | `Quake/client/cl_access.c`: `access_mouseonly` defaults to `"1"` (40); `Access_Frame` runs every frame (`host.c:672`) with early-outs on `access_mouseonly == 0` | MCP input must be deterministic under both `access_mouseonly` states; the capability matrix records effective behavior at the shipped default (1). Never alter access-module behavior when `access_mouseonly` is 0. |
| Rendered image | `Quake/render/gl_screen.c`: `SCR_UpdateScreen` (824); `GL_BeginRendering` sets `glx/gly/glwidth/glheight` (852); `glReadPixels` shot path (630) with RGB-to-BGR swap | Capture after world/HUD/menu composition and before buffer presentation; do not invoke graphics APIs from an IPC worker. Reuse the shot path geometry with no disk files on the repeat path. [S7] |
| Save, load and map operations | `Quake/host_cmd.c`: `Host_Map_f` (256), `Host_Savegame_f` (465), `Host_Loadgame_f` (561); commands `map`, `restart`, `changelevel`, `save`, `load`, `kill`, `pause` (1883-1905); `wait` (`common/cmd.c:438`) | Wrap supported operations with typed validation, completion detection and error handling. [S8] |

Paths re-verified against the fork; re-check line numbers if the tree moves. A differently named equivalent is acceptable; an unverified assumed hook is not.

## 4. MCP tool surface

All tools have strict input schemas and documented output schemas. Unknown fields and unsupported operations fail explicitly. Mutations share one controller lease and execute serially; reads and emergency release remain responsive.

| Tool | Required behavior |
|---|---|
| `quake_status(action_id?)` | Report connection, instance/epoch, capabilities, controller, execution mode, UI context and loading/dead/intermission flags; query a specific action or the latest action. |
| `quake_start(profile_id)` | Launch a preconfigured executable, data directory and arguments; wait for an authenticated ready state. Never accept a shell command. |
| `quake_attach(instance_id)` | Attach only to an instrumented, explicitly authorized instance; do not claim control of an arbitrary stock binary. |
| `quake_stop()` | Gracefully stop a process launched by this server. Refuse to terminate an attached user-owned process. |
| `quake_control(operation, ...)` | Acquire/release control, detach, select execution mode or set an explicit paused state. No ambiguous pause toggle. |
| `quake_act(action_id, action_seq, epoch, world_generation, control_revision, ...)` | Execute simultaneous movement, aiming, buttons and weapon selection for a bounded interval; return the resulting observation. |
| `quake_observe(after_frame_id?, timeout_ms?, image_options?)` | Return a fresh rendered image and matching metadata without advancing stepped gameplay. |
| `quake_ui(action_id, context, key?, text?)` | Navigate menus and dialogs; deliver matched key-down/key-up events. Text is allowed only in validated UI text fields, not as an unrestricted console-submission bypass. |
| `quake_game(action_id, operation, ...)` | Typed ops map to engine commands (`Quake/host_cmd.c`): `new_game`->`map <firstmap>`, `restart`->`restart`, `load_map`->`map`/`changelevel`, `save`/`load`->`save`/`load`; `list_maps`/`list_saves` are sidecar gamedir inventories (no engine commands exist); `respawn` death-flow semantics must be verified in `engine-integration.md`, not assumed; enforce state prerequisites and report completion, not just command enqueueing. |
| `quake_config(operation, name, value?)` | Read/write declared game settings, including supported input, audio and video options; return the effective value. |
| `quake_console(action_id, command, args)` | Execute only explicitly registered commands with typed argument validation. Raw console scripting is disabled by default. |
| `quake_release(reason?)` | Priority, idempotent emergency stop: cancel actions, revoke control and neutralize MCP input without waiting behind another mutation. |

Capabilities declare available actions, weapon identifiers, UI inputs, settings, frame formats, execution modes and telemetry policy. Optional tools must not advertise functionality that has no working backend. All lease-protected mutations carry the controller lease ID, action ID, monotonically increasing action sequence and epoch/world/control preconditions; lifecycle acquisition and priority release use their own validated authorization rules.

## 5. Gameplay action contract

`quake_act` combines `forward`, `strafe` and `vertical` values in `[-1, 1]`, a `run` flag, `yaw_delta_deg`, `pitch_delta_deg`, an `attack` flag, `jump` (`none`, `tap`, `hold`) and an optional capability-advertised `weapon_id`.

Positive axes mean forward, right and upward. Positive view deltas mean turn right and look up, regardless of native engine sign conventions. Apply the total view delta once at action start, clamp to supported view limits and report the effective change. Scale movement through normal engine speed/physics rules; never teleport or bypass collision. Vertical input is valid only where ordinary game physics allows it.

Weapon selection is a one-shot impulse, not a continuously repeated command. Report requested and resulting weapons; an unavailable weapon must not be reported as successfully selected. A jump tap lasts one simulation step and is then released; hold follows normal game semantics, not an invented auto-jump behavior.

Do not infer support from a command name alone: the fork registers `+use` (`Quake/client/cl_input.c:437`), but its movement packet serializes attack and jump bits, not a general interaction button (`cl_input.c:364-375`). Generic use, crouch and reload are not baseline capabilities. Mods require explicit mappings and tests. [S4]

Timing is a mutually exclusive union:

- **Stepped:** `ticks` from 1 to 72 at a proposed fixed `1/72` second simulation interval; the engine advances exactly the completed count reported.
- **Real-time:** `duration_ms` from 1 to 1000, bounded by an engine monotonic deadline; report actual elapsed time and simulation steps.

Both modes also have a hard wall-clock action deadline, proposed default 5 seconds, so a stalled simulation cannot hold input indefinitely. These limits are proposed defaults, not measured performance claims. Every action starts from neutral MCP input and releases it on completion. Omitted controls are neutral, never inherited from a previous call. Continuous activity uses another bounded action; do not expose unbounded button-down tools.

## 6. Scheduling, lifecycle and failure guarantees

**Stepped mode is the default for an owned local single-player session.** Freeze gameplay clocks and physics while idle, but continue IPC, UI input, rendering and watchdog handling. Do not simply block the host loop, assume ordinary pause is sufficient, or rely on the `wait` console command. Reset accumulated wall time when resuming to avoid a large catch-up step. Fixed stepping does not by itself guarantee bit-identical replay across builds or platforms.

Real-time mode keeps ordinary simulation running. Its action observation represents the captured instant, not the state when the model eventually receives it. Unsupported timing modes return an error rather than silently degrading.

Distinguish `bridge_ready` (authenticated connection, responsive UI and a usable rendered frame) from `gameplay_ready` (a loaded local world, completed sign-on and verified input acceptance). Starting at the main menu may satisfy the first without the second; allow UI/new-game tools there, but reject gameplay actions. Account for startup input suppression; the fork dumps its first two movement messages after connect (`Quake/client/cl_input.c:391-394`, counter `client.h:150`), so `gameplay_ready` additionally requires `movemessages > 2`. Do not substitute arbitrary sleeps. [S4]

Treat process state, UI context, execution mode and controller ownership as separate state dimensions. Menu/loading/death/intermission transitions can interrupt actions; return `interrupted` and completed ticks, release input, and require a new action appropriate to the new state. A manual pause rejects gameplay actions until explicitly resumed. New-game, save/load and restart operations must expose their actual engine errors. A dialog-opening operation returns `needs_input` and its modal frame instead of keeping the MCP call blocked until a future answer. The modal pump accepts authorized `quake_ui` input; the original operation ID tracks subsequent completion. This avoids serial-mutation deadlocks.

**Ownership and recovery:**

- Maintain distinct human and MCP input state. Acquisition requires neutral physical controls; explicit human takeover cancels the lease. Provide a visible left-click-accessible stop control as well as an optional hotkey; do not require keyboard input for recovery. On release, preserve or re-read genuine physical input without retaining synthetic holds.
- Heartbeat every 500 ms; expire the bridge lease after 2 seconds without a heartbeat. Use wall-clock time independent of paused simulation. On loss, clear MCP input and return ownership to the human; an owned stepped session stays simulation-paused until explicit resume.
- Cancellation, timeout, EOF, crashes, map changes and engine abort paths invalidate queued MCP actions. Emergency release is serviced by the control pump within a proposed 100 ms while the engine is responsive. A genuinely hung process cannot satisfy this guarantee; only an owned child may be terminated after its configured shutdown deadline.
- Use an engine-generated process epoch, a world generation incremented on load/restart/map changes, and a control revision incremented on mutations/takeover. Reject mismatched preconditions. Include all three in observations.
- Deduplicate mutations by lease/action ID and payload hash within the process epoch; reject reuse with different arguments. Retain a bounded result ledger and a sequence high-water mark per lease. An old sequence with an evicted result returns `RESULT_EXPIRED`, never re-executes. Revoked leases remain invalid. After authorization, check known duplicates before current-state preconditions so a legitimate retry can retrieve its receipt. Reconnection queries action status before retrying. Never promise exactly-once execution across process crashes.

Save slots and map IDs come from validated inventories. Disallow path traversal, require explicit overwrite permission and preserve engine save restrictions. Route save/load through a confined user-data location; never overwrite game assets.

## 7. Vision and observation contract

**Required vision capability:** Send the actual game image to a vision-capable MCP client. Returning only text, a filename, a URI or extracted health values does not satisfy this requirement. MCP supports image blocks alongside text and structured results. [S2]

Each successful observation returns a `content` array containing an `image` block (`image/png` by default), a concise text/JSON counterpart, and `structuredContent` containing:

| Field group | Required information |
|---|---|
| Identity | Instance ID, process epoch, world generation, control revision, observation/frame IDs and completed action ID, when applicable. |
| Timing | Capture timestamp, simulation time and step index, actual action duration/steps, and observation age when sent. |
| Image | Source/output dimensions, viewport and HUD rectangles, crop/scale transform, encoding and frame hash. |
| State | Execution mode, UI context, loading/dead/intermission state and action completion/interruption. |
| Telemetry | Policy, provenance and availability; optional HUD values captured with the same state snapshot. |

Declare an output schema and satisfy it. Keep image bytes in MCP image content rather than burying base64 inside the metadata object. Clients must preserve image blocks even when structured output is present; verify the selected client/model combination actually receives the pixels. [S2]

A successful action observation must follow the action's final completed simulation step and its corresponding render. Capture state and pixels as one immutable snapshot; never read telemetry again later and label it as the same frame. Do not increment frame IDs when rendering is skipped. A same-scene redraw may have identical pixels but a new frame ID.

Default to the whole game framebuffer, including HUD, menus and dialogs. Capture no desktop content. Support renderer-specific OpenGL readback and software-buffer conversion only after each adapter is validated. Handle row stride, vertical orientation, palette/gamma and color effects correctly. The original screenshot implementation is a reference, not a suitable repeated disk-file transport. [S7]

Image options allow lossless PNG, optional JPEG, aspect-preserving resize and crop in source-pixel coordinates. Proposed defaults: longest edge 1280 pixels, no upscaling, maximum encoded image 2 MiB. Never silently crop the HUD or downscale after a payload limit; return a size error with supported alternatives. Report the exact transform. Cap source pixels and buffer sizes before allocation.

Use a small bounded frame ring and pin an in-flight action's snapshot until encoding finishes. Readers may request a newer frame with a bounded wait. No fresh frame means `FRAME_TIMEOUT` or `RENDER_UNAVAILABLE`, not a fabricated image or an unlabelled cached result. An evicted retry frame is `FRAME_EXPIRED`; never re-execute the action to reproduce it.

**Perception boundary:** Default `hud` telemetry may report authoritative player HUD values; `pixels_only` removes gameplay telemetry and derived gameplay flags, retaining only transport, timing, renderer, control and input-context metadata. Any future privileged world/entity telemetry requires a separate explicit policy. Engine facts are not "vision detections." Image interpretation uses the client's vision model; optional server-side perception requires a configured provider, model provenance and explicit availability. No OCR or object detector is required for baseline image delivery, and no perception accuracy is claimed here.

## 8. Protocol and security

Use **stdio MCP by default**. Standard output contains only MCP traffic; send server logs to standard error and redirect child-game output separately. An optional Streamable HTTP endpoint requires authentication, Origin validation and localhost binding by default. Do not implement a new legacy HTTP+SSE-only transport. [S3]

Engine IPC is separate from MCP: a versioned, length-prefixed protocol over loopback TCP with bounded JSON messages and separately framed binary images. Authenticate with a per-instance random token passed through a private channel, never logs or visible command-line arguments. Reject unknown versions, unauthorized peers, oversized payloads and malformed fields before queuing work. Perform nonblocking I/O and bounded parsing; large image sends must not starve release/heartbeat processing.

Mutating tool annotations must reflect side effects, but annotations are not authorization. Validate policy in the server and bridge. Restrict executables, data roots, settings and commands through local configuration; do not permit arbitrary shell execution, dynamic code evaluation, unrestricted filesystem paths or remote connection commands. [S2]

Screenshots and command traces are memory-only by default. Optional recording has explicit enablement, retention and redaction. Treat game/mod text and console output as untrusted observations, never instructions that authorize broader access.

Use explicit errors including `NOT_READY`, `CONTROL_BUSY`, `STALE_STATE`, `UNSUPPORTED_CAPABILITY`, `INVALID_CONTEXT`, `ACTION_TIMEOUT`, `ACTION_INTERRUPTED`, `ENGINE_DISCONNECTED`, `FRAME_TIMEOUT`, `FRAME_EXPIRED`, `RENDER_UNAVAILABLE`, `RESULT_EXPIRED` and `POLICY_DENIED`. Tool execution failures set MCP `isError`; malformed protocol requests remain protocol errors. Include partial action progress and recovery guidance. Do not silently retry mutations.

Quake's source release and game data have different distribution terms. Keep required notices and supply an engine-appropriate license; do not bundle proprietary PAK assets. [S9]

## 9. Acceptance gates

These are tests to implement and run, not claims of tests already passed.

| Gate | Required evidence |
|---|---|
| Integration inventory | Actual fork, commit, platform, renderer, build path and equivalent hooks recorded before implementation. |
| MCP interoperability | Initialize, list tools, validate schemas, receive actual image blocks plus metadata, cancel requests and shut down cleanly in the target client. |
| Complete gameplay input | Forward/back, both strafes, run, look, attack, jump, water movement and every advertised weapon; simultaneous move/turn/fire; signs and limits verified. |
| Full UI reachability | New game, options, save/load UI, confirmation dialogs, pause/resume, death/respawn and intermission; no stuck modal loop. |
| Action/frame alignment | Scene-changing actions return frames and telemetry from the same completed state; skipped rendering cannot pass as fresh. |
| Timing | Stepped mode advances exactly the requested completed steps and stays frozen while idle; real-time bounds and actual durations are measured. |
| Failure cleanup | Inject cancellation, timeout, lost heartbeat, MCP crash, engine abort, map transition and physical takeover; no synthetic input remains held. |
| Retry/concurrency | Duplicate IDs, expired results, stale epochs, simultaneous writers and slow readers cannot replay mutations or block emergency release. |
| Image fidelity | Real renderer frames verify orientation, palette/gamma, HUD, menus, dialog overlays, resize/crop mapping, payload limits and unavailable/minimized-renderer behavior. |
| Save/lifecycle safety | Missing assets, unauthorized attachment, invalid slots, traversal, overwrite denial, failed load and owned-versus-attached shutdown are covered. |
| Vision usability | The target vision model receives frames and is exercised on visible threats, HUD, menu state and action effects; document limitations rather than infer accuracy from transport success. |
| Regression | MCP-disabled builds behave normally; existing accessibility/input components remain usable; measured capture/encoding latency and bounded memory are recorded. |

Use unit tests with a fake clock/bridge, MCP contract tests, and real-engine integration tests. Mocks do not count as proof of gameplay or vision. Keep licensed assets out of fixtures; use locally supplied assets or redistributable test content.

## 10. Delivery order and definition of done

First map the actual engine hooks and ship one vertical slice: start, acquire, perform a bounded move, receive the resulting image, release. Then complete gameplay/UI/lifecycle coverage; add stepped scheduling, watchdogs and retry safety; finally execute the acceptance matrix and document setup.

The final implementation must include a tested launch command and MCP client configuration using the real repository paths (`make build-release` -> `Quake/build-macosx/glquake`; run with `-basedir "$(CURDIR)/game"` per the root `Makefile` `run` target), a capability matrix, security defaults and recorded acceptance results. It is complete only when an MCP client can play a local session through repeated image observations and bounded actions without manual intervention, and a human can always reclaim control while the engine is responsive.

## Sources

The uploaded reorganization document is the only project-specific source supplied. The following public primary sources were reviewed on 2026-09-13. References support existing protocol/engine behavior; architecture, limits and acceptance gates above are proposed requirements.

- **S1:** Official MCP Python SDK. `https://github.com/modelcontextprotocol/python-sdk`
- **S2:** MCP tools, image content, structured results and output schemas (2025-11-25 specification). `https://modelcontextprotocol.io/specification/2025-11-25/server/tools`
- **S3:** MCP transports and transport security (2025-11-25 specification). `https://modelcontextprotocol.io/specification/2025-11-25/basic/transports`
- **S4:** Original Quake client input. `https://github.com/id-Software/Quake/blob/master/WinQuake/cl_input.c`
- **S5:** Original Quake host loop. `https://github.com/id-Software/Quake/blob/master/WinQuake/host.c`
- **S6:** Original Quake keyboard/UI dispatch. `https://github.com/id-Software/Quake/blob/master/WinQuake/keys.c`
- **S7:** Original Quake OpenGL screen composition, screenshots and modal dialogs. `https://github.com/id-Software/Quake/blob/master/WinQuake/gl_screen.c`
- **S8:** Original Quake host commands. `https://github.com/id-Software/Quake/blob/master/WinQuake/host_cmd.c`
- **S9:** Original Quake source-release notes and asset restrictions. `https://github.com/id-Software/Quake/blob/master/readme.txt`
