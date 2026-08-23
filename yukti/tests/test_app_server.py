import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from yukti.app_server import AppServer, missing_protocol, verify_protocol
from yukti.settings import DEFAULTS


class AppServerRunTest(unittest.TestCase):
    def test_returns_when_agent_message_item_completes(self) -> None:
        server = AppServer.__new__(AppServer)
        server._next_request_id = 1
        server.thread_id = "thread"
        server._send = lambda message: None
        messages = iter(
            [
                {
                    "method": "item/agentMessage/delta",
                    "params": {"delta": "complete"},
                },
                {
                    "method": "item/completed",
                    "params": {"item": {"type": "agentMessage", "text": "complete"}},
                },
                {
                    "method": "thread/status/changed",
                    "params": {"status": {"type": "idle"}},
                },
            ]
        )
        server._read = lambda: next(messages)

        self.assertEqual(server.run("question"), "complete")


class AnswerToolTest(unittest.TestCase):
    """``item/tool/call`` is a request, so every path must answer it once."""

    def _answered(self, on_action):
        server = AppServer.__new__(AppServer)
        sent = []
        server._send = sent.append
        server._answer_tool(
            {
                "id": 7,
                "method": "item/tool/call",
                "params": {"tool": "insert_cells", "arguments": {"cells": []}},
            },
            on_action,
        )
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["id"], 7)
        return sent[0]["result"]

    def test_a_applied_call_answers_with_the_line_the_notebook_got(self) -> None:
        result = self._answered(lambda tool, arguments: f"sent {tool}")

        self.assertTrue(result["success"])
        self.assertEqual(
            result["contentItems"],
            [{"type": "inputText", "text": "sent insert_cells"}],
        )

    def test_a_refused_call_answers_with_the_reason(self) -> None:
        def refuse(tool, arguments):
            raise RuntimeError("no cell_id gone in the notebook")

        result = self._answered(refuse)

        self.assertFalse(result["success"])
        self.assertEqual(
            result["contentItems"][0]["text"], "no cell_id gone in the notebook"
        )

    def test_a_turn_without_a_notebook_still_answers(self) -> None:
        result = self._answered(None)

        self.assertFalse(result["success"])
        self.assertIn("cannot change the notebook", result["contentItems"][0]["text"])


CLIENT = {
    "definitions": {
        "ThreadStartParams": {"properties": {"dynamicTools": {}}},
        "InitializeCapabilities": {"properties": {"experimentalApi": {}}},
    }
}
SERVER = {"oneOf": [{"properties": {"method": {"enum": ["item/tool/call"]}}}]}


class ProtocolTest(unittest.TestCase):
    """The App Server ignores a field it cannot read, so Yukti checks the
    names itself before it trusts them with a cell."""

    def test_a_complete_protocol_is_missing_nothing(self) -> None:
        self.assertEqual(missing_protocol(CLIENT, SERVER), [])

    def test_a_renamed_thread_field_is_named(self) -> None:
        renamed = {"definitions": dict(
            CLIENT["definitions"], ThreadStartParams={"properties": {"tools": {}}}
        )}

        self.assertEqual(missing_protocol(renamed, SERVER), ["dynamicTools"])

    def test_a_dropped_callback_is_named(self) -> None:
        other = {"oneOf": [{"properties": {"method": {"enum": ["turn/start"]}}}]}

        self.assertEqual(missing_protocol(CLIENT, other), ["item/tool/call"])

    def test_a_codex_that_lost_the_field_stops_the_turn(self) -> None:
        root = Path(tempfile.mkdtemp())
        written = root / "schema"
        written.mkdir()
        (written / "ClientRequest.json").write_text(json.dumps({"definitions": {}}))
        (written / "ServerRequest.json").write_text(json.dumps(SERVER))

        with patch("yukti.app_server.subprocess.run"):
            with self.assertRaises(RuntimeError) as raised:
                verify_protocol("codex 9.9", root, {})

        self.assertIn("dynamicTools", str(raised.exception))
        self.assertIn("insert_cells", str(raised.exception))

    def test_a_codex_that_cannot_print_its_schema_stops_the_turn(self) -> None:
        with patch("yukti.app_server.subprocess.run", side_effect=OSError("no codex")):
            with self.assertRaises(RuntimeError) as raised:
                verify_protocol("codex 9.9", Path(tempfile.mkdtemp()), {})

        self.assertIn("cannot read the Codex app-server schema", str(raised.exception))


class ThreadStartTest(unittest.TestCase):
    def test_thread_start_registers_the_notebook_tools(self) -> None:
        """Both fields are experimental: without the capability the App Server
        hides dynamicTools, and without dynamicTools Codex calls nothing."""
        server = AppServer.__new__(AppServer)
        server.settings = dict(DEFAULTS)
        server.workdir = Path(".")
        server.codex_home = Path("codex-home")
        server.environment = {}
        server._send = lambda message: None
        replies = {
            "initialize": {},
            "account/read": {"account": {"type": "chatgpt"}},
            "thread/start": {"thread": {"id": "thread"}, "instructionSources": []},
        }
        seen: dict[str, Any] = {}

        def request(method: str, params: Any) -> dict:
            seen[method] = params
            return replies[method]

        server._request = request
        # The schema check has its own tests; this one is about the params.
        with patch("yukti.app_server.verify_protocol"):
            server._initialize()

        self.assertEqual(seen["initialize"]["capabilities"], {"experimentalApi": True})
        self.assertEqual(
            [tool["name"] for tool in seen["thread/start"]["dynamicTools"]],
            ["insert_cells", "replace_cells"],
        )


if __name__ == "__main__":
    unittest.main()


class InterruptTurnTest(unittest.TestCase):
    """A turn that is still running is stopped before the process is killed."""

    def _server(self, turn_id: str, replies: list) -> Any:
        server = AppServer.__new__(AppServer)
        server._next_request_id = 3
        server.thread_id = "thread"
        server.turn_id = turn_id
        server.sent = []
        server._send = server.sent.append
        server.process = MagicMock()
        server.process.poll.return_value = None
        server.process.stdout.readline.side_effect = replies
        server._selector = MagicMock()
        server._selector.select.return_value = [object()]
        return server

    def test_an_open_turn_is_interrupted_and_awaited(self) -> None:
        server = self._server(
            "turn-1", ['{"method":"turn/completed","params":{}}\n']
        )
        server._interrupt_turn()
        self.assertEqual(
            server.sent,
            [
                {
                    "method": "turn/interrupt",
                    "id": 3,
                    "params": {"threadId": "thread", "turnId": "turn-1"},
                }
            ],
        )
        self.assertEqual(server.turn_id, "")

    def test_a_finished_turn_sends_nothing(self) -> None:
        server = self._server("", [])
        server._interrupt_turn()
        self.assertEqual(server.sent, [])

    def test_the_wait_ends_when_the_stream_closes(self) -> None:
        server = self._server("turn-1", [""])
        server._interrupt_turn()
        self.assertEqual(len(server.sent), 1)
