"""The ``%%ask`` and ``%%yukti`` cell magics.

``%%ask`` wires the notebook prefix to one Codex turn. ``%%yukti`` changes the
privileges every later ``%%ask`` runs with.

Both magics are orchestrators only. Parsing lives in ``stream``, validation in
``actions``, the transcript in ``context``, the privileges in ``settings``,
tracing in ``trace``.
"""

import html
import json
import tempfile
from typing import Any

from IPython.core.error import UsageError
from IPython.core.magic import Magics, cell_magic, line_cell_magic, magics_class
from IPython.display import HTML, Markdown, display

from .app_server import AppServer
from .comm import NotebookPrefixCache, register_prefix_comm
from .context import build_transcript
from .settings import DEFAULTS, help_text, parse_settings, summary
from .stream import Action, ActionStream
from .trace import Trace


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
                stream = ActionStream(cells)
                handles: dict[int, Any] = {}

                def apply(events) -> None:
                    for event in events:
                        if isinstance(event, Action):
                            trace.write("notebook_send", event.payload)
                            comm.send({**event.payload, "request_id": request_id})
                        elif event.block in handles:
                            handles[event.block].update(Markdown(event.text))
                        else:
                            handles[event.block] = display(
                                Markdown(event.text), display_id=True
                            )

                # A tool line is its own output below the message it follows,
                # so prose that keeps growing stays in the block above it.
                def show_tool(line: str) -> None:
                    display(Markdown(f"`{line}`"))

                try:
                    answer = server.run(
                        transcript,
                        on_delta=lambda delta: apply(stream.feed(delta)),
                        on_event=lambda event: trace.write("codex_event", event),
                        on_tool=show_tool,
                    )
                    if not stream.received_delta:
                        apply(stream.feed(answer))
                    apply(stream.finish())
                finally:
                    spinner.update(HTML(""))
                    comm.close()
                    trace.close()
                    if trace.path is not None:
                        display(Markdown(f"Trace: `{trace.path}`"))
                return None
