"""Run one ``%%ask`` turn on the user's own signed-in ``claude`` CLI.

Anthropic permits a Claude subscription only inside its own unmodified
binary: a third party may not read, store or forward the OAuth token. So this
backend starts ``claude -p`` with the user's default config directory, never
opens a credential file, and asks ``claude auth status`` whether the login is
a subscription before the turn starts.

The CLI speaks the stream-json control protocol on its stdin and stdout, the
same protocol the Agent SDK drives. An ``sdk`` MCP server makes the CLI send
every MCP message for that server back over the same pipe, so the notebook
tools still run in the kernel process, as Codex ``dynamicTools`` do.

Pro: one pipe, one reader, and the tool calls never leave the kernel.
Con: the ``sdk`` server type and ``control_request`` messages are the CLI's
internal SDK protocol, so a CLI release can change them; the ``system/init``
check turns a lost tool into an error line.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .actions import tool_specs
from .app_server import (
    APPROVAL,
    DEFAULT_MODE,
    INTERRUPT_WAIT,
    AppServer,
    base_instructions,
    workdir,
)
from .settings import DEFAULTS


# The MCP server name the CLI prefixes to every notebook tool.
SERVER = "yukti"
MCP_CONFIG = {"mcpServers": {SERVER: {"type": "sdk", "name": SERVER}}}

# ``--setting-sources=`` loads no user, project or local settings, so no
# CLAUDE.md, hook or plugin competes with Yukti's instruction, as
# ``project_doc_max_bytes=0`` does for Codex. ``--bare`` would do more, but it
# never reads the OAuth login, so the subscription would stop working.
CLAUDE_COMMAND = (
    "claude",
    "-p",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
    "--include-partial-messages",
    "--setting-sources=",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--permission-prompt-tool",
    "stdio",
)
AUTH_COMMAND = ("claude", "auth", "status", "--json")

# The CLI bills an API key or a cloud provider before the subscription, so
# these stay out of the environment the CLI starts with.
BILLING_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

# The read-only sandbox of Codex, as Claude Code tools.
READ_TOOLS = "Read,Glob,Grep"

# Claude Code writes a file with these tools, and each names its file the same way.
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")


def subscription(status: Mapping[str, Any]) -> bool:
    """Whether ``claude auth status`` reports a Claude subscription login.

    >>> subscription({"loggedIn": True, "authMethod": "claude.ai",
    ...               "apiProvider": "firstParty"})
    True
    >>> subscription({"loggedIn": True, "authMethod": "apiKey",
    ...               "apiProvider": "firstParty"})
    False
    >>> subscription({"loggedIn": False})
    False
    """
    return (
        bool(status.get("loggedIn"))
        and status.get("authMethod") == "claude.ai"
        and status.get("apiProvider") == "firstParty"
    )


def require_subscription(environment: Mapping[str, str]) -> dict[str, Any]:
    """Stop the turn unless the CLI is signed in with a Claude subscription."""
    try:
        done = subprocess.run(
            AUTH_COMMAND,
            env=dict(environment),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        status = json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Yukti cannot read the Claude Code login: {error}") from None
    if not subscription(status):
        raise RuntimeError(
            "Yukti requires Claude Code to be logged in with a Claude "
            "subscription; run `claude auth login` in a terminal"
        )
    return status


def claude_command(settings: Mapping[str, Any], instructions: str) -> list[str]:
    """The CLI call for one turn, with the privileges ``%%yukti`` set.

    No built-in tool runs without ``tools``; ``read-only`` keeps only the read
    tools; ``workspace-write`` turns on the Claude Code sandbox, which bounds
    Bash to the working directory; ``danger-full-access`` runs without it.

    >>> command = claude_command(DEFAULTS | {"model": "sonnet"}, "be Yukti")
    >>> command[command.index("--tools") + 1]
    ''
    >>> command[command.index("--allowedTools") + 1]
    'mcp__yukti__insert_cells,mcp__yukti__replace_cells,mcp__yukti__run_cells'
    >>> command = claude_command(DEFAULTS | {"model": "sonnet", "tools": True,
    ...     "sandbox": "workspace-write", "writable_roots": ["/"]}, "")
    >>> json.loads(command[command.index("--settings") + 1])["sandbox"]["enabled"]
    True
    >>> command[command.index("--disallowedTools") + 1]
    'WebFetch,WebSearch'
    >>> command[command.index("--add-dir") + 1]
    '/'
    """
    run = settings.get("run", False)
    allowed = ",".join(f"mcp__{SERVER}__{spec['name']}" for spec in tool_specs(run))
    command = [
        *CLAUDE_COMMAND,
        "--system-prompt",
        instructions,
        "--mcp-config",
        json.dumps(MCP_CONFIG),
        "--allowedTools",
        allowed,
        "--model",
        settings["model"],
        "--effort",
        settings.get("reasoning_effort", "medium"),
    ]
    if not settings["tools"]:
        return [*command, "--tools", ""]
    if settings["sandbox"] == "read-only":
        return [*command, "--tools", READ_TOOLS]
    command += ["--tools", "default"]
    if settings["sandbox"] == "workspace-write":
        sandbox = {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True}}
        command += ["--settings", json.dumps(sandbox)]
    if not settings["network"]:
        command += ["--disallowedTools", "WebFetch,WebSearch"]
    for root in settings.get("writable_roots", []):
        command += ["--add-dir", str(Path(root).expanduser().resolve())]
    return command


def tool_item(block: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """A Claude ``tool_use`` block as the Codex item ``tool_line`` reads, or None.

    The notebook already shows one Codex item shape, so this bridge keeps
    ``tool_line``, ``tool_detail`` and the ``%%ask`` display unchanged.

    >>> tool_item({"type": "tool_use", "id": "t1", "name": "Bash",
    ...            "input": {"command": "ls"}})
    {'type': 'commandExecution', 'id': 't1', 'command': 'ls'}
    >>> tool_item({"type": "tool_use", "id": "t2", "name": "Write",
    ...            "input": {"file_path": "/a/b.py", "content": "x = 1"}})
    {'type': 'fileChange', 'id': 't2', 'changes': [{'path': '/a/b.py', 'diff': 'x = 1'}]}
    >>> tool_item({"type": "tool_use", "id": "t3", "name": "mcp__yukti__insert_cells",
    ...            "input": {}}) is None
    True
    """
    name = block.get("name")
    arguments = block.get("input") or {}
    if name == "Bash":
        return {
            "type": "commandExecution",
            "id": block.get("id"),
            "command": arguments.get("command", ""),
        }
    if name in EDIT_TOOLS:
        path = arguments.get("file_path") or arguments.get("notebook_path", "")
        diff = arguments.get("new_string") or arguments.get("content") or ""
        return {
            "type": "fileChange",
            "id": block.get("id"),
            "changes": [{"path": path, "diff": diff}],
        }
    return None


def result_text(block: Mapping[str, Any]) -> str:
    """The text of one ``tool_result`` block, which is a string or a list.

    >>> result_text({"content": "done"})
    'done'
    >>> result_text({"content": [{"type": "text", "text": "a"},
    ...                          {"type": "text", "text": "b"}]})
    'a\\nb'
    """
    content = block.get("content")
    if isinstance(content, str):
        return content
    return "\n".join(
        str(part.get("text", "")) for part in content or [] if isinstance(part, dict)
    )


class ClaudeCode(AppServer):
    """The ``AppServer`` contract over ``claude -p``.

    It reuses the pipe reader, ``close`` and the context manager, and replaces
    the protocol: ``initialize``, ``run``, the tool answers and the interrupt.
    """

    NAME = "Claude Code"

    def __init__(
        self,
        root: str,
        settings: Mapping[str, Any] = DEFAULTS,
        timeout: int = 300,
    ) -> None:
        self.settings = dict(settings)
        self.workdir = workdir(Path(root), self.settings)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in BILLING_VARIABLES
        }
        self.account = require_subscription(environment)
        instructions = base_instructions(
            self.settings["tools"],
            self.settings.get("run", False),
            self.settings.get("mode", DEFAULT_MODE),
        )
        command = claude_command(self.settings, instructions)
        self.thread_params = {"baseInstructions": instructions, "command": command}
        self.thread_id = ""
        self.turn_id = ""
        self._on_action: Optional[Callable[[str, Any], str]] = None
        self._on_tool: Optional[Callable[[Mapping[str, Any]], None]] = None
        self._open(command, environment, timeout)
        try:
            self._control({"subtype": "initialize", "hooks": None})
        except Exception:
            self.close()
            raise

    def _control(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send one control request and return its response.

        The CLI asks for the MCP handshake while it starts, so a control
        request that arrives during the wait is answered here.
        """
        request_id = f"yukti_{self._next_request_id}"
        self._next_request_id += 1
        self._send(
            {"type": "control_request", "request_id": request_id, "request": request}
        )
        while True:
            message = self._read()
            if message.get("type") == "control_request":
                self._answer_control(message)
                continue
            response = message.get("response", {})
            if message.get("type") != "control_response":
                continue
            if response.get("request_id") != request_id:
                continue
            if response.get("subtype") == "error":
                raise RuntimeError(response.get("error", "Claude Code refused a request"))
            return response.get("response") or {}

    def _answer_control(self, message: Mapping[str, Any]) -> None:
        """Answer one control request from the CLI, however it ends.

        A notebook has no prompt, so a permission request is accepted and
        shown, as Codex approvals are; the tool list is the real limit.
        """
        request = message.get("request", {})
        subtype = request.get("subtype")
        if subtype == "mcp_message":
            response: dict[str, Any] = {"mcp_response": self._mcp(request.get("message", {}))}
        elif subtype == "can_use_tool":
            response = {"behavior": "allow", "updatedInput": request.get("input", {})}
            if self._on_tool is not None:
                self._on_tool({"type": APPROVAL})
        else:
            self._send(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "error",
                        "request_id": message.get("request_id"),
                        "error": f"Yukti does not handle {subtype}",
                    },
                }
            )
            return
        self._send(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": message.get("request_id"),
                    "response": response,
                },
            }
        )

    def _mcp(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Answer one JSON-RPC message for the ``yukti`` MCP server.

        A refused call is a result, not an exception: the model reads the
        sentence and can call again with a shape the notebook accepts.
        """
        method = message.get("method")
        params = message.get("params") or {}
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message.get("id")}
        if method == "initialize":
            reply["result"] = {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER, "version": "0.0.4"},
            }
        elif method == "tools/list":
            specs = tool_specs(self.settings.get("run", False))
            reply["result"] = {
                "tools": [
                    {key: spec[key] for key in ("name", "description", "inputSchema")}
                    for spec in specs
                ]
            }
        elif method == "tools/call":
            try:
                if self._on_action is None:
                    raise RuntimeError("Yukti cannot change the notebook in this turn")
                text = self._on_action(params.get("name", ""), params.get("arguments"))
                failed = False
            except RuntimeError as error:
                text, failed = str(error), True
            reply["result"] = {"content": [{"type": "text", "text": text}], "isError": failed}
        elif str(method).startswith("notifications/"):
            reply = {"jsonrpc": "2.0", "result": {}}
        else:
            reply["error"] = {"code": -32601, "message": f"Yukti has no {method}"}
        return reply

    def run(
        self,
        transcript: str,
        on_delta: Optional[Callable[[str], None]] = None,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
        on_tool: Optional[Callable[[Mapping[str, Any]], None]] = None,
        on_action: Optional[Callable[[str, Any], str]] = None,
    ) -> str:
        self._on_action = on_action
        self._on_tool = on_tool
        self._send(
            {
                "type": "user",
                "session_id": "",
                "message": {"role": "user", "content": transcript},
                "parent_tool_use_id": None,
            }
        )
        self.turn_id = "running"
        deltas: list[str] = []
        # The text blocks open now, by stream index, so the end of each one
        # closes its message block in the notebook.
        texts: set[int] = set()
        items: dict[str, dict[str, Any]] = {}
        while True:
            message = self._read()
            if on_event is not None:
                on_event(message)
            kind = message.get("type")
            if kind == "control_request":
                self._answer_control(message)
                continue
            if kind == "system" and message.get("subtype") == "init":
                self._verify_tools(message)
                continue
            # A subagent streams too; only the main thread speaks to the reader.
            if message.get("parent_tool_use_id"):
                continue
            if kind == "stream_event":
                self._stream(message.get("event", {}), texts, deltas, on_delta)
                continue
            if kind in ("assistant", "user"):
                self._items(message, items, on_tool)
                continue
            if kind != "result":
                continue
            self.turn_id = ""
            if message.get("is_error") or message.get("subtype") != "success":
                errors = message.get("errors") or [message.get("result") or message.get("subtype")]
                raise RuntimeError(f"Claude Code turn failed: {errors}")
            if deltas:
                return "".join(deltas)
            return str(message.get("result") or "")

    def _verify_tools(self, message: Mapping[str, Any]) -> None:
        """Stop the turn when the CLI did not load the notebook tools."""
        names = set(message.get("tools") or [])
        wanted = f"mcp__{SERVER}__insert_cells"
        if wanted not in names:
            raise RuntimeError(
                f"Claude Code did not load {wanted}, so Yukti cannot give the "
                "model its notebook tools. Install the Claude Code release "
                "Yukti supports."
            )

    @staticmethod
    def _stream(
        event: Mapping[str, Any],
        texts: set[int],
        deltas: list[str],
        on_delta: Optional[Callable[[str], None]],
    ) -> None:
        kind = event.get("type")
        index = event.get("index")
        if kind == "content_block_start":
            if event.get("content_block", {}).get("type") == "text":
                texts.add(index)
        elif kind == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                deltas.append(delta.get("text", ""))
                if on_delta is not None:
                    on_delta(delta.get("text", ""))
        elif kind == "content_block_stop" and index in texts:
            texts.discard(index)
            if on_delta is not None:
                on_delta("\n")

    @staticmethod
    def _items(
        message: Mapping[str, Any],
        items: dict[str, dict[str, Any]],
        on_tool: Optional[Callable[[Mapping[str, Any]], None]],
    ) -> None:
        """Show each built-in tool call once it starts and again once it ends."""
        content = message.get("message", {}).get("content")
        if on_tool is None or not isinstance(content, list):
            return
        for block in content:
            if block.get("type") == "tool_use":
                item = tool_item(block)
                if item is not None:
                    items[str(item["id"])] = item
                    on_tool(item)
            elif block.get("type") == "tool_result":
                item = items.get(str(block.get("tool_use_id")))
                if item is None or item["type"] != "commandExecution":
                    continue
                on_tool(
                    {
                        **item,
                        "aggregatedOutput": result_text(block),
                        "exitCode": 1 if block.get("is_error") else 0,
                    }
                )

    def debug_details(self, transcript: str) -> dict[str, Any]:
        return {
            "command": self.thread_params["command"],
            "settings": self.settings,
            "authentication": {
                "authMethod": self.account.get("authMethod"),
                "subscriptionType": self.account.get("subscriptionType"),
            },
            "user message": transcript,
        }

    def _interrupt_turn(self) -> None:
        """Tell the CLI to stop the running turn, and wait for its result."""
        if not self.turn_id or self.process.poll() is not None:
            return
        self._send(
            {
                "type": "control_request",
                "request_id": f"yukti_{self._next_request_id}",
                "request": {"subtype": "interrupt"},
            }
        )
        self._next_request_id += 1
        self.turn_id = ""
        deadline = time.monotonic() + INTERRUPT_WAIT
        while True:
            try:
                line = self._line(deadline)
            except TimeoutError:
                return
            if not line:
                return
            try:
                if json.loads(line).get("type") == "result":
                    return
            except json.JSONDecodeError:
                continue
