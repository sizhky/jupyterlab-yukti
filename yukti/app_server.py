import json
import os
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional


BASE_INSTRUCTIONS = (
    "You are Yukti. Answer the final [user] question using only the notebook "
    "transcript in the user message. Do not call tools. Return Markdown."
)
APP_SERVER_COMMAND = (
    "codex",
    "app-server",
    "--stdio",
    "-c",
    "project_doc_max_bytes=0",
)


class AppServer:
    def __init__(self, root: str, timeout: int = 300) -> None:
        root_path = Path(root)
        self.workdir = root_path / "work"
        self.codex_home = root_path / "codex-home"
        self.workdir.mkdir()
        self.codex_home.mkdir()
        self._link_auth_cache()

        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(self.codex_home)
        self.process = subprocess.Popen(
            APP_SERVER_COMMAND,
            cwd=self.workdir,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self.process.stdin and self.process.stdout and self.process.stderr
        self._selector = selectors.DefaultSelector()
        self._selector.register(self.process.stdout, selectors.EVENT_READ)
        self._deadline = time.monotonic() + timeout
        self._next_request_id = 0
        self.sent: list[dict[str, Any]] = []
        self.account: dict[str, Any] = {}
        self.thread_params: dict[str, Any] = {}
        self.thread_id = ""
        try:
            self._initialize()
        except Exception:
            self.close()
            raise

    def _link_auth_cache(self) -> None:
        current_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        source = current_home / "auth.json"
        if source.is_file():
            (self.codex_home / "auth.json").symlink_to(source)

    def _send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        assert self.process.stdin
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def _read(self) -> dict[str, Any]:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0 or not self._selector.select(remaining):
            raise TimeoutError("Codex App Server timed out")
        assert self.process.stdout
        line = self.process.stdout.readline()
        if line:
            return json.loads(line)
        assert self.process.stderr
        error = self.process.stderr.read().strip()
        raise RuntimeError(error or "Codex App Server stopped unexpectedly")

    def _request(self, method: str, params: Any) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._send({"method": method, "id": request_id, "params": params})
        while True:
            message = self._read()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(message["error"].get("message", str(message["error"])))
            return message.get("result", {})

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {"clientInfo": {"name": "yukti", "title": "Yukti", "version": "0.0.4"}},
        )
        self._send({"method": "initialized", "params": {}})
        account_result = self._request("account/read", {"refreshToken": False})
        self.account = account_result.get("account") or {}
        if self.account.get("type") != "chatgpt":
            raise RuntimeError(
                "Yukti requires Codex to be logged in with ChatGPT subscription authentication"
            )

        self.thread_params = {
            "ephemeral": True,
            "cwd": str(self.workdir),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "modelProvider": "openai",
            "baseInstructions": BASE_INSTRUCTIONS,
            "developerInstructions": "",
            "config": {"project_doc_max_bytes": 0},
        }
        result = self._request("thread/start", self.thread_params)
        sources = result.get("instructionSources", [])
        if sources:
            raise RuntimeError(f"Yukti rejected Codex instruction files: {sources}")
        self.thread_id = result["thread"]["id"]

    def run(
        self,
        transcript: str,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> str:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._send(
            {
                "method": "turn/start",
                "id": request_id,
                "params": {
                    "threadId": self.thread_id,
                    "input": [{"type": "text", "text": transcript}],
                },
            }
        )
        deltas: list[str] = []
        while True:
            message = self._read()
            if message.get("id") == request_id and "error" in message:
                raise RuntimeError(message["error"].get("message", str(message["error"])))
            method = message.get("method")
            params = message.get("params", {})
            if method == "item/agentMessage/delta":
                delta = params["delta"]
                deltas.append(delta)
                if on_delta is not None:
                    on_delta(delta)
            if method == "item/completed":
                item = params.get("item", {})
                if (
                    item.get("type") == "agentMessage"
                    and item.get("phase") in {None, "final_answer"}
                ):
                    return item["text"]
            if method != "turn/completed":
                continue

            turn = params["turn"]
            if turn["status"] != "completed":
                error = turn.get("error") or {}
                raise RuntimeError(error.get("message", f"Codex turn {turn['status']}"))
            if deltas:
                return "".join(deltas)
            for item in reversed(turn.get("items", [])):
                if item.get("type") == "agentMessage":
                    return item["text"]
            raise RuntimeError("Codex completed without an answer")

    def debug_details(self, transcript: str) -> dict[str, Any]:
        return {
            "command": list(APP_SERVER_COMMAND),
            "authentication": {
                "type": self.account.get("type"),
                "planType": self.account.get("planType"),
            },
            "thread/start": self.thread_params,
            "turn/start input": [{"type": "text", "text": transcript}],
        }

    def close(self) -> None:
        self._selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

    def __enter__(self) -> "AppServer":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
