"""Unit tests: models validation, error enum, Observation keys."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "src"))

from quakemcp.models import (CONSOLE_COMMANDS, ERROR_CODES, KNOWN_OPS,
                             Observation, QuakeMCPError, REQUIRED_STATE_KEYS,
                             validate_line)


def test_error_enum_has_all_13_codes():
    assert set(ERROR_CODES) == {
        "NOT_READY", "CONTROL_BUSY", "STALE_STATE",
        "UNSUPPORTED_CAPABILITY", "INVALID_CONTEXT", "ACTION_TIMEOUT",
        "ACTION_INTERRUPTED", "ENGINE_DISCONNECTED", "FRAME_TIMEOUT",
        "FRAME_EXPIRED", "RENDER_UNAVAILABLE", "RESULT_EXPIRED",
        "POLICY_DENIED",
    }


def test_unknown_op_rejected():
    with pytest.raises(ValueError):
        validate_line({"v": 1, "auth": "t", "id": "1", "op": "nope"})


def test_missing_auth_rejected():
    with pytest.raises(ValueError):
        validate_line({"v": 1, "id": "1", "op": "ping"})


def test_oversize_rejected():
    with pytest.raises(ValueError):
        validate_line({"v": 1, "auth": "t", "id": "1", "op": "ping",
                       "pad": "x" * 70000})


def test_known_ops_pass():
    for op in ("ping", "exec", "cvar", "tail", "status"):
        assert validate_line({"v": 1, "auth": "t", "id": "1",
                              "op": op})["op"] == op


def test_impulse_validator_rejects_non_ascii_digits():
    # str.isdigit() accepts non-ASCII digits like "²"; the validator must
    # reject them with its frozen code instead of falling through to int()
    with pytest.raises(ValueError, match="INVALID_CONTEXT"):
        CONSOLE_COMMANDS["impulse"][1]("\u00b2")


def test_observation_requires_identity_keys():
    with pytest.raises(ValueError):
        Observation(identity={})
    with pytest.raises(ValueError):
        Observation(identity={"instance": "q1"})
    # identity complete but state group missing -> rejected (Task 6)
    with pytest.raises(ValueError):
        Observation(identity={"instance": "q1", "epoch": 1, "frame": 7})
    state = {k: 0 for k in REQUIRED_STATE_KEYS}
    state["instance"] = "q1"
    obs = Observation(identity={"instance": "q1", "epoch": 1, "frame": 7},
                      state=state)
    assert obs.identity["frame"] == 7
    del state["health"]
    with pytest.raises(ValueError):
        Observation(identity={"instance": "q1", "epoch": 1, "frame": 7},
                    state=state)


def test_tool_error_carries_code():
    e = QuakeMCPError("POLICY_DENIED", "bad auth")
    assert e.code == "POLICY_DENIED"
    with pytest.raises(ValueError):
        QuakeMCPError("NOPE", "x")
