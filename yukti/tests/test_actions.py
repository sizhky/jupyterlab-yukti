import json

import pytest

from yukti.actions import TOOL_SPECS, action_line, tool_payload


def test_accepts_multiple_inserted_cells():
    arguments = {
        "cells": [
            {"cell_type": "markdown", "source": "Define $F_n$."},
            {"cell_type": "code", "source": "def fibonacci(n):\n    return n"},
            {"cell_type": "markdown", "source": "This takes $O(n)$ time."},
        ]
    }

    payload = tool_payload("insert_cells", arguments, [])

    assert payload == {"type": "insert_cells", **arguments}


def test_accepts_arguments_encoded_as_a_json_string():
    """Codex sends an object today, but a string is the older function-call
    encoding and costs one line to accept."""
    arguments = json.dumps({"cells": [{"cell_type": "code", "source": "1"}]})

    assert tool_payload("insert_cells", arguments, [])["cells"][0]["source"] == "1"


def test_accepts_a_replacement_of_a_cell_the_notebook_sent():
    arguments = {"cells": [{"cell_id": "abc", "source": "1 + 1"}]}

    payload = tool_payload("replace_cells", arguments, [{"cell_id": "abc"}])

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

    assert names == ["insert_cells", "replace_cells"]
    for spec in TOOL_SPECS:
        assert spec["inputSchema"]["required"] == ["cells"]
        assert spec["inputSchema"]["additionalProperties"] is False


def test_action_line_counts_the_cells():
    assert action_line({"type": "insert_cells", "cells": [{}]}) == "insert 1 cell"
    assert action_line({"type": "replace_cells", "cells": [{}, {}]}) == "replace 2 cells"
