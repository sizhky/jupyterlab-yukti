import html
import json
import tempfile
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
ACTION_REQUEST = """
[response action]
Return only one JSON action using one of these shapes:
{"type":"answer","source":"<markdown answer>"}
{"type":"insert_cells","cells":[{"cell_type":"code|markdown","source":"<complete source>"}]}
{"type":"replace_cells","cells":[{"cell_id":"<id>","source":"<complete source>"}]}
Use answer for a question, insert_cells for new notebook content, and
replace_cells for changes to earlier cells.
Use markdown for prose, formulas, and documentation. Use code for executable source.
Use only cell_id values present in the transcript. Do not use Markdown fences.
"""


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
        ):
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
        if option not in {"", "--debug"}:
            raise UsageError("%%ask accepts only --debug")

        request_id, cells, comm = self.prefixes.take()
        transcript = build_transcript(cells, cell)
        with tempfile.TemporaryDirectory(prefix="yukti-") as root:
            with AppServer(root) as server:
                if option == "--debug":
                    comm.close()
                    return DebugPayload(server.debug_details(transcript))

                handle = display(SPINNER, display_id=True)
                try:
                    action = parse_edit(server.run(transcript + ACTION_REQUEST), cells)
                    if action["type"] == "answer":
                        handle.update(Markdown(action["source"]))
                    else:
                        comm.send({**action, "request_id": request_id})
                        handle.update(Markdown(f"Updated {len(action['cells'])} cell(s)."))
                finally:
                    comm.close()
                return None
