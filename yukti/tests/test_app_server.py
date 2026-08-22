import unittest

from yukti.app_server import AppServer


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


if __name__ == "__main__":
    unittest.main()
