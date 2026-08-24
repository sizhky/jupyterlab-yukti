import json

from yukti.trace import Trace, read


def test_every_line_carries_the_clock_the_reader_needs(tmp_path):
    """Yukti's own lines had no time, so a slow turn could not be measured."""
    trace = Trace(tmp_path / "turn.jsonl")
    trace.write("input", {"question": "why?"})
    trace.write("notebook_send", {"type": "insert_cells"})
    trace.close()

    rows = [json.loads(line) for line in (tmp_path / "turn.jsonl").read_text().splitlines()]

    assert all(isinstance(row["at"], int) for row in rows)
    assert rows[1]["at"] >= rows[0]["at"]


def test_the_reader_charges_a_wait_to_the_work_that_waited(tmp_path):
    """A tool call that Yukti answered at once still sat open for 11 seconds,
    and the table must name the call, not the notification after it."""
    path = tmp_path / "turn.jsonl"
    lines = [
        {"at": 1_000, "kind": "input", "payload": {}},
        {
            "at": 2_000,
            "kind": "codex_event",
            "payload": {"method": "item/tool/call", "params": {"tool": "run_cells"}},
        },
        {"at": 2_010, "kind": "tool_result", "payload": {"line": "run 1 cell"}},
        {
            "at": 13_000,
            "kind": "codex_event",
            "payload": {"method": "thread/tokenUsage/updated"},
        },
        {
            "at": 13_000,
            "kind": "codex_event",
            "payload": {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "dynamicToolCall",
                        "tool": "run_cells",
                        "durationMs": 11_000,
                    }
                },
            },
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n")

    table = read(path)

    assert "run_cells stayed open 11.0s" in table
    assert "tokenUsage" not in table
    # 11 of the 12 seconds were a call sitting unread, and 10 ms was Yukti.
    assert "unread 11.0s, Yukti 0.0s" in table
    assert "**12.0s turn**" in table
