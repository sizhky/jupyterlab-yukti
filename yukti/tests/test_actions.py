import json

import pytest

from yukti.actions import parse_action


def test_accepts_multiple_inserted_cells():
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

    assert parse_action(answer, []) == json.loads(answer)


def test_accepts_a_replacement_of_a_cell_the_notebook_sent():
    answer = json.dumps(
        {"type": "replace_cells", "cells": [{"cell_id": "abc", "source": "1 + 1"}]}
    )

    assert parse_action(answer, [{"cell_id": "abc"}]) == json.loads(answer)


def test_rejects_a_replacement_of_an_unknown_cell():
    answer = json.dumps(
        {"type": "replace_cells", "cells": [{"cell_id": "gone", "source": "1"}]}
    )

    with pytest.raises(RuntimeError):
        parse_action(answer, [{"cell_id": "abc"}])


def test_rejects_two_replacements_of_the_same_cell():
    answer = json.dumps(
        {
            "type": "replace_cells",
            "cells": [
                {"cell_id": "abc", "source": "1"},
                {"cell_id": "abc", "source": "2"},
            ],
        }
    )

    with pytest.raises(RuntimeError):
        parse_action(answer, [{"cell_id": "abc"}])


def test_rejects_an_empty_cell_list():
    with pytest.raises(RuntimeError):
        parse_action('{"type":"insert_cells","cells":[]}', [])


def test_rejects_an_unknown_cell_type():
    answer = '{"type":"insert_cells","cells":[{"cell_type":"raw","source":"x"}]}'

    with pytest.raises(RuntimeError):
        parse_action(answer, [])


def test_rejects_extra_keys_that_would_reach_the_notebook():
    answer = '{"type":"insert_cells","cells":[{"cell_type":"code","source":"1"}],"x":1}'

    with pytest.raises(RuntimeError):
        parse_action(answer, [])


def test_rejects_text_that_is_not_json():
    with pytest.raises(RuntimeError):
        parse_action("Here is the plan.", [])
