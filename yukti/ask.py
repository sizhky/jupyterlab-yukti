"""The ``%%ask`` and ``%%yukti`` cell magics.

``%%ask`` wires the notebook prefix to one Codex turn. ``%%yukti`` changes the
privileges every later ``%%ask`` runs with.

Both magics are orchestrators only. Parsing lives in ``stream``, validation in
``actions``, the transcript in ``context``, the privileges in ``settings``,
running a cell in ``execute``, tracing in ``trace``.
"""

import html
import json
import tempfile
from typing import Any

from IPython.core.error import UsageError
from IPython.core.magic import Magics, cell_magic, line_cell_magic, magics_class
from IPython.display import HTML, Markdown, display

from .actions import (
    CELL_OUTPUT,
    INSERT_CELLS,
    RUN_CELLS,
    action_line,
    tool_payload,
)
from .app_server import AppServer, tool_detail, tool_line
from .comm import NotebookPrefixCache, register_prefix_comm
from .context import build_transcript
from .execute import run_source
from .settings import DEFAULTS, help_text, parse_settings, summary
from .stream import MessageStream
from .trace import Trace, read as read_trace


# One turn may run a cell, read the output, fix the cell and run it again, so
# the limit is loose. It exists because a model that keeps failing would
# otherwise keep running code in the user's kernel.
RUN_LIMIT = 20


SPINNER = HTML(
    '<span class="yukti-spinner" role="status" aria-label="Yukti is thinking"></span>'
    "<style>"
    ".yukti-spinner{display:inline-block;width:1em;height:1em;"
    "border:2px solid currentColor;border-right-color:transparent;"
    "border-radius:50%;animation:yukti-spin .7s linear infinite}"
    "@keyframes yukti-spin{to{transform:rotate(360deg)}}"
    "@media(prefers-reduced-motion:reduce){.yukti-spinner{animation:none}}"
    "</style>"
)


class DebugPayload:
    def __init__(self, details: dict[str, Any]) -> None:
        self.details = details

    def _repr_mimebundle_(self, include=None, exclude=None):
        content = json.dumps(self.details, indent=2)
        return {
            "text/plain": content,
            "text/html": f"<pre>{html.escape(content)}</pre>",
        }


class ToolBlock:
    """One tool call as a collapsible output, and as text for a later turn.

    JupyterLab renders the richest mime type it knows, so the reader sees a
    ``details`` element that opens on demand. ``build_transcript`` reads
    ``text/markdown``, so the next ``%%ask`` cell still receives the command
    and what it printed. Both tags survive the JupyterLab sanitizer, so the
    block still collapses in a notebook the reader has not trusted.

    Pro: a long command output costs the reader one line until they open it.
    Con: one call is written twice, and both texts must say the same thing.

    >>> shown = ToolBlock("$ ls", "ls")._repr_mimebundle_()
    >>> sorted(shown)
    ['text/html', 'text/markdown']
    >>> shown["text/html"].startswith("<details><summary><code>$ ls</code>")
    True
    >>> ToolBlock("$ ls", "")._repr_mimebundle_()["text/markdown"]
    '`$ ls`'
    """

    def __init__(self, line: str, detail: str) -> None:
        self.line = line
        self.detail = detail

    def _repr_mimebundle_(self, include=None, exclude=None):
        shown = f"<pre>{html.escape(self.detail)}</pre>" if self.detail else ""
        fenced = f"\n\n```\n{self.detail}\n```" if self.detail else ""
        return {
            "text/markdown": f"`{self.line}`{fenced}",
            "text/html": (
                f"<details><summary><code>{html.escape(self.line)}</code>"
                f"</summary>{shown}</details>"
            ),
        }


def help_transform(lines: list[str]) -> list[str]:
    """Rewrite a cell that holds only ``%%yukti`` into the line magic.

    IPython refuses a cell magic with an empty body before the magic runs, so
    an empty ``%%yukti`` cell could never reach the help.

    Pro: the help appears where a reader looks for it.
    Con: one transform runs over every cell the kernel executes.

    >>> help_transform(["%%yukti\\n"])
    ['%yukti\\n']
    >>> help_transform(["%%yukti\\n", "permissions: elevated\\n"])
    ['%%yukti\\n', 'permissions: elevated\\n']
    """
    written = [line.strip() for line in lines if line.strip()]
    if written in (["%%yukti"], ["%%yukti help"]):
        return ["%yukti\n"]
    return lines


@magics_class
class YuktiMagics(Magics):
    # A class attribute, so the settings survive as the shared default and one
    # %%yukti cell rebinds them for the rest of the kernel session.
    settings = DEFAULTS

    def __init__(self, shell: Any) -> None:
        super().__init__(shell)
        self.prefixes = NotebookPrefixCache()
        register_prefix_comm(shell, self.prefixes)
        if help_transform not in shell.input_transformers_cleanup:
            shell.input_transformers_cleanup.append(help_transform)

    @line_cell_magic
    def yukti(self, line: str, cell: str = "") -> Any:
        """Change the privileges every later ``%%ask`` cell runs with.

        An empty cell, ``%yukti`` and ``%%yukti help`` all show the help
        instead, so the vocabulary is one keystroke away from the mistake.
        """
        if line.strip() in {"", "help"} and not (cell or "").strip():
            return Markdown(help_text(self.settings))
        if line.strip():
            raise UsageError("%%yukti takes no options; write settings in the cell body")
        try:
            self.settings = parse_settings(cell, self.settings)
        except ValueError as error:
            raise UsageError(str(error)) from None
        return Markdown(summary(self.settings))

    @cell_magic
    def ask(self, line: str, cell: str) -> Any:
        option = line.strip()
        if option not in {"", "--debug", "--trace"}:
            raise UsageError("%%ask accepts only --debug or --trace")

        request_id, cells, comm = self.prefixes.take()
        transcript = build_transcript(cells, cell)
        trace = Trace.enabled() if option == "--trace" else Trace.disabled()

        with tempfile.TemporaryDirectory(prefix="yukti-") as root:
            with AppServer(root, self.settings) as server:
                if option == "--debug":
                    comm.close()
                    trace.close()
                    return DebugPayload(server.debug_details(transcript))

                trace.write(
                    "input",
                    {
                        "question": cell,
                        "context": cells,
                        "system_prompt": server.thread_params["baseInstructions"],
                    },
                )

                # The spinner is created first so it stays above the message
                # blocks, which are appended as the turn streams.
                spinner = display(SPINNER, display_id=True)
                stream = MessageStream()
                handles: dict[int, Any] = {}

                def show(message) -> None:
                    if message.block in handles:
                        handles[message.block].update(Markdown(message.text))
                    else:
                        handles[message.block] = display(
                            Markdown(message.text), display_id=True
                        )

                # A tool call is its own output below the message it follows,
                # so prose that keeps growing stays in the block above it.
                def show_line(line: str) -> None:
                    display(Markdown(f"`{line}`"))

                # One item arrives twice, started then completed, so its block
                # is updated in place and the output it printed lands under the
                # summary it belongs to instead of at the end of the cell.
                tools: dict[str, Any] = {}

                def show_tool(item: Any) -> None:
                    line = tool_line(item)
                    if not line:
                        return
                    block = ToolBlock(line, tool_detail(item))
                    key = str(item.get("id") or "")
                    if key in tools:
                        tools[key].update(block)
                        return
                    stream.close()
                    handle = display(block, display_id=True)
                    if key:
                        tools[key] = handle

                def send(payload: dict[str, Any]) -> None:
                    trace.write("notebook_send", payload)
                    comm.send({**payload, "request_id": request_id})

                # The cells Yukti inserted in this turn, by the cell_id the
                # notebook adopted. The kernel mints that id, so it can name a
                # cell it created without an answer from a frontend that never
                # answers.
                inserted: dict[str, Any] = {}
                runs = 0

                def allow_run(count: int) -> None:
                    """Refuse a run past the limit before the notebook hears it.

                    The refusal happens here and not in ``run_named``, because
                    a cell that has already been told to look busy would keep
                    that prompt with no output ever arriving.
                    """
                    nonlocal runs
                    runs += count
                    if runs > RUN_LIMIT:
                        raise RuntimeError(
                            f"Yukti runs at most {RUN_LIMIT} cells in one turn; "
                            "answer with what you have"
                        )

                def run_named(named: list) -> str:
                    """Run each named cell and say what every one of them printed.

                    The notebook hears ``run_cells`` before the first run
                    starts, so the cell shows a busy prompt while the kernel is
                    inside it, and one ``cell_output`` after each run, so the
                    outputs land in the cell that holds the source.

                    The execution count is the one the ``%%ask`` cell is
                    already using, because that run is this run: nothing else
                    ran between them.

                    Pro: the reader sees the output where the code is.
                    Con: two cells then show the same count.
                    """
                    said = []
                    for item in named:
                        cell_id = item["cell_id"]
                        outputs, printed = run_source(
                            self.shell, inserted[cell_id]["source"]
                        )
                        send(
                            {
                                "type": CELL_OUTPUT,
                                "cell_id": cell_id,
                                "execution_count": self.shell.execution_count,
                                "outputs": outputs,
                            }
                        )
                        said.append(f"[cell_id {cell_id} printed]\n{printed}")
                    return "\n".join(said)

                def change_notebook(tool: str, arguments: Any) -> str:
                    """Apply one Codex tool call and say what the notebook got.

                    The frontend never answers, so the sentence the model
                    reads reports what Yukti sent, not what the notebook drew.
                    It still says ``finished``, because a result that only
                    says ``sent`` reads as pending and the model then streams
                    a message about waiting for a cell nobody can confirm.

                    An insert reports the cell_id it minted, because that id is
                    the only handle ``run_cells`` accepts.
                    """
                    payload = tool_payload(
                        tool, arguments, cells, list(inserted.values())
                    )
                    if payload["type"] == RUN_CELLS:
                        allow_run(len(payload["cells"]))
                    send(payload)
                    # The call closes the block it followed, so the next
                    # message opens an output below this line.
                    stream.close()
                    line = action_line(payload)
                    show_line(line)
                    result = f"{line}: finished"
                    if payload["type"] == RUN_CELLS:
                        result = f"{result}\n{run_named(payload['cells'])}"
                    elif payload["type"] == INSERT_CELLS:
                        for cell in payload["cells"]:
                            inserted[cell["cell_id"]] = cell
                        ids = ", ".join(cell["cell_id"] for cell in payload["cells"])
                        result = f"{result}, cell_id {ids}"
                    else:
                        # A rewritten cell keeps its cell_id, so the next run of
                        # that id must run the source the notebook now holds.
                        for cell in payload["cells"]:
                            if cell["cell_id"] in inserted:
                                inserted[cell["cell_id"]]["source"] = cell["source"]
                    # The answer closes the call, so this line is where a
                    # reader of the trace sees Yukti's own share of the turn end.
                    trace.write("tool_result", {"line": line, "text": result})
                    return result

                try:
                    answer = server.run(
                        transcript,
                        on_delta=lambda delta: show(stream.feed(delta)),
                        on_event=lambda event: trace.write("codex_event", event),
                        on_tool=show_tool,
                        on_action=change_notebook,
                    )
                    if not stream.received_delta:
                        show(stream.feed(answer))
                finally:
                    spinner.update(HTML(""))
                    comm.close()
                    trace.close()
                    if trace.path is not None:
                        display(Markdown(f"Trace: `{trace.path}`"))
                        display(Markdown(read_trace(trace.path)))
                return None
