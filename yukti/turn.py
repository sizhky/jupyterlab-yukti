"""One ``%%ask`` turn and one ``%%yukti`` cell, with no kernel in the loop.

The Jupyter server runs both, so a notebook on any kernel can ask. Everything
the reader sees leaves through ``emit``; running a cell goes through ``run``,
which the frontend answers through the notebook's own kernel.
"""

from __future__ import annotations

import html
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .actions import INSERT_CELLS, RUN_CELLS, USAGE, action_line, tool_payload
from .app_server import AppServer, tool_detail, tool_line
from .claude_code import ClaudeCode
from .context import OUTPUT_LIMIT_BYTES, build_transcript, question_images, truncate
from .settings import changes, help_text, parse_settings, summary
from .stream import MessageStream
from .trace import Trace, read as read_trace

Emit = Callable[[dict[str, Any]], None]
Run = Callable[[str, str], list]

# One turn may run a cell, read the output, fix the cell and run it again, so
# the limit is loose. It exists because a model that keeps failing would
# otherwise keep running code in the user's kernel.
RUN_LIMIT = 20

# JupyterLab renders the escapes a traceback carries, so they stay in the
# output and are stripped only from the text the model reads.
ANSI = re.compile(r"\x1b\[[0-9;]*m")

EMPTY = "the cell ran and printed nothing"

ASK_OPTIONS = {"", "--debug", "--trace"}


def display(data: dict[str, str]) -> dict[str, Any]:
    """One mime bundle as an nbformat output.

    >>> display({"text/markdown": "hi"})["output_type"]
    'display_data'
    """
    return {"output_type": "display_data", "data": data, "metadata": {}}


def markdown(text: str) -> dict[str, Any]:
    """One Markdown block as an nbformat output.

    >>> markdown("hi")["data"]
    {'text/markdown': 'hi'}
    """
    return display({"text/markdown": text})


def tool_block(line: str, detail: str) -> dict[str, str]:
    """One tool call as a collapsible block, and as text for a later turn.

    JupyterLab renders the HTML, a ``details`` element that opens on demand.
    ``build_transcript`` reads the Markdown, so the next ``%%ask`` still gets
    the command and what it printed.

    >>> shown = tool_block("$ ls", "ls")
    >>> shown["text/html"].startswith("<details><summary><code>$ ls</code>")
    True
    >>> tool_block("$ ls", "")["text/markdown"]
    '`$ ls`'
    """
    shown = f"<pre>{html.escape(detail)}</pre>" if detail else ""
    fenced = f"\n\n```\n{detail}\n```" if detail else ""
    return {
        "text/markdown": f"`{line}`{fenced}",
        "text/html": f"<details><summary><code>{html.escape(line)}</code></summary>{shown}</details>",
    }


def _said(output: Mapping[str, Any]) -> str:
    """What one output says to the model. An image is named, not sent.

    >>> _said({"output_type": "stream", "name": "stdout", "text": "hi"})
    'hi'
    >>> _said({"output_type": "execute_result", "data": {"text/plain": "3"}})
    '3'
    >>> _said({"output_type": "display_data", "data": {"image/png": "iVBO"}})
    '[image/png]'
    >>> _said({"output_type": "error", "ename": "E", "evalue": "no",
    ...        "traceback": ["\\x1b[0;31mE\\x1b[0m: no"]})
    'E: no'
    """
    kind = output.get("output_type")
    if kind == "stream":
        return str(output.get("text", ""))
    if kind == "error":
        return ANSI.sub("", "\n".join(output.get("traceback", []))).strip()
    data = output.get("data", {})
    for mime in ("text/plain", "text/markdown", "text/html"):
        if mime in data:
            return str(data[mime])
    return f"[{', '.join(data) or kind}]"


def said(outputs: list) -> str:
    """What one run's nbformat outputs say to the model.

    >>> said([{"output_type": "stream", "name": "stdout", "text": "1 1 0 0"}])
    '1 1 0 0'
    >>> said([])
    'the cell ran and printed nothing'
    """
    text = "\n".join(_said(output) for output in outputs).strip()
    return truncate(text or EMPTY, OUTPUT_LIMIT_BYTES, "output")


def in_notebook_dir(settings: Mapping[str, Any], folder: Path) -> dict[str, Any]:
    """Resolve a relative ``cwd`` against the notebook, not the server.

    >>> in_notebook_dir({"cwd": "."}, Path("/nb"))["cwd"]
    '/nb'
    >>> in_notebook_dir({"cwd": ""}, Path("/nb"))["cwd"]
    ''
    >>> in_notebook_dir({"cwd": "/tmp"}, Path("/nb"))["cwd"]
    '/tmp'
    """
    cwd = settings.get("cwd") or ""
    return {**settings, "cwd": str(folder / Path(cwd).expanduser()) if cwd else ""}


def split_magic(source: str, magic: str) -> tuple[str, str]:
    """The option line and the body of a ``%%ask`` or ``%%yukti`` cell.

    >>> split_magic("%%ask --trace\\nwhy?", "%%ask")
    ('--trace', 'why?')
    >>> split_magic("  %%yukti", "%%yukti")
    ('', '')
    """
    head, _, body = source.lstrip().partition("\n")
    return head[len(magic):].strip(), body


def yukti_cell(source: str, current: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Apply one ``%%yukti`` cell: the settings after it, and what to show.

    An empty cell shows the settings in force, ``help`` the vocabulary, and a
    settings cell names only what it changed. ``--verbose`` shows the table.

    >>> from .settings import DEFAULTS
    >>> after, shown = yukti_cell("%%yukti\\npermissions: elevated", DEFAULTS)
    >>> after["sandbox"]
    'workspace-write'
    >>> yukti_cell("%yukti", after)[1] == summary(after)
    True
    >>> yukti_cell("%%yukti --nope\\nx: y", DEFAULTS)
    Traceback (most recent call last):
    ValueError: %%yukti accepts only --verbose; write settings in the cell body
    """
    magic = "%%yukti" if source.lstrip().startswith("%%") else "%yukti"
    option, body = split_magic(source, magic)
    if not body.strip():
        if option in {"", "--verbose"}:
            return dict(current), summary(current)
        if option == "help":
            return dict(current), help_text(current)
    if option not in {"", "--verbose"}:
        raise ValueError("%%yukti accepts only --verbose; write settings in the cell body")
    after = parse_settings(body, current)
    return after, summary(after) if option == "--verbose" else changes(current, after)


def ask(source: str, cells: list, settings: Mapping[str, Any], folder: Path,
        emit: Emit, run: Run) -> None:
    """Run one turn: stream the answer, apply tool calls, run named cells."""
    option, question = split_magic(source, "%%ask")
    if option not in ASK_OPTIONS:
        raise ValueError("%%ask accepts only --debug or --trace")
    images = [str(folder / path) for path in question_images(question)]
    missing = [path for path in images if not Path(path).is_file()]
    if missing:
        raise ValueError(f"%%ask cannot read the image {missing[0]}")
    transcript = build_transcript(cells, question)
    settings = in_notebook_dir(settings, folder)
    backend = ClaudeCode if settings.get("backend") == "claude" else AppServer
    held = {cell["cell_id"]: dict(cell) for cell in cells if isinstance(cell.get("cell_id"), str)}
    trace = Trace.enabled() if option == "--trace" else Trace.disabled()
    stream, runs, lines = MessageStream(), [0], [0]
    started, first_ms = time.monotonic(), []

    def show(key: str, output: dict[str, Any]) -> None:
        emit({"type": "output", "key": key, "output": output})

    def send(payload: dict[str, Any]) -> None:
        trace.write("notebook_send", payload)
        emit(payload)

    def on_delta(delta: str) -> None:
        first_ms or first_ms.append(round((time.monotonic() - started) * 1000))
        message = stream.feed(delta)
        show(f"m{message.block}", markdown(message.text))

    # One item arrives twice, started then completed, so its key updates the
    # block in place and its output lands under its own summary.
    def on_tool(item: Any) -> None:
        line = tool_line(item)
        if not line:
            return
        stream.close()
        show(f"t{item.get('id') or time.monotonic_ns()}", display(tool_block(line, tool_detail(item))))

    def on_action(tool: str, arguments: Any) -> str:
        payload = tool_payload(tool, arguments, cells, list(held.values()))
        if payload["type"] == RUN_CELLS:
            runs[0] += len(payload["cells"])
            if runs[0] > RUN_LIMIT:
                raise RuntimeError(f"Yukti runs at most {RUN_LIMIT} cells in one turn; answer with what you have")
        else:
            # A run reaches the notebook through ``run``, which waits for the outputs.
            send(payload)
        stream.close()
        line = action_line(payload)
        lines[0] += 1
        show(f"a{lines[0]}", markdown(f"`{line}`"))
        result = f"{line}: finished"
        if payload["type"] == RUN_CELLS:
            for item in payload["cells"]:
                outputs = run(item["cell_id"], held[item["cell_id"]]["source"])
                result += f"\n[cell_id {item['cell_id']} printed]\n{said(outputs)}"
        elif payload["type"] == INSERT_CELLS:
            held.update((cell["cell_id"], dict(cell)) for cell in payload["cells"])
            result += ", cell_id " + ", ".join(cell["cell_id"] for cell in payload["cells"])
        else:
            for cell in payload["cells"]:
                held[cell["cell_id"]]["source"] = cell["source"]
        trace.write("tool_result", {"line": line, "text": result})
        return result

    try:
        with tempfile.TemporaryDirectory(prefix="yukti-") as root, backend(root, settings) as server:
            if option == "--debug":
                details = json.dumps(server.debug_details(transcript, images), indent=2)
                show("debug", display({"text/plain": details, "text/html": f"<pre>{html.escape(details)}</pre>"}))
                return
            trace.write("input", {"question": question, "context": cells,
                                  "system_prompt": server.thread_params["baseInstructions"]})
            answer = server.run(transcript, images, on_delta=on_delta,
                                on_event=lambda event: trace.write("codex_event", event),
                                on_tool=on_tool, on_action=on_action)
            if not stream.received_delta:
                message = stream.feed(answer)
                show(f"m{message.block}", markdown(message.text))
            if server.usage is not None:
                send({"type": USAGE, **server.usage, "first_ms": (first_ms or [None])[0]})
    finally:
        trace.close()
        if trace.path is not None:
            show("trace", markdown(f"Trace: `{trace.path}`\n\n{read_trace(trace.path)}"))
