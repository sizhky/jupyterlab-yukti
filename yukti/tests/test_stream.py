from yukti.stream import Message, MessageStream


def test_deltas_grow_one_block():
    stream = MessageStream()

    assert stream.feed("Adding ") == Message("Adding ", 0)
    assert stream.feed("a cell.") == Message("Adding a cell.", 0)


def test_a_closed_block_restarts_the_text():
    stream = MessageStream()
    stream.feed("First.")
    stream.close()

    assert stream.feed("Second.") == Message("Second.", 1)


def test_each_block_gets_its_own_index():
    """Blocks must stay distinguishable, or the caller overwrites the earlier
    prose with the later prose in one output."""
    stream = MessageStream()
    blocks = []
    for text in ("Naive recursion.", "Memoised.", "Fast doubling."):
        blocks.append(stream.feed(text))
        stream.close()

    assert [block.text for block in blocks] == [
        "Naive recursion.",
        "Memoised.",
        "Fast doubling.",
    ]
    assert [block.block for block in blocks] == [0, 1, 2]


def test_json_in_the_prose_stays_in_the_prose():
    """The old text protocol read a line starting with a brace as an action,
    so an answer that showed a dict failed the cell."""
    stream = MessageStream()

    assert stream.feed("Try:\n{'a': 1}\n").text == "Try:\n{'a': 1}\n"


def test_a_marker_in_the_prose_is_only_text():
    stream = MessageStream()

    assert stream.feed("done\n%%action\n").text == "done\n%%action\n"


def test_received_delta_reports_whether_the_turn_streamed():
    stream = MessageStream()

    assert stream.received_delta is False
    stream.feed("anything")
    assert stream.received_delta is True
