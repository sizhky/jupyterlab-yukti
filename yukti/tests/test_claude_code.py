import unittest
from typing import Any

from yukti.claude_code import ClaudeCode
from yukti.settings import DEFAULTS


def server(messages: list[dict[str, Any]]) -> tuple[ClaudeCode, list[dict[str, Any]]]:
    """A ``ClaudeCode`` that reads ``messages`` and records what it sends."""
    code = ClaudeCode.__new__(ClaudeCode)
    code.settings = dict(DEFAULTS)
    code._next_request_id = 0
    code._on_action = None
    code._on_tool = None
    code.turn_id = ""
    sent: list[dict[str, Any]] = []
    code._send = sent.append
    stream = iter(messages)
    code._read = lambda: next(stream)
    return code, sent


INIT = {"type": "system", "subtype": "init", "tools": ["mcp__yukti__insert_cells"]}


def delta(index: int, text: str) -> dict[str, Any]:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        },
    }


class RunTest(unittest.TestCase):
    def test_streams_text_and_closes_each_block(self) -> None:
        code, _ = server(
            [
                INIT,
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text"},
                    },
                },
                delta(0, "hel"),
                delta(0, "lo"),
                {"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}},
                {"type": "result", "subtype": "success", "result": "hello"},
            ]
        )
        seen: list[str] = []

        self.assertEqual(code.run("question", on_delta=seen.append), "hello")
        self.assertEqual(seen, ["hel", "lo", "\n"])
        self.assertEqual(code.turn_id, "")

    def test_a_missing_notebook_tool_stops_the_turn(self) -> None:
        code, _ = server([{"type": "system", "subtype": "init", "tools": ["Read"]}])

        with self.assertRaisesRegex(RuntimeError, "mcp__yukti__insert_cells"):
            code.run("question")

    def test_a_failed_result_raises(self) -> None:
        code, _ = server(
            [INIT, {"type": "result", "subtype": "error_max_turns", "is_error": True}]
        )

        with self.assertRaisesRegex(RuntimeError, "error_max_turns"):
            code.run("question")

    def test_a_bash_call_shows_its_command_then_its_output(self) -> None:
        use = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}
        done = {"type": "tool_result", "tool_use_id": "t1", "content": "a.py"}
        code, _ = server(
            [
                INIT,
                {"type": "assistant", "message": {"content": [use]}},
                {"type": "user", "message": {"content": [done]}},
                {"type": "result", "subtype": "success", "result": "ok"},
            ]
        )
        items: list[Any] = []

        code.run("question", on_tool=items.append)

        self.assertEqual(items[0]["command"], "ls")
        self.assertEqual(items[1]["aggregatedOutput"], "a.py")
        self.assertEqual(items[1]["exitCode"], 0)


class ControlTest(unittest.TestCase):
    def _answer(self, code: ClaudeCode, sent: list, request: dict) -> dict:
        code._answer_control({"type": "control_request", "request_id": "r1", "request": request})
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["response"]["request_id"], "r1")
        return sent[0]["response"]

    def test_tools_list_names_the_notebook_tools(self) -> None:
        code, sent = server([])
        response = self._answer(
            code,
            sent,
            {"subtype": "mcp_message", "message": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}},
        )

        tools = response["response"]["mcp_response"]["result"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools], ["insert_cells", "replace_cells", "run_cells"]
        )

    def test_a_tool_call_runs_in_the_kernel(self) -> None:
        code, sent = server([])
        code._on_action = lambda tool, arguments: f"sent {tool}"
        call = {"name": "insert_cells", "arguments": {"cells": []}}
        response = self._answer(
            code,
            sent,
            {
                "subtype": "mcp_message",
                "message": {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": call},
            },
        )

        result = response["response"]["mcp_response"]["result"]
        self.assertEqual(result["content"][0]["text"], "sent insert_cells")
        self.assertFalse(result["isError"])

    def test_a_refused_call_is_a_result_the_model_reads(self) -> None:
        code, sent = server([])

        def refuse(tool: str, arguments: Any) -> str:
            raise RuntimeError("Yukti received an invalid action")

        code._on_action = refuse
        response = self._answer(
            code,
            sent,
            {
                "subtype": "mcp_message",
                "message": {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {}},
            },
        )

        self.assertTrue(response["response"]["mcp_response"]["result"]["isError"])

    def test_a_permission_request_is_allowed_and_shown(self) -> None:
        code, sent = server([])
        shown: list[Any] = []
        code._on_tool = shown.append
        response = self._answer(
            code,
            sent,
            {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"}},
        )

        self.assertEqual(response["response"]["behavior"], "allow")
        self.assertEqual(shown, [{"type": "approval"}])

    def test_an_unknown_request_gets_an_error(self) -> None:
        code, sent = server([])

        self.assertEqual(self._answer(code, sent, {"subtype": "hook_callback"})["subtype"], "error")


if __name__ == "__main__":
    unittest.main()
