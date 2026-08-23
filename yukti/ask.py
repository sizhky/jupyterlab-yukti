import html
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from IPython.core.error import UsageError
from IPython.core.magic import Magics, cell_magic, magics_class
from IPython.display import HTML, Markdown, display

from .app_server import AppServer
from .comm import NotebookPrefixCache, register_prefix_comm
from .context import build_transcript


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
def parse_edit(answer: str, cells: list[dict[str, Any]]) -> dict[str, Any]:
    action = json.loads(answer)
    if isinstance(action, dict) and action.get("type") == "answer":
        if set(action) == {"type", "source"} and isinstance(action["source"], str):
            return action
    if isinstance(action, dict) and action.get("type") == "insert_cells":
        inserted = action.get("cells")
        if isinstance(inserted, list) and inserted and all(
            isinstance(cell, dict)
            and set(cell) == {"cell_type", "source"}
            and cell.get("cell_type") in {"code", "markdown"}
            and isinstance(cell.get("source"), str)
            for cell in inserted
        ) and set(action) == {"type", "cells"}:
            return action
    if isinstance(action, dict) and action.get("type") == "replace_cells":
        replacements = action.get("cells")
        allowed = {
            cell["cell_id"] for cell in cells if isinstance(cell.get("cell_id"), str)
        }
        if isinstance(replacements, list) and replacements:
            ids = [replacement.get("cell_id") for replacement in replacements]
            valid = all(
                isinstance(replacement, dict)
                and set(replacement) == {"cell_id", "source"}
                and isinstance(replacement.get("cell_id"), str)
                and replacement.get("cell_id") in allowed
                and isinstance(replacement.get("source"), str)
                for replacement in replacements
            )
            if valid and len(ids) == len(set(ids)):
                return action
    raise RuntimeError("Yukti received an invalid edit action")


class DebugPayload:
    def __init__(self, details: dict[str, Any]) -> None:
        self.details = details

    def _repr_mimebundle_(self, include=None, exclude=None):
        content = json.dumps(self.details, indent=2)
        return {
            "text/plain": content,
            "text/html": f"<pre>{html.escape(content)}</pre>",
        }


@magics_class
class YuktiMagics(Magics):
    def __init__(self, shell: Any) -> None:
        super().__init__(shell)
        self.prefixes = NotebookPrefixCache()
        register_prefix_comm(shell, self.prefixes)

    @cell_magic
    def ask(self, line: str, cell: str) -> Any:
        option = line.strip()
        if option not in {"", "--debug", "--trace"}:
            raise UsageError("%%ask accepts only --debug or --trace")

        request_id, cells, comm = self.prefixes.take()
        transcript = build_transcript(cells, cell)
        trace_path = None
        trace_file = None
        if option == "--trace":
            trace_dir = Path.home() / ".cache" / "yukti" / "traces"
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_path = trace_dir / f"{time.time_ns()}.jsonl"
            trace_file = trace_path.open("w", encoding="utf-8")

        def trace(kind: str, payload: Any) -> None:
            if trace_file is None:
                return
            trace_file.write(
                json.dumps({"kind": kind, "payload": payload}, default=str) + "\n"
            )
            trace_file.flush()

        with tempfile.TemporaryDirectory(prefix="yukti-") as root:
            with AppServer(root) as server:
                if option == "--debug":
                    comm.close()
                    return DebugPayload(server.debug_details(transcript))

                trace(
                    "input",
                    {
                        "question": cell,
                        "context": cells,
                        "system_prompt": server.thread_params["baseInstructions"],
                    },
                )

                handle = display(Markdown(""), display_id=True)
                spinner = display(SPINNER, display_id=True)
                try:
                    marker = "\n%%action\n"
                    response = ""
                    pending = ""
                    in_action = False
                    received_delta = False

                    def send(answer: str) -> None:
                        action = parse_edit(answer, cells)
                        trace("notebook_send", action)
                        comm.send({**action, "request_id": request_id})

                    def on_delta(delta: str) -> None:
                        nonlocal pending, received_delta, response, in_action
                        received_delta = True
                        pending += delta
                        while True:
                            if in_action:
                                if "\n" not in pending:
                                    return
                                answer, pending = pending.split("\n", 1)
                                if answer.strip():
                                    send(answer)
                                trace("parser", {"from": "action", "to": "message"})
                                response = ""
                                in_action = False
                                continue
                            if marker in pending:
                                visible, pending = pending.split(marker, 1)
                                response += visible
                                handle.update(Markdown(response))
                                trace("parser", {"from": "message", "to": "action"})
                                in_action = True
                                continue
                            implicit_action = pending.find("\n{")
                            if pending.lstrip().startswith("{"):
                                pending = pending.lstrip()
                                trace("parser", {"from": "message", "to": "action"})
                                in_action = True
                                continue
                            if implicit_action >= 0:
                                response += pending[:implicit_action]
                                pending = pending[implicit_action + 1:]
                                handle.update(Markdown(response))
                                trace("parser", {"from": "message", "to": "action"})
                                in_action = True
                                continue
                            visible = max(0, len(pending) - len(marker) + 1)
                            response += pending[:visible]
                            pending = pending[visible:]
                            if visible:
                                handle.update(Markdown(response))
                                return
                            return

                    answer = server.run(
                        transcript,
                        on_delta=on_delta,
                        on_event=lambda event: trace("codex_event", event),
                    )
                    if not received_delta:
                        on_delta(answer)
                    if in_action and pending.strip():
                        send(pending)
                    elif not in_action:
                        response += pending
                        handle.update(Markdown(response))
                finally:
                    spinner.update(HTML(""))
                    comm.close()
                    if trace_file is not None:
                        trace_file.close()
                        display(Markdown(f"Trace: `{trace_path}`"))
                return None
