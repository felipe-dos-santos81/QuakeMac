"""Task 7 integration: death flow, respawn and intermission, through the
MCP tools against a real engine. Skips without game data.

Observed mechanism (2026-09-14, this host/data; raw captures in
docs/engine-integration.md):

- `kill` is forwarded through the client message, so a stepped session
  queues it until a realtime window. In single player the shipped progs'
  ClientKill path calls respawn(), whose single-player branch is
  `localcmd ("restart\n")`: the level restarts (world_gen advances) and
  the player is alive again. `state.dead` is only transiently true during
  the restart sign-on, so the observable effect of the console death
  command is the restart, not a standing corpse.
- A stable dead state is the `end` map: the Rotfish kill leaves health
  <= 0 with the world running (no automatic restart). From there an
  attack act respawns (dead false, health 100) — and, in single player,
  restarts the level.
- `load_map end` does not set `intermission`: svc_intermission is written
  by the progs' execute_changelevel (a level-exit path) and no console
  command reaches it. The flag is asserted false, never faked true.
"""
import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from mcp.shared.memory import create_connected_server_and_client_session

from quakemcp.server import mcp

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
PAK = os.path.join(REPO, "game", "id1", "pak0.pak")
PORT = 29889


def _text(result):
    return "".join(getattr(c, "text", "") for c in result.content)


async def _state(s, inst):
    r = await s.call_tool("quake_state", {"instance": inst})
    assert not r.isError, _text(r)
    return json.loads(_text(r))


async def _wait(s, inst, ok, timeout):
    """Poll quake_state until ok(state); return the matching snapshot."""
    deadline = time.time() + timeout
    st = None
    while time.time() < deadline:
        st = await _state(s, inst)
        if ok(st):
            return st
        await asyncio.sleep(0.1)
    raise AssertionError("condition not reached in %.0fs: %r" % (timeout, st))


async def _mode(s, inst, mode):
    r = await s.call_tool("quake_control", {"instance": inst,
                                            "operation": "mode",
                                            "mode": mode})
    assert not r.isError, _text(r)
    return json.loads(_text(r))


async def _run():
    async with create_connected_server_and_client_session(mcp) as s:
        await s.initialize()
        r = await s.call_tool("quake_start", {"profile": "local",
                                              "port": PORT})
        assert not r.isError, _text(r)
        start = json.loads(_text(r))
        assert start["mode"] == "stepped", start
        inst = start["instance"]
        try:
            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "new_game"})
            assert not r.isError, _text(r)
            assert json.loads(_text(r))["gameplay_ready"] is True

            # (a) stepped console kill: the flush lets the forwarded
            # command land; single player answers with a level restart
            before = await _state(s, inst)
            assert before["dead"] is False and before["world_gen"] > 0
            r = await s.call_tool("quake_console", {"instance": inst,
                                                    "command": "kill"})
            assert not r.isError, _text(r)
            after = await _wait(s, inst,
                                lambda st: st["world_gen"] > before["world_gen"],
                                5.0)
            assert after["world_gen"] == before["world_gen"] + 1, after
            assert after["mode"] == "stepped", after
            alive = await _wait(s, inst,
                                lambda st: not st["dead"]
                                and st["health"] > 0, 5.0)
            assert alive["map"] == "start", alive

            # (b) stable dead state on the end map, then respawn
            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "load_map",
                                                 "map_id": "end"})
            assert not r.isError, _text(r)
            assert json.loads(_text(r))["map"] == "end"
            # the Rotfish kill needs a running clock
            await _mode(s, inst, "realtime")
            dead = await _wait(s, inst, lambda st: st["dead"], 15.0)
            assert dead["map"] == "end", dead
            assert dead["health"] <= 0, dead
            await _mode(s, inst, "stepped")

            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "respawn"})
            assert not r.isError, _text(r)
            out = json.loads(_text(r))
            assert out["respawned"] is True, out
            assert 0 <= out["waited_ms"] <= 10000, out
            alive = await _state(s, inst)
            assert alive["dead"] is False and alive["health"] > 0, alive
            assert alive["mode"] == "stepped", alive

            # (c) intermission observation: the console surface cannot
            # reach the progs' execute_changelevel, so `end` (Shub-
            # Niggurath's Pit) never sets the flag. Asserting the observed
            # negative; never faked true.
            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "list_maps"})
            assert not r.isError, _text(r)
            maps = json.loads(_text(r))["maps"]
            if "end" not in maps:
                pytest.skip("game data has no `end` map")
            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "load_map",
                                                 "map_id": "end"})
            assert not r.isError, _text(r)
            st = await _state(s, inst)
            assert st["map"] == "end", st
            assert st["intermission"] is False, st
        finally:
            r = await s.call_tool("quake_stop", {"instance": inst})
            assert not r.isError, _text(r)


def test_death_flow_tools():
    if not os.path.exists(PAK):
        pytest.skip("game data absent: %s" % PAK)
    asyncio.run(_run())
