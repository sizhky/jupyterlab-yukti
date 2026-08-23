import json
from unittest.mock import MagicMock, call, patch

import pytest

from IPython.core.error import UsageError

from yukti.ask import YuktiMagics


def build_magic(comm):
    magic = object.__new__(YuktiMagics)
    magic.prefixes = MagicMock()
    magic.prefixes.take.return_value = ("request-1", [], comm)
    return magic


def test_ask_rejects_an_unknown_option():
    with pytest.raises(UsageError):
        object.__new__(YuktiMagics).ask("--verbose", "why?")


def test_ask_sends_an_action_returned_without_deltas():
    action = {
        "type": "insert_cells",
        "cells": [
            {"cell_type": "markdown", "source": "Define $F_n$."},
            {"cell_type": "code", "source": "def fibonacci(n):\n    return n"},
        ],
    }
    comm = MagicMock()
    magic = build_magic(comm)
    server = MagicMock()
    server.__enter__.return_value = server
    server.run.return_value = json.dumps(action)

    with patch("yukti.ask.AppServer", return_value=server), patch("yukti.ask.display"):
        magic.ask("", "create Fibonacci cells")

    comm.send.assert_called_once_with({**action, "request_id": "request-1"})
    comm.close.assert_called_once_with()


def test_ask_sends_each_completed_action_during_generation():
    # The frontend applies insert_cells and replace_cells only; an "answer"
    # action reaches the comm and is ignored there.
    plan = {"type": "answer", "source": "I will use recursion, then iteration."}
    first = {
        "type": "insert_cells",
        "cells": [{"cell_type": "markdown", "source": "Recursion: $O(2^n)$."}],
    }
    second = {
        "type": "insert_cells",
        "cells": [{"cell_type": "code", "source": "def fibonacci(n): return n"}],
    }
    comm = MagicMock()
    magic = build_magic(comm)
    server = MagicMock()
    server.__enter__.return_value = server

    def run(_transcript, on_delta, on_event):
        on_delta(json.dumps(plan) + "\n")
        assert comm.send.call_count == 1
        on_delta(json.dumps(first) + "\n")
        assert comm.send.call_count == 2
        # The last action has no trailing newline, so finish() flushes it.
        on_delta(json.dumps(second))
        assert comm.send.call_count == 2
        return ""

    server.run.side_effect = run
    with patch("yukti.ask.AppServer", return_value=server), patch("yukti.ask.display"):
        magic.ask("", "create Fibonacci cells")

    assert comm.send.call_args_list == [
        call({**plan, "request_id": "request-1"}),
        call({**first, "request_id": "request-1"}),
        call({**second, "request_id": "request-1"}),
    ]
    comm.close.assert_called_once_with()


def test_debug_stops_before_the_model_turn():
    comm = MagicMock()
    magic = build_magic(comm)
    server = MagicMock()
    server.__enter__.return_value = server
    server.debug_details.return_value = {"command": ["codex"]}

    with patch("yukti.ask.AppServer", return_value=server), patch("yukti.ask.display"):
        payload = magic.ask("--debug", "why?")

    assert payload.details == {"command": ["codex"]}
    server.run.assert_not_called()
    comm.close.assert_called_once_with()
