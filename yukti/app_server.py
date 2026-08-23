import json
import os
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .settings import APPROVAL_PARAMS, DEFAULTS


PREAMBLE = """You are Yukti. Answer the final [user] question using the notebook
transcript in the user message. """
NO_TOOLS = "Use only that transcript. Do not call tools."
TOOLS = """You may also read and change files under the working
directory and run commands there. Say in your streamed messages what you
changed on disk."""
ACTION_RULES = """

Stream non-action messages as Markdown text. Before every action, stream one short
message that explains the cell you are about to create or change. Never emit two
actions without a message between them. After the final action, stream a short
completion message. End each action message with a line containing only %%action,
then write one complete JSON action on one line using one of these shapes:
{"type":"insert_cells","cells":[{"cell_type":"code|markdown","source":"<complete source>"}]}
{"type":"replace_cells","cells":[{"cell_id":"<id>","source":"<complete source>"}]}
After the JSON line, continue streaming Markdown or start the next %%action.
Each insert_cells action must contain exactly one cell. Continue until every cell
requested by the user has been inserted. For questions, return only Markdown.
Use markdown for prose, formulas, and documentation. Use code for executable source.
Write inline formulas as $...$ and display formulas as $$...$$.
Use transcript cell_id values for replace_cells. Do not use Markdown fences."""


def base_instructions(tools: bool) -> str:
    """Yukti's own instruction, with or without permission to use tools.

    >>> base_instructions(False).splitlines()[1].endswith("Do not call tools.")
    True
    >>> "run commands there" in base_instructions(True)
    True
    """
    return PREAMBLE + (TOOLS if tools else NO_TOOLS) + ACTION_RULES


# Approval requests reach Yukti only when ``%%yukti`` routes approvals away
# from ``never``. Nothing in a notebook can prompt, so Yukti accepts them and
# shows the line it accepted.
# Pro: the turn never stalls behind a prompt nobody can answer.
# Con: the acceptance is automatic, so the sandbox stays the real limit.
APPROVAL_METHODS = (
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
)


def tool_line(item: Mapping[str, Any]) -> str:
    """Describe one tool item in a single line, or return "" for other items.

    >>> tool_line({"type": "commandExecution", "command": "pytest -q"})
    '$ pytest -q'
    >>> tool_line({"type": "fileChange", "changes": [{"path": "/repo/ask.py"}]})
    'edit ask.py'
    >>> tool_line({"type": "agentMessage", "text": "hello"})
    ''
    """
    kind = item.get("type")
    if kind == "commandExecution":
        return f"$ {str(item.get('command', '')).strip()}"
    if kind == "fileChange":
        changed = [Path(str(one.get("path", ""))).name for one in item.get("changes", [])]
        return f"edit {', '.join(changed)}"
    return ""


APP_SERVER_COMMAND = (
    "codex",
    "app-server",
    "--stdio",
    "-c",
    "project_doc_max_bytes=0",
)


class AppServer:
    def __init__(
        self,
        root: str,
        settings: Mapping[str, Any] = DEFAULTS,
        timeout: int = 300,
    ) -> None:
        root_path = Path(root)
        self.settings = dict(settings)
        self.codex_home = root_path / "codex-home"
        self.codex_home.mkdir()
        # An empty cwd setting keeps the disposable directory, so the default
        # thread still cannot see the notebook's files.
        self.workdir = root_path / "work"
        if self.settings["cwd"]:
            self.workdir = Path(self.settings["cwd"]).expanduser().resolve()
            if not self.workdir.is_dir():
                raise RuntimeError(f"Yukti cannot use cwd {self.workdir}: no directory")
        else:
            self.workdir.mkdir()
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

        roots = [
            str(Path(root).expanduser().resolve())
            for root in self.settings["writable_roots"]
        ]
        self.thread_params = {
            "ephemeral": True,
            "cwd": str(self.workdir),
            **APPROVAL_PARAMS[self.settings["approvals"]],
            "sandbox": self.settings["sandbox"],
            "modelProvider": "openai",
            "baseInstructions": base_instructions(self.settings["tools"]),
            "developerInstructions": "",
            # project_doc_max_bytes stays 0 in every mode, so an AGENTS.md in
            # the working directory never competes with Yukti's instruction.
            "config": {
                "project_doc_max_bytes": 0,
                "sandbox_workspace_write": {
                    "network_access": self.settings["network"],
                    "writable_roots": roots,
                },
            },
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
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
        on_tool: Optional[Callable[[str], None]] = None,
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
            if on_event is not None:
                on_event(message)
            if message.get("id") == request_id and "error" in message:
                raise RuntimeError(message["error"].get("message", str(message["error"])))
            method = message.get("method")
            params = message.get("params", {})
            if method in APPROVAL_METHODS and "id" in message:
                self._send({"id": message["id"], "result": {"decision": "acceptForSession"}})
                if on_tool is not None:
                    on_tool("approved automatically")
                continue
            if method == "item/started" and on_tool is not None:
                line = tool_line(params.get("item", {}))
                if line:
                    on_tool(line)
            if method == "item/agentMessage/delta":
                delta = params["delta"]
                deltas.append(delta)
                if on_delta is not None:
                    on_delta(delta)
            if method == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage" and on_delta is not None:
                    on_delta("\n")
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
            "settings": self.settings,
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
