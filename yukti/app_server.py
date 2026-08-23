import json
import os
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .actions import TOOL_SPECS
from .settings import APPROVAL_PARAMS, DEFAULTS


PREAMBLE = """You are Yukti. Answer the final [user] question using the notebook
transcript in the user message. """
NO_TOOLS = "Use only that transcript. Do not run commands and do not read files."
TOOLS = """You may also read and change files under the working
directory and run commands there. Say in your streamed messages what you
changed on disk."""
# The insert_cells and replace_cells shapes are the tool schemas, not prose,
# so this text says only what a schema cannot: when to call, and how often.
ACTION_RULES = """

Change the notebook only by calling insert_cells and replace_cells. Never write
an action as text, and never ask the user to paste a cell.
Stream your messages as Markdown text. Before every call, stream one short
message that explains the cell you are about to create or change. Never make two
calls without a message between them. After the last call, stream a short
completion message. Each insert_cells call must carry exactly one cell.
Continue until every cell the user asked for exists. For a question, answer in
Markdown and call nothing.
Use markdown cells for prose, formulas, and documentation. Use code cells for
executable source. Write inline formulas as $...$ and display formulas as $$...$$.
Use transcript cell_id values for replace_cells."""


def base_instructions(tools: bool) -> str:
    """Yukti's own instruction, with or without permission to touch the disk.

    The cell tools are always there, so ``tools`` governs the shell and the
    file system only.

    >>> base_instructions(False).splitlines()[1].endswith("do not read files.")
    True
    >>> "run commands there" in base_instructions(True)
    True
    >>> "insert_cells" in base_instructions(False)
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

# ``dynamicTools``, ``experimentalApi`` and ``item/tool/call`` carry every cell
# Yukti writes, and all three are experimental. The App Server drops a field it
# does not know without a word, so a renamed field would cost the notebook its
# tools and still look like a working turn.
SCHEMA_COMMAND = (
    APP_SERVER_COMMAND[0],
    "app-server",
    "generate-json-schema",
    "--experimental",
    "--out",
)
NEEDED_FIELDS = (
    ("ThreadStartParams", "dynamicTools"),
    ("InitializeCapabilities", "experimentalApi"),
)
TOOL_CALL_METHOD = "item/tool/call"

# One entry per Codex version already checked in this kernel session.
_VERIFIED: set[str] = set()


def missing_protocol(client_request: Any, server_request: Any) -> list[str]:
    """Name what Yukti sends that this Codex protocol schema does not read.

    >>> missing_protocol({}, {})
    ['dynamicTools', 'experimentalApi', 'item/tool/call']
    >>> client = {"definitions": {
    ...     "ThreadStartParams": {"properties": {"dynamicTools": {}}},
    ...     "InitializeCapabilities": {"properties": {"experimentalApi": {}}}}}
    >>> server = {"oneOf": [{"properties": {"method": {"enum": ["item/tool/call"]}}}]}
    >>> missing_protocol(client, server)
    []
    """
    definitions = client_request.get("definitions", {})
    missing = [
        field
        for owner, field in NEEDED_FIELDS
        if field not in definitions.get(owner, {}).get("properties", {})
    ]
    methods = {
        method
        for request in server_request.get("oneOf", [])
        for method in request.get("properties", {}).get("method", {}).get("enum", [])
    }
    if TOOL_CALL_METHOD not in methods:
        missing.append(TOOL_CALL_METHOD)
    return missing


def verify_protocol(version: str, root: Path, environment: Mapping[str, str]) -> None:
    """Stop the turn when the installed Codex dropped a field Yukti needs.

    The schema comes from the binary that is about to run, so the check
    follows a Codex upgrade instead of trusting a pinned version number. It
    runs once per version per kernel session, and costs about a quarter of a
    second the first time.

    Pro: a renamed experimental field is one error line, not a notebook that
    quietly stops receiving cells.
    Con: a Codex that cannot print its schema stops Yukti even when the fields
    are still there.
    """
    if version in _VERIFIED:
        return
    written = root / "schema"
    try:
        subprocess.run(
            SCHEMA_COMMAND + (str(written),),
            env=dict(environment),
            capture_output=True,
            check=True,
            timeout=60,
        )
        client = json.loads((written / "ClientRequest.json").read_text())
        server = json.loads((written / "ServerRequest.json").read_text())
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Yukti cannot read the Codex app-server schema, so it cannot tell "
            f"whether {version or 'this Codex'} still accepts the notebook "
            f"tools: {error}"
        ) from None
    missing = missing_protocol(client, server)
    if missing:
        raise RuntimeError(
            f"{version or 'This Codex'} has no {', '.join(missing)}, so Yukti "
            "cannot give the model its insert_cells and replace_cells tools. "
            "Install the Codex release Yukti supports."
        )
    _VERIFIED.add(version)


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
        self.environment = environment
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
        started = self._request(
            "initialize",
            {
                "clientInfo": {"name": "yukti", "title": "Yukti", "version": "0.0.4"},
                # dynamicTools and item/tool/call are experimental, and the
                # App Server hides both until a client opts in.
                "capabilities": {"experimentalApi": True},
            },
        )
        # The reply names the Codex version, which is the key the schema check
        # remembers, so one upgrade costs one check.
        verify_protocol(
            str(started.get("userAgent", "")), self.codex_home.parent, self.environment
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
            # The notebook tools run in this process, not in the sandbox, so
            # they stay available in every permission profile.
            "dynamicTools": TOOL_SPECS,
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
        on_action: Optional[Callable[[str, Any], str]] = None,
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
            if method == "item/tool/call" and "id" in message:
                self._answer_tool(message, on_action)
                continue
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

    def _answer_tool(
        self, message: Mapping[str, Any], on_action: Optional[Callable[[str, Any], str]]
    ) -> None:
        """Run one client tool call and answer it, however it ends.

        A refused call is a result, not an exception: the model reads the
        sentence and can call again with a shape the notebook accepts.
        """
        params = message.get("params", {})
        try:
            if on_action is None:
                raise RuntimeError("Yukti cannot change the notebook in this turn")
            text, success = on_action(params.get("tool", ""), params.get("arguments")), True
        except RuntimeError as error:
            text, success = str(error), False
        self._send(
            {
                "id": message["id"],
                "result": {
                    "success": success,
                    "contentItems": [{"type": "inputText", "text": text}],
                },
            }
        )

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
