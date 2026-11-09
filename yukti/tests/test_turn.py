from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yukti import turn
from yukti.settings import DEFAULTS, parse_settings

SETTINGS = parse_settings("backend: codex", DEFAULTS)


def backend(script):
    """A Codex server whose run calls ``script`` with the turn callbacks."""
    server = MagicMock()
    server.__enter__.return_value = server
    server.usage = {"input": 3, "output": 1}
    server.thread_params = {"baseInstructions": ""}
    server.run.side_effect = lambda transcript, images, **hooks: script(hooks)
    return MagicMock(return_value=server)


def ask(script, cells=(), run=lambda cell_id, source: []):
    sent = []
    with patch.object(turn, "AppServer", backend(script)):
        turn.ask("%%ask\nwhy?", list(cells), SETTINGS, Path("/nb"), sent.append, run)
    return sent


def test_the_answer_streams_into_one_keyed_output_and_usage_comes_last():
    def script(hooks):
        hooks["on_delta"]("Use ")
        hooks["on_delta"]("`cache`.")
        return "Use `cache`."

    sent = ask(script)
    assert [m["key"] for m in sent if m["type"] == "output"] == ["m0", "m0"]
    assert sent[-2]["output"]["data"]["text/markdown"] == "Use `cache`."
    assert sent[-1]["type"] == "usage"


def test_an_inserted_cell_runs_through_the_notebook_and_its_output_reaches_the_model():
    ran, results = [], []

    def run(cell_id, source):
        ran.append((cell_id, source))
        return [{"output_type": "stream", "name": "stdout", "text": "0 1 1 0"}]

    def script(hooks):
        inserted = hooks["on_action"]("insert_cells", {"cells": [{"cell_type": "code", "source": "≠/X"}]})
        cell_id = inserted.rsplit("cell_id ", 1)[1]
        results.append(hooks["on_action"]("run_cells", {"cells": [{"cell_id": cell_id}]}))
        return "done"

    sent = ask(script, run=run)
    assert ran[0][1] == "≠/X"
    assert "0 1 1 0" in results[0]
    # The run reaches the notebook through ``run`` only, never as a message.
    assert "run_cells" not in [m["type"] for m in sent]


def test_an_unknown_option_stops_before_the_model():
    with pytest.raises(ValueError, match="--debug or --trace"):
        turn.ask("%%ask --nope\nwhy?", [], SETTINGS, Path("/nb"), print, print)
