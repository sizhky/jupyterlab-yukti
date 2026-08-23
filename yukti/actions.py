"""The tools Codex calls to change the notebook.

One module owns what an action is: the specs Codex receives in the
``dynamicTools`` field of ``thread/start``, and the validator that checks the
arguments it sends back in ``item/tool/call``. The ``inputSchema`` is the
contract, so the model no longer writes a JSON line into its prose for Yukti
to find, and prose that merely contains JSON stays prose.

``yukti_frontend`` re-checks the same shapes in TypeScript, because the
notebook must not trust the kernel either.

Pro: a malformed call is a tool error the model can read and retry.
Con: ``dynamicTools`` is an experimental App Server field, so a Codex release
can rename it; ``app_server.verify_protocol`` turns that into an error line.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence


INSERT_CELLS = "insert_cells"
REPLACE_CELLS = "replace_cells"

# A tuple, not a set: containment then uses ``==`` and never hashes a value the
# model made up, so a list-valued cell_type is rejected instead of raising.
CELL_TYPES = ("code", "markdown")

INVALID = "Yukti received an invalid action"


def _cells_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """The one-key argument object both tools take."""
    return {
        "type": "object",
        "properties": {
            "cells": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            }
        },
        "required": ["cells"],
        "additionalProperties": False,
    }


TOOL_SPECS = [
    {
        "type": "function",
        "name": INSERT_CELLS,
        "description": (
            "Insert new cells into the user's Jupyter notebook, below the "
            "cell that asked the question. Send the complete source of each "
            "cell. Use one call per cell."
        ),
        "inputSchema": _cells_schema(
            {
                "cell_type": {"type": "string", "enum": list(CELL_TYPES)},
                "source": {"type": "string"},
            },
            ["cell_type", "source"],
        ),
    },
    {
        "type": "function",
        "name": REPLACE_CELLS,
        "description": (
            "Replace the whole source of cells that already exist in the "
            "notebook. Use a cell_id from the transcript."
        ),
        "inputSchema": _cells_schema(
            {
                "cell_id": {"type": "string"},
                "source": {"type": "string"},
            },
            ["cell_id", "source"],
        ),
    },
]


def _inserted_cell(cell: Any) -> bool:
    return (
        isinstance(cell, dict)
        and set(cell) == {"cell_type", "source"}
        and cell.get("cell_type") in CELL_TYPES
        and isinstance(cell.get("source"), str)
    )


def _replacement(replacement: Any, allowed: set) -> bool:
    return (
        isinstance(replacement, dict)
        and set(replacement) == {"cell_id", "source"}
        and isinstance(replacement.get("cell_id"), str)
        and replacement["cell_id"] in allowed
        and isinstance(replacement.get("source"), str)
    )


def _written(arguments: Any) -> list:
    """Read the ``cells`` argument, however the model encoded it."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            raise RuntimeError(f"{INVALID}: the arguments are not JSON") from None
    if not isinstance(arguments, dict) or set(arguments) != {"cells"}:
        raise RuntimeError(f"{INVALID}: send exactly one argument, cells")
    cells = arguments["cells"]
    if not isinstance(cells, list) or not cells:
        raise RuntimeError(f"{INVALID}: cells must hold at least one cell")
    return cells


def tool_payload(
    tool: str, arguments: Any, cells: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Validate one tool call and return the message the notebook receives.

    ``cells`` is the notebook prefix; ``replace_cells`` may only name a
    cell_id that appeared in it. Every failure raises ``RuntimeError`` with a
    sentence the model can act on, because the caller sends it back as the
    tool result.

    >>> tool_payload("insert_cells",
    ...              {"cells": [{"cell_type": "code", "source": "1"}]}, [])
    {'type': 'insert_cells', 'cells': [{'cell_type': 'code', 'source': '1'}]}
    >>> tool_payload("replace_cells",
    ...              {"cells": [{"cell_id": "a", "source": "1"}]}, [{"cell_id": "a"}])
    {'type': 'replace_cells', 'cells': [{'cell_id': 'a', 'source': '1'}]}
    >>> tool_payload("replace_cells",
    ...              {"cells": [{"cell_id": "gone", "source": "1"}]}, [])
    Traceback (most recent call last):
    RuntimeError: Yukti received an invalid action: no cell_id gone in the notebook
    >>> tool_payload("delete_cells", {"cells": []}, [])
    Traceback (most recent call last):
    RuntimeError: Yukti received an invalid action: no tool named delete_cells
    """
    if tool not in {INSERT_CELLS, REPLACE_CELLS}:
        raise RuntimeError(f"{INVALID}: no tool named {tool}")
    written = _written(arguments)

    if tool == INSERT_CELLS:
        if not all(_inserted_cell(cell) for cell in written):
            raise RuntimeError(
                f"{INVALID}: each cell needs a cell_type of "
                f"{' or '.join(CELL_TYPES)} and a source string"
            )
        return {"type": tool, "cells": written}

    allowed = {
        cell["cell_id"] for cell in cells if isinstance(cell.get("cell_id"), str)
    }
    for replacement in written:
        if isinstance(replacement, dict) and replacement.get("cell_id") not in allowed:
            raise RuntimeError(
                f"{INVALID}: no cell_id {replacement.get('cell_id')} in the notebook"
            )
    if not all(_replacement(item, allowed) for item in written):
        raise RuntimeError(f"{INVALID}: each cell needs a cell_id and a source string")
    ids = [item["cell_id"] for item in written]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"{INVALID}: name each cell_id once")
    return {"type": tool, "cells": written}


def action_line(payload: Mapping[str, Any]) -> str:
    """Describe one applied action in a single line, for the cell and the model.

    >>> action_line({"type": "insert_cells", "cells": [{}]})
    'insert 1 cell'
    >>> action_line({"type": "replace_cells", "cells": [{}, {}]})
    'replace 2 cells'
    """
    count = len(payload["cells"])
    verb = "insert" if payload["type"] == INSERT_CELLS else "replace"
    return f"{verb} {count} cell{'' if count == 1 else 's'}"
