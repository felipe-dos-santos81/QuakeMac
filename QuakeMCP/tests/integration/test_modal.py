"""Task 6 integration: a key that opens SCR_ModalMessage answers
`needs_input` with the dialog frame instead of blocking the caller; the
later answer closes the receipt. Skips without game data.

Observed menu path (pinned 2026-09-14 against the Task 6 MCP build):
after quake_game(new_game) the session is in key_game; escape raises the
main menu; enter (cursor 0) opens the Single Player menu; a second enter
(cursor 0, New Game) hits menu.c:435 and raises the confirmation modal,
because sv.active is true. The dialog text is:
    "Are you sure you want to\\nstart a new game?\\n"
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
PORT = 29888


def _text(result):
    return "".join(getattr(c, "text", "") for c in result.content)


def _body(result):
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    return json.loads(_text(result))


async def _run():
    async with create_connected_server_and_client_session(mcp) as s:
        await s.initialize()
        r = await s.call_tool("quake_start", {"profile": "local",
                                              "port": PORT})
        assert not r.isError, _text(r)
        inst = json.loads(_text(r))["instance"]
        try:
            r = await s.call_tool("quake_game", {"instance": inst,
                                                 "operation": "new_game"})
            assert not r.isError, _text(r)
            assert json.loads(_text(r))["gameplay_ready"] is True

            # key_game -> main menu
            r = await s.call_tool("quake_ui", {"instance": inst,
                                               "key": "escape"})
            assert not r.isError, _text(r)
            assert _body(await s.call_tool(
                "quake_state", {"instance": inst}))["ui"] == 3

            # main menu cursor 0 -> Single Player
            r = await s.call_tool("quake_ui", {"instance": inst,
                                               "key": "enter",
                                               "context": "menu"})
            assert not r.isError, _text(r)
            assert _body(await s.call_tool(
                "quake_state", {"instance": inst}))["ui"] == 3

            # Single Player cursor 0 -> New Game -> confirmation modal.
            # The modal pumps MCP_Poll, so the needs_input reply and the
            # dialog observation both happen while the dialog waits.
            r = await s.call_tool("quake_ui", {"instance": inst,
                                               "key": "enter",
                                               "action_id": "modal1",
                                               "context": "menu"})
            assert not r.isError, _text(r)
            res = r.structuredContent
            assert res is not None, _text(r)
            assert res.get("needs_input") is True, res
            assert "start a new game" in res.get("modal_text", ""), res
            assert res.get("action_id") == "modal1", res
            assert any(getattr(c, "type", "") == "image"
                       for c in r.content), \
                [getattr(c, "type", "") for c in r.content]
            st = _body(await s.call_tool("quake_state", {"instance": inst}))
            assert st["ui"] == 3, st

            # a duplicate retry while the receipt is pending replays the
            # needs_input body idempotently; no second dialog opens
            r2 = await s.call_tool("quake_ui", {"instance": inst,
                                                "key": "enter",
                                                "action_id": "modal1",
                                                "context": "menu"})
            assert not r2.isError, _text(r2)
            res2 = r2.structuredContent
            assert res2 is not None, _text(r2)
            assert res2.get("needs_input") is True, res2
            assert res2.get("modal_text") == res.get("modal_text"), res2

            # answer with escape: cancels the modal; the dialog wait loop
            # exits, so the receipt flips to denied (MCP_ModalClosed)
            r = await s.call_tool("quake_ui", {"instance": inst,
                                               "key": "escape",
                                               "context": "menu"})
            assert not r.isError, _text(r)

            r = await s.call_tool("quake_status", {"instance": inst,
                                                   "action_id": "modal1"})
            assert not r.isError, _text(r)
            action = _body(r)["action"]
            assert action["state"] == "denied", action
            assert action["action_id"] == "modal1", action
            # the UI answers reads and is still on the menu: no stuck modal
            st = _body(await s.call_tool("quake_state", {"instance": inst}))
            assert st["ui"] == 3, st

            # second dialog, closed by release instead of a key answer:
            # MCP_ClearControl injects escape and denies the receipt
            r = await s.call_tool("quake_ui", {"instance": inst,
                                               "key": "enter",
                                               "action_id": "modal2",
                                               "context": "menu"})
            assert not r.isError, _text(r)
            assert r.structuredContent.get("needs_input") is True, \
                _text(r)
            r = await s.call_tool("quake_release", {"instance": inst})
            assert not r.isError, _text(r)
            r = await s.call_tool("quake_status", {"instance": inst,
                                                   "action_id": "modal2"})
            assert not r.isError, _text(r)
            action = _body(r)["action"]
            assert action["state"] == "denied", action
            st = _body(await s.call_tool("quake_state", {"instance": inst}))
            assert st["ui"] == 3, st
        finally:
            r = await s.call_tool("quake_stop", {"instance": inst})
            assert not r.isError, _text(r)


def test_modal_needs_input():
    if not os.path.exists(PAK):
        pytest.skip("game data absent: %s" % PAK)
    asyncio.run(_run())
