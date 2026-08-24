import json
import os
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .actions import tool_specs
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
calls without a message between them. A call is finished when it returns, so
never stream a message about waiting for a cell, or about checking whether one
arrived. After the last call, stream a short completion message. Each
insert_cells call must carry exactly one cell.
Continue until every cell the user asked for exists. For a question, answer in
Markdown and call nothing.
Use markdown cells for prose, formulas, and documentation. Use code cells for
executable source. Write inline formulas as $...$ and display formulas as $$...$$.
Use transcript cell_id values for replace_cells."""

# run_cells is what makes one turn agentic, so the rules say when to reach for
# it: the model needs the output, not the reader.
RUN_RULES = """
Every insert_cells call returns the cell_id the notebook gave the cell. Call
run_cells with that cell_id when the answer depends on what the code prints, or
when you must see that the code works. The result is what the cell printed, and
the notebook shows the same run in the cell itself, so never paste the output
into another cell and never claim an output you did not read. If the run fails,
call replace_cells with the same cell_id to fix that cell, then run it again;
never leave a broken cell behind and insert a second one next to it."""


def base_instructions(tools: bool, run: bool = False) -> str:
    """Yukti's own instruction, with or without permission to touch the disk.

    The cell tools are always there, so ``tools`` governs the shell and the
    file system only, and ``run`` governs the kernel.

    >>> base_instructions(False).splitlines()[1].endswith("do not read files.")
    True
    >>> "run commands there" in base_instructions(True)
    True
    >>> "insert_cells" in base_instructions(False)
    True
    >>> "run_cells" in base_instructions(False)
    False
    >>> "run_cells" in base_instructions(False, run=True)
    True
    """
    return PREAMBLE + (TOOLS if tools else NO_TOOLS) + ACTION_RULES + (
        RUN_RULES if run else ""
    )


# Approval requests reach Yukti only when ``%%yukti`` routes approvals away
# from ``never``. Nothing in a notebook can prompt, so Yukti accepts them and
# shows the line it accepted.
# Pro: the turn never stalls behind a prompt nobody can answer.
# Con: the acceptance is automatic, so the sandbox stays the real limit.
APPROVAL_METHODS = (
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
)


# One notification per tool item is not enough: ``item/started`` knows the
# command and ``item/completed`` knows what it printed, so the cell shows both.
ITEM_METHODS = ("item/started", "item/completed")

# A synthetic item type, because an accepted approval is a line the reader
# wants in turn order and the protocol sends it as a request, not an item.
APPROVAL = "approval"

# The whole output of one command can be a megabyte, and the notebook file
# keeps every byte a cell displays, so only the tail survives. A failure and a
# summary both live at the end of an output.
OUTPUT_TAIL = 2000

# One shell command can be longer than the cell is wide, and a wrapped summary
# costs the reader the shape of the turn. ``tool_detail`` keeps every character,
# so the summary only has to say which call this is.
SUMMARY_WIDTH = 80


def summary_line(line: str) -> str:
    """Cut one summary line to ``SUMMARY_WIDTH``, and mark what it dropped.

    Pro: every call stays one line, whatever the model ran.
    Con: two long commands that differ late read as the same summary, so the
    reader must open the block to tell them apart.

    >>> summary_line("$ pytest -q")
    '$ pytest -q'
    >>> len(summary_line("$ " + "a" * 200))
    80
    >>> summary_line("$ " + "a" * 200).endswith("…")
    True
    """
    if len(line) <= SUMMARY_WIDTH:
        return line
    return line[: SUMMARY_WIDTH - 1].rstrip() + "…"


def tool_line(item: Mapping[str, Any]) -> str:
    """Describe one tool item in a single line, or return "" for other items.

    A summary must stay one line, so a command that spans several lines keeps
    its first line, a long line is cut, and ``tool_detail`` holds the whole of
    it either way.

    >>> tool_line({"type": "commandExecution", "command": "pytest -q"})
    '$ pytest -q'
    >>> tool_line({"type": "commandExecution", "command": "cd yukti\\npytest -q"})
    '$ cd yukti …'
    >>> tool_line({"type": "commandExecution", "command": "ls " + "long/" * 40})
    '$ ls long/long/long/long/long/long/long/long/long/long/long/long/long/long/long…'
    >>> tool_line({"type": "fileChange", "changes": [{"path": "/repo/ask.py"}]})
    'edit ask.py'
    >>> tool_line({"type": "approval"})
    'approved automatically'
    >>> tool_line({"type": "agentMessage", "text": "hello"})
    ''
    """
    kind = item.get("type")
    if kind == APPROVAL:
        return "approved automatically"
    if kind == "commandExecution":
        written = str(item.get("command", "")).strip().splitlines() or [""]
        return summary_line(f"$ {written[0]}" + (" …" if len(written) > 1 else ""))
    if kind == "fileChange":
        changed = [Path(str(one.get("path", ""))).name for one in item.get("changes", [])]
        return summary_line(f"edit {', '.join(changed)}")
    return ""


def tool_detail(item: Mapping[str, Any]) -> str:
    """Render what one tool item hides behind its summary line, or "".

    A command shows what it ran, what it printed and how it ended; a file
    change shows the diff. The caller puts this text in a collapsible block,
    so a reader who only wants the prose never sees it.

    Pro: the reader can check the command that changed their files.
    Con: the notebook file grows by the output of every command.

    >>> tool_detail({"type": "commandExecution", "command": "ls",
    ...              "aggregatedOutput": "ask.py\\n", "exitCode": 0})
    'ls\\n\\nask.py\\n\\nexit 0'
    >>> tool_detail({"type": "fileChange",
    ...              "changes": [{"path": "ask.py", "diff": "+one"}]})
    'ask.py\\n+one'
    >>> tool_detail({"type": "agentMessage", "text": "hello"})
    ''
    """
    kind = item.get("type")
    if kind == "commandExecution":
        output = str(item.get("aggregatedOutput") or "").strip()
        if len(output) > OUTPUT_TAIL:
            output = "[earlier output dropped]\n" + output[-OUTPUT_TAIL:]
        code = item.get("exitCode")
        parts = [
            str(item.get("command", "")).strip(),
            output,
            "" if code is None else f"exit {code}",
        ]
        return "\n\n".join(part for part in parts if part)
    if kind == "fileChange":
        return "\n\n".join(
            f"{one.get('path', '')}\n{one.get('diff', '')}".strip()
            for one in item.get("changes", [])
        )
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

# Codex needs a moment to act on an interrupt, so the kill waits this long for
# the turn to come back aborted.
INTERRUPT_WAIT = 2.0

# One read takes whatever the pipe holds. The App Server writes several
# messages in one write, so the reader must keep what it did not use yet.
READ_BYTES = 65536


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
        # The reader takes the bytes of stdout itself and never calls readline
        # on the wrapper, because a wrapper that buffers a line the selector
        # cannot see is what made a whole turn wait. ``_pending`` is that
        # buffer, owned here.
        self._stdout = self.process.stdout.fileno()
        self._pending = b""
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._stdout, selectors.EVENT_READ)
        self._deadline = time.monotonic() + timeout
        self._next_request_id = 0
        self.sent: list[dict[str, Any]] = []
        self.account: dict[str, Any] = {}
        self.thread_params: dict[str, Any] = {}
        self.thread_id = ""
        # Set while one turn is running, so close() knows there is a turn to
        # stop and a completed turn is never interrupted.
        self.turn_id = ""
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

    def _line(self, deadline: float) -> bytes:
        """The next whole line of stdout, or ``b""`` once the process stops.

        The App Server writes ``item/started`` and ``item/tool/call`` in one
        write. A reader that selects on the pipe and then reads one line leaves
        the second message inside a buffer the selector cannot see: the pipe
        goes quiet, the reader waits, and the App Server waits for an answer to
        a request already delivered. That deadlock ended only when the App
        Server wrote something else, about eleven seconds later, and it cost
        every tool call those eleven seconds.

        So this reader keeps the bytes it did not use, and only waits on the
        pipe when it holds no whole line.

        Pro: a message that has already arrived is never waiting on the next
        one, and one call answers in microseconds.
        Con: the line lives in ``_pending``, so the reader owns a buffer that
        the standard library used to own badly.

        Raises ``TimeoutError`` when ``deadline`` passes with no whole line.
        """
        while True:
            while b"\n" not in self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._selector.select(remaining):
                    raise TimeoutError("Codex App Server timed out")
                chunk = os.read(self._stdout, READ_BYTES)
                if not chunk:
                    return b""
                self._pending += chunk
            line, _, self._pending = self._pending.partition(b"\n")
            if line.strip():
                return line

    def _read(self) -> dict[str, Any]:
        line = self._line(self._deadline)
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
            "baseInstructions": base_instructions(
                self.settings["tools"], self.settings.get("run", False)
            ),
            "developerInstructions": "",
            # The notebook tools run in this process, not in the sandbox, so
            # they stay available in every permission profile.
            "dynamicTools": tool_specs(self.settings.get("run", False)),
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
        on_tool: Optional[Callable[[Mapping[str, Any]], None]] = None,
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
            if method == "turn/started":
                self.turn_id = str(params.get("turn", {}).get("id") or "")
            if method == "item/tool/call" and "id" in message:
                self._answer_tool(message, on_action)
                continue
            if method in APPROVAL_METHODS and "id" in message:
                self._send({"id": message["id"], "result": {"decision": "acceptForSession"}})
                if on_tool is not None:
                    on_tool({"type": APPROVAL})
                continue
            if method in ITEM_METHODS and on_tool is not None:
                on_tool(params.get("item", {}))
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
                    # The answer is in hand, so nothing is left to interrupt.
                    self.turn_id = ""
                    return item["text"]
            if method != "turn/completed":
                continue

            turn = params["turn"]
            self.turn_id = ""
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

    def _interrupt_turn(self) -> None:
        """Tell Codex to stop the turn that is still running, and wait for it.

        Killing the process ends the turn too, but only once the stream drops,
        so this is the one message that says stop while the model is still
        writing. The wait is bounded, because an interrupted ``%%ask`` cell
        reaches this from a KeyboardInterrupt and the reader is already there.

        Pro: an interrupted cell stops paying for tokens it will never show.
        Con: the interrupt costs up to ``INTERRUPT_WAIT`` seconds before the
        process is killed.
        """
        if not self.turn_id or self.process.poll() is not None:
            return
        self._send(
            {
                "method": "turn/interrupt",
                "id": self._next_request_id,
                "params": {"threadId": self.thread_id, "turnId": self.turn_id},
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
                if json.loads(line).get("method") == "turn/completed":
                    return
            except json.JSONDecodeError:
                continue

    def close(self) -> None:
        # The kill must happen whatever the interrupt does, so a second Ctrl+C
        # during the wait cannot leave a Codex process behind.
        try:
            self._interrupt_turn()
        except BaseException:
            pass
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
