"""Task 8 integration: game lifecycle through the MCP tools against a
real engine. Skips without game data.

Exercises quake_start/status/game(save, load, lists)/stop. The mcp0.sav
artifact lives in the user game dir and is deleted in teardown.
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from mcp.shared.memory import create_connected_server_and_client_session

from quakemcp.server import mcp

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
PAK = os.path.join(REPO, "game", "id1", "pak0.pak")
SAVE = os.path.join(REPO, "game", "id1", "mcp0.sav")
PORT = 29885


def _text(result):
    return "".join(getattr(c, "text", "") for c in result.content)


async def _run():
    async with create_connected_server_and_client_session(mcp) as s:
        await s.initialize()
        r = await s.call_tool("quake_start", {"profile": "local",
                                              "port": PORT})
        assert not r.isError, _text(r)
        inst = json.loads(_text(r))["instance"]
        try:
            r = await s.call_tool("quake_status", {"instance": inst})
            assert not r.isError, _text(r)
            assert json.loads(_text(r))["bridge_ready"] is True

            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "new_game"})
            assert not r.isError, _text(r)
            game = json.loads(_text(r))
            assert game["gameplay_ready"] is True, game
            assert game["map"] == "start", game

            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "list_maps"})
            assert not r.isError and "start" in json.loads(_text(r))["maps"]

            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "save",
                                                 "slot": "mcp0"})
            assert not r.isError, _text(r)
            assert json.loads(_text(r))["saved"] is True
            assert os.path.exists(SAVE), "save file missing"

            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "list_saves"})
            assert not r.isError and "mcp0" in json.loads(_text(r))["saves"]

            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "load",
                                                 "slot": "mcp0"})
            assert not r.isError, _text(r)
            assert json.loads(_text(r))["gameplay_ready"] is True
        finally:
            r = await s.call_tool("quake_stop", {"instance": inst})
            assert not r.isError, _text(r)
            r = await s.call_tool("quake_stop", {"instance": inst})
            assert r.isError and "ENGINE_DISCONNECTED" in _text(r)


def test_lifecycle_tools():
    if not os.path.exists(PAK):
        pytest.skip("game data absent: %s" % PAK)
    if os.path.exists(SAVE):
        os.unlink(SAVE)
    try:
        asyncio.run(_run())
    finally:
        if os.path.exists(SAVE):
            os.unlink(SAVE)
