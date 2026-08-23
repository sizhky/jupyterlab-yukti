import json

from yukti.stream import Action, ActionStream, Message


INSERT = {"type": "insert_cells", "cells": [{"cell_type": "code", "source": "1"}]}
SECOND = {"type": "insert_cells", "cells": [{"cell_type": "markdown", "source": "ok"}]}


def test_marker_ends_the_message_and_starts_the_action():
    stream = ActionStream()

    assert stream.feed("Adding a cell.\n%%action\n") == [Message("Adding a cell.")]
    assert stream.feed(json.dumps(INSERT) + "\n") == [Action(INSERT)]


def test_message_restarts_empty_after_an_action():
    stream = ActionStream()
    stream.feed("First.\n%%action\n" + json.dumps(INSERT) + "\n")

    assert stream.feed("Second.\n%%action\n") == [Message("Second.")]


def test_a_bare_json_line_is_an_action_without_the_marker():
    stream = ActionStream()
    stream.feed("Here it is.\n" + json.dumps(INSERT))

    assert stream.finish() == [Action(INSERT)]


def test_a_delta_that_is_only_json_is_an_action():
    stream = ActionStream()

    assert stream.feed(json.dumps(INSERT) + "\n") == [Action(INSERT)]


def test_a_marker_split_across_deltas_never_renders_as_markdown():
    stream = ActionStream()

    # The tail short enough to be a partial marker is held back, so only "d"
    # is visible after the first delta.
    assert stream.feed("done\n%%act") == [Message("d")]
    assert stream.feed("ion\n" + json.dumps(INSERT) + "\n") == [
        Message("done"),
        Action(INSERT),
    ]


def test_two_actions_arrive_as_two_events():
    stream = ActionStream()
    events = stream.feed(
        "One.\n%%action\n"
        + json.dumps(INSERT)
        + "\nTwo.\n%%action\n"
        + json.dumps(SECOND)
        + "\n"
    )

    assert events == [
        Message("One."),
        Action(INSERT),
        Message("Two."),
        Action(SECOND),
    ]


def test_finish_flushes_a_trailing_action():
    stream = ActionStream()
    stream.feed("Adding.\n%%action\n" + json.dumps(INSERT))

    assert stream.finish() == [Action(INSERT)]


def test_finish_flushes_the_held_back_tail_of_a_message():
    stream = ActionStream()
    stream.feed("Done.")

    assert stream.finish() == [Message("Done.")]


def test_replacements_are_checked_against_the_cells_the_notebook_sent():
    replace = {
        "type": "replace_cells",
        "cells": [{"cell_id": "abc", "source": "1"}],
    }
    stream = ActionStream([{"cell_id": "abc"}])

    assert stream.feed(json.dumps(replace) + "\n") == [Action(replace)]


def test_received_delta_reports_whether_the_turn_streamed():
    stream = ActionStream()

    assert stream.received_delta is False
    stream.feed("anything")
    assert stream.received_delta is True
