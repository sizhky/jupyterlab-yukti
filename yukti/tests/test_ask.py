import json
from unittest.mock import MagicMock, patch

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
