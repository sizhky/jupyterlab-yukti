import json
from unittest.mock import MagicMock, call, patch

from yukti.ask import YuktiMagics, parse_edit


def test_parse_edit_accepts_multiple_inserted_cells():
    answer = json.dumps(
        {
            "type": "insert_cells",
            "cells": [
                {"cell_type": "markdown", "source": "Define $F_n$."},
                {"cell_type": "code", "source": "def fibonacci(n):\n    return n"},
                {"cell_type": "markdown", "source": "This takes $O(n)$ time."},
            ],
        }
    )

    assert parse_edit(answer, []) == json.loads(answer)


def test_plain_ask_sends_multiple_cells_to_the_notebook():
    action = {
        "type": "insert_cells",
        "cells": [
            {"cell_type": "markdown", "source": "Define $F_n$."},
            {"cell_type": "code", "source": "def fibonacci(n):\n    return n"},
        ],
    }
    comm = MagicMock()
    magic = object.__new__(YuktiMagics)
    magic.prefixes = MagicMock()
    magic.prefixes.take.return_value = ("request-1", [], comm)
    server = MagicMock()
    server.__enter__.return_value = server
    server.run.return_value = json.dumps(action)

    with patch("yukti.ask.AppServer", return_value=server), patch("yukti.ask.display"):
        magic.ask("", "create Fibonacci cells")

    comm.send.assert_called_once_with({**action, "request_id": "request-1"})
    comm.close.assert_called_once_with()


def test_plain_ask_sends_each_completed_action_during_generation():
    plan = {"type": "answer", "source": "I will use recursion, iteration, and doubling."}
    first = {
        "type": "insert_cells",
        "cells": [{"cell_type": "markdown", "source": "Recursion: $O(2^n)$."}],
    }
    second = {
        "type": "insert_cells",
        "cells": [{"cell_type": "code", "source": "def fibonacci(n): return n"}],
    }
    comm = MagicMock()
    handle = MagicMock()
    magic = object.__new__(YuktiMagics)
    magic.prefixes = MagicMock()
    magic.prefixes.take.return_value = ("request-1", [], comm)
    server = MagicMock()
    server.__enter__.return_value = server

    def run(_transcript, on_delta):
        on_delta(json.dumps(plan) + "\n")
        assert handle.update.call_count == 1
        on_delta(json.dumps(first) + "\n")
        assert comm.send.call_count == 1
        on_delta(json.dumps(second))
        assert comm.send.call_count == 1
        return ""

    server.run.side_effect = run
    with patch("yukti.ask.AppServer", return_value=server), patch(
        "yukti.ask.display", return_value=handle
    ):
        magic.ask("", "create Fibonacci cells")

    assert comm.send.call_args_list == [
        call({**first, "request_id": "request-1"}),
        call({**second, "request_id": "request-1"}),
    ]
    comm.close.assert_called_once_with()
