import json

import pytest

from yukti.actions import TOOL_SPECS, action_line, tool_payload, tool_specs


CODE = {"cell_id": "new", "cell_type": "code", "source": "print(1)"}


def test_accepts_multiple_inserted_cells():
    arguments = {
        "cells": [
            {"cell_type": "markdown", "source": "Define $F_n$."},
            {"cell_type": "code", "source": "def fibonacci(n):\n    return n"},
            {"cell_type": "markdown", "source": "This takes $O(n)$ time."},
        ]
    }

    payload = tool_payload("insert_cells", arguments, [])

    minted = [dict(cell) for cell in payload["cells"]]
    ids = [cell.pop("cell_id") for cell in minted]
    assert payload["type"] == "insert_cells"
    assert minted == arguments["cells"]
    assert len(set(ids)) == 3


def test_every_inserted_cell_carries_the_id_the_notebook_adopts():
    """The frontend adopts this id, so run_cells can name the cell without an
    answer from a comm the kernel cannot read while it is busy."""
    payload = tool_payload(
        "insert_cells", {"cells": [{"cell_type": "code", "source": "1"}]}, []
    )

    cell_id = payload["cells"][0]["cell_id"]

    assert cell_id.isalnum() and len(cell_id) == 32


def test_runs_a_code_cell_yukti_inserted():
    payload = tool_payload("run_cells", {"cells": [{"cell_id": "new"}]}, [], [CODE])

    assert payload == {"type": "run_cells", "cells": [{"cell_id": "new"}]}


def test_refuses_to_run_a_cell_yukti_did_not_insert():
    """A cell of the user's own may hold anything, so the agent runs only what
    it wrote in this turn."""
    with pytest.raises(RuntimeError, match="theirs"):
        tool_payload("run_cells", {"cells": [{"cell_id": "theirs"}]}, [{"cell_id": "theirs"}], [CODE])


def test_refuses_to_run_a_markdown_cell():
    markdown = {"cell_id": "prose", "cell_type": "markdown", "source": "hello"}

    with pytest.raises(RuntimeError, match="code cell_id"):
        tool_payload("run_cells", {"cells": [{"cell_id": "prose"}]}, [], [markdown])


def test_refuses_to_run_a_cell_that_asks_again():
    """The kernel is already inside one %%ask cell, and a second turn would
    wait for a kernel nobody is going to free."""
    asking = {"cell_id": "loop", "cell_type": "code", "source": "%%ask\nwhy?"}

    with pytest.raises(RuntimeError, match="cannot be run"):
        tool_payload("run_cells", {"cells": [{"cell_id": "loop"}]}, [], [asking])


def test_refuses_to_run_one_cell_twice_in_a_call():
    arguments = {"cells": [{"cell_id": "new"}, {"cell_id": "new"}]}

    with pytest.raises(RuntimeError, match="once"):
        tool_payload("run_cells", arguments, [], [CODE])


def test_a_run_call_takes_nothing_but_a_cell_id():
    arguments = {"cells": [{"cell_id": "new", "source": "print(2)"}]}

    with pytest.raises(RuntimeError, match="nothing else"):
        tool_payload("run_cells", arguments, [], [CODE])


def test_accepts_arguments_encoded_as_a_json_string():
    """Codex sends an object today, but a string is the older function-call
    encoding and costs one line to accept."""
    arguments = json.dumps({"cells": [{"cell_type": "code", "source": "1"}]})

    assert tool_payload("insert_cells", arguments, [])["cells"][0]["source"] == "1"


def test_accepts_a_replacement_of_a_cell_the_notebook_sent():
    arguments = {"cells": [{"cell_id": "abc", "source": "1 + 1"}]}

    payload = tool_payload("replace_cells", arguments, [{"cell_id": "abc"}])

    assert payload == {"type": "replace_cells", **arguments}


def test_accepts_a_replacement_of_a_cell_yukti_inserted():
    """A cell whose run failed is rewritten under the same cell_id, so the
    reader sees one cell that works instead of two attempts."""
    arguments = {"cells": [{"cell_id": "new", "source": "print(2)"}]}

    payload = tool_payload("replace_cells", arguments, [], [CODE])

    assert payload == {"type": "replace_cells", **arguments}


def test_rejects_a_replacement_of_an_unknown_cell():
    arguments = {"cells": [{"cell_id": "gone", "source": "1"}]}

    with pytest.raises(RuntimeError, match="gone"):
        tool_payload("replace_cells", arguments, [{"cell_id": "abc"}])


def test_rejects_two_replacements_of_the_same_cell():
    arguments = {
        "cells": [
            {"cell_id": "abc", "source": "1"},
            {"cell_id": "abc", "source": "2"},
        ]
    }

    with pytest.raises(RuntimeError):
        tool_payload("replace_cells", arguments, [{"cell_id": "abc"}])


def test_rejects_an_empty_cell_list():
    with pytest.raises(RuntimeError):
        tool_payload("insert_cells", {"cells": []}, [])


def test_rejects_an_unknown_cell_type():
    arguments = {"cells": [{"cell_type": "raw", "source": "x"}]}

    with pytest.raises(RuntimeError):
        tool_payload("insert_cells", arguments, [])


def test_rejects_extra_keys_that_would_reach_the_notebook():
    arguments = {"cells": [{"cell_type": "code", "source": "1", "x": 1}]}

    with pytest.raises(RuntimeError):
        tool_payload("insert_cells", arguments, [])


def test_rejects_a_tool_yukti_does_not_have():
    with pytest.raises(RuntimeError, match="delete_cells"):
        tool_payload("delete_cells", {"cells": [{"cell_id": "a", "source": ""}]}, [])


def test_every_refusal_says_what_to_do_instead():
    """The sentence goes back to the model as the tool result, so it must name
    the problem, not only report failure."""
    with pytest.raises(RuntimeError, match="cells"):
        tool_payload("insert_cells", {"cell": []}, [])


def test_the_specs_name_the_tools_the_validator_accepts():
    names = [spec["name"] for spec in TOOL_SPECS]

    assert names == ["insert_cells", "replace_cells", "run_cells"]
    for spec in TOOL_SPECS:
        assert spec["inputSchema"]["required"] == ["cells"]
        assert spec["inputSchema"]["additionalProperties"] is False


def test_run_is_the_one_cell_tool_a_setting_takes_away():
    """Inserting a cell writes text a reader can see; running one changes the
    kernel, so it is the tool a cautious user turns off."""
    assert [spec["name"] for spec in tool_specs(False)] == [
        "insert_cells",
        "replace_cells",
    ]


def test_action_line_counts_the_cells():
    assert action_line({"type": "insert_cells", "cells": [{}]}) == "insert 1 cell"
    assert action_line({"type": "replace_cells", "cells": [{}, {}]}) == "replace 2 cells"
    assert action_line({"type": "run_cells", "cells": [{}, {}]}) == "run 2 cells"
