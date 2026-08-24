from unittest.mock import MagicMock, patch

import pytest

from IPython.core.error import UsageError
from IPython.display import Markdown

from yukti.ask import YuktiMagics


def build_magic(comm):
    magic = object.__new__(YuktiMagics)
    magic.prefixes = MagicMock()
    magic.prefixes.take.return_value = ("request-1", [], comm)
    magic.shell = MagicMock()
    magic.shell.execution_count = 7
    return magic


def sent(comm):
    """Every message the notebook received, with the ids Yukti minted."""
    return [one.args[0] for one in comm.send.call_args_list]


def test_ask_rejects_an_unknown_option():
    with pytest.raises(UsageError):
        object.__new__(YuktiMagics).ask("--verbose", "why?")


def test_the_answer_renders_when_the_turn_never_streamed():
    """A turn that only answers changes nothing in the notebook."""
    comm = MagicMock()
    magic = build_magic(comm)
    server = MagicMock()
    server.__enter__.return_value = server
    server.run.return_value = "Use `functools.cache`."

    with patch("yukti.ask.AppServer", return_value=server), patch(
        "yukti.ask.display"
    ) as shown:
        magic.ask("", "how do I memoise?")

    rendered = [
        one.args[0].data
        for one in shown.call_args_list
        if one.args and isinstance(one.args[0], Markdown)
    ]
    assert "Use `functools.cache`." in rendered
    comm.send.assert_not_called()
    comm.close.assert_called_once_with()


def test_ask_sends_each_tool_call_to_the_notebook():
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

    def run(_transcript, on_delta, on_event, on_tool, on_action):
        on_delta("The slow one first.")
        result = on_action("insert_cells", {"cells": first["cells"]})
        assert result.startswith("insert 1 cell: finished, cell_id ")
        assert comm.send.call_count == 1
        on_delta("Now the fast one.")
        on_action("insert_cells", {"cells": second["cells"]})
        return ""

    server.run.side_effect = run
    with patch("yukti.ask.AppServer", return_value=server), patch("yukti.ask.display"):
        magic.ask("", "create Fibonacci cells")

    messages = sent(comm)
    assert [message["type"] for message in messages] == ["insert_cells"] * 2
    for message, expected in zip(messages, [first, second]):
        cell = dict(message["cells"][0])
        assert cell.pop("cell_id")
        assert cell == expected["cells"][0]
        assert message["request_id"] == "request-1"
    comm.close.assert_called_once_with()


def test_a_run_reports_the_output_to_the_model_and_to_the_notebook():
    """One turn inserts a cell, runs it, and reads what it printed, so the
    reader sees the run in the cell and the model sees the same text."""
    comm = MagicMock()
    magic = build_magic(comm)
    server = MagicMock()
    server.__enter__.return_value = server
    printed = [{"output_type": "stream", "name": "stdout", "text": "55\n"}]

    def run(_transcript, on_delta, on_event, on_tool, on_action):
        on_delta("Here is the cell.")
        inserted = on_action(
            "insert_cells", {"cells": [{"cell_type": "code", "source": "print(55)"}]}
        )
        cell_id = inserted.rsplit(" ", 1)[1]
        on_delta("Now I run it.")
        result = on_action("run_cells", {"cells": [{"cell_id": cell_id}]})
        assert result == f"run 1 cell: finished\n[cell_id {cell_id} printed]\n55"
        return ""

    server.run.side_effect = run
    with patch("yukti.ask.AppServer", return_value=server), patch(
        "yukti.ask.run_source", return_value=(printed, "55")
    ) as ran, patch("yukti.ask.display"):
        magic.ask("", "print the tenth Fibonacci number")

    messages = sent(comm)
    cell_id = messages[0]["cells"][0]["cell_id"]
    assert [message["type"] for message in messages] == [
        "insert_cells",
        "run_cells",
        "cell_output",
    ]
    # The notebook hears the run before it starts, so the cell shows a busy
    # prompt, and hears the outputs after, so they land in that cell.
    assert messages[1]["cells"] == [{"cell_id": cell_id}]
    assert messages[2] == {
        "type": "cell_output",
        "cell_id": cell_id,
        "execution_count": 7,
        "outputs": printed,
        "request_id": "request-1",
    }
    assert ran.call_args.args[1] == "print(55)"


def test_a_rewritten_cell_runs_the_source_the_notebook_now_holds():
    """Insert, run, rewrite, run again: the second run must not repeat the
    source the first one failed on."""
    comm = MagicMock()
    magic = build_magic(comm)
    server = MagicMock()
    server.__enter__.return_value = server

    def run(_transcript, on_delta, on_event, on_tool, on_action):
        inserted = on_action(
            "insert_cells", {"cells": [{"cell_type": "code", "source": "print(oops)"}]}
        )
        cell_id = inserted.rsplit(" ", 1)[1]
        on_action("run_cells", {"cells": [{"cell_id": cell_id}]})
        on_action(
            "replace_cells", {"cells": [{"cell_id": cell_id, "source": "print(1)"}]}
        )
        on_action("run_cells", {"cells": [{"cell_id": cell_id}]})
        return ""

    server.run.side_effect = run
    with patch("yukti.ask.AppServer", return_value=server), patch(
        "yukti.ask.run_source", return_value=([], "1")
    ) as ran, patch("yukti.ask.display"):
        magic.ask("", "print one")

    assert [one.args[1] for one in ran.call_args_list] == ["print(oops)", "print(1)"]


def test_a_run_of_a_cell_no_insert_returned_is_refused():
    comm = MagicMock()
    magic = build_magic(comm)
    server = MagicMock()
    server.__enter__.return_value = server

    def run(_transcript, on_delta, on_event, on_tool, on_action):
        with pytest.raises(RuntimeError, match="ghost"):
            on_action("run_cells", {"cells": [{"cell_id": "ghost"}]})
        return ""

    server.run.side_effect = run
    with patch("yukti.ask.AppServer", return_value=server), patch("yukti.ask.display"):
        magic.ask("", "run the cell")

    comm.send.assert_not_called()


def test_a_refused_tool_call_never_reaches_the_notebook():
    """The App Server turns the exception into a failed tool result, so the
    model can call again with a cell_id the notebook has."""
    comm = MagicMock()
    magic = build_magic(comm)
    server = MagicMock()
    server.__enter__.return_value = server

    def run(_transcript, on_delta, on_event, on_tool, on_action):
        with pytest.raises(RuntimeError, match="gone"):
            on_action("replace_cells", {"cells": [{"cell_id": "gone", "source": "1"}]})
        return ""

    server.run.side_effect = run
    with patch("yukti.ask.AppServer", return_value=server), patch("yukti.ask.display"):
        magic.ask("", "fix the first cell")

    comm.send.assert_not_called()


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


def test_each_message_block_renders_into_its_own_output():
    """One display handle per block, so earlier prose is not overwritten."""
    comm = MagicMock()
    magic = build_magic(comm)
    server = MagicMock()
    server.__enter__.return_value = server

    def run(_transcript, on_delta, on_event, on_tool, on_action):
        on_delta("Naive recursion.")
        on_action("insert_cells", {"cells": [{"cell_type": "code", "source": "1"}]})
        on_delta("Memoised")
        on_delta(" version.")
        return ""

    server.run.side_effect = run
    outputs = []

    def show(obj, **_):
        handle = MagicMock()
        outputs.append((obj, handle))
        return handle

    with patch("yukti.ask.AppServer", return_value=server), patch(
        "yukti.ask.display", side_effect=show
    ):
        magic.ask("", "three versions")

    # The spinner is not Markdown; every remaining output is a message block
    # or the one line that reports the tool call between them.
    blocks = [(obj.data, handle) for obj, handle in outputs if isinstance(obj, Markdown)]
    assert [text for text, _ in blocks] == [
        "Naive recursion.",
        "`insert 1 cell`",
        "Memoised",
    ]

    first, _line, second = (handle for _, handle in blocks)
    first.update.assert_not_called()
    assert second.update.call_args.args[0].data == "Memoised version."
