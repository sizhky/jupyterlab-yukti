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
from uuid import uuid4


INSERT_CELLS = "insert_cells"
REPLACE_CELLS = "replace_cells"
RUN_CELLS = "run_cells"

# A cell that asks again would open a turn inside this one, and the kernel is
# already busy with the %%ask cell that owns it.
ASKING = ("%%ask", "%%yukti")

# A tuple, not a set: containment then uses ``==`` and never hashes a value the
# model made up, so a list-valued cell_type is rejected instead of raising.
CELL_TYPES = ("code", "markdown")

INVALID = "Yukti received an invalid action"

VERBS = {INSERT_CELLS: "insert", REPLACE_CELLS: "replace", RUN_CELLS: "run"}

# Not a tool: the message that carries one run's outputs to the notebook, so
# they land in the cell that holds the source instead of in the %%ask cell.
CELL_OUTPUT = "cell_output"


def _cells_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """The one-key argument object every tool takes."""
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
    {
        "type": "function",
        "name": RUN_CELLS,
        "description": (
            "Run code cells you inserted in this turn, in the user's kernel, "
            "and read what they printed. The notebook shows the run in the "
            "cell itself. Use a cell_id an insert_cells call returned."
        ),
        "inputSchema": _cells_schema({"cell_id": {"type": "string"}}, ["cell_id"]),
    },
]


def tool_specs(run: bool) -> list[dict[str, Any]]:
    """The specs one turn receives, with or without permission to run a cell.

    Inserting a cell only writes text a reader can see; running one changes
    the kernel the notebook is holding, so it is the one cell tool a setting
    can take away.

    >>> [spec["name"] for spec in tool_specs(False)]
    ['insert_cells', 'replace_cells']
    >>> [spec["name"] for spec in tool_specs(True)][-1]
    'run_cells'
    """
    return [spec for spec in TOOL_SPECS if run or spec["name"] != RUN_CELLS]


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


def _runnable(inserted: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """The source of every code cell Yukti inserted, by cell_id."""
    return {
        cell["cell_id"]: cell["source"]
        for cell in inserted
        if cell.get("cell_type") == "code" and isinstance(cell.get("cell_id"), str)
    }


def _run(written: list, inserted: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Check the cell_ids one ``run_cells`` call names."""
    runnable = _runnable(inserted)
    ids = []
    for item in written:
        if not (
            isinstance(item, dict)
            and set(item) == {"cell_id"}
            and isinstance(item.get("cell_id"), str)
        ):
            raise RuntimeError(f"{INVALID}: each cell needs one cell_id and nothing else")
        cell_id = item["cell_id"]
        if cell_id not in runnable:
            raise RuntimeError(
                f"{INVALID}: no code cell_id {cell_id} to run; run only a code "
                "cell an insert_cells call returned in this turn"
            )
        if runnable[cell_id].lstrip().startswith(ASKING):
            raise RuntimeError(
                f"{INVALID}: a cell that starts with {' or '.join(ASKING)} "
                "cannot be run"
            )
        ids.append(cell_id)
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"{INVALID}: name each cell_id once")
    return [{"cell_id": cell_id} for cell_id in ids]


def tool_payload(
    tool: str,
    arguments: Any,
    cells: Sequence[Mapping[str, Any]],
    inserted: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Validate one tool call and return the message the notebook receives.

    ``cells`` is the notebook prefix, and ``inserted`` holds the cells Yukti
    added in this turn. ``replace_cells`` may name a cell_id from either, so a
    cell that failed its run can be rewritten and run again. ``run_cells`` may
    only name one Yukti inserted, so the agent runs what it wrote and never
    re-runs a cell of the user's own.

    Every failure raises ``RuntimeError`` with a sentence the model can act
    on, because the caller sends it back as the tool result.

    An inserted cell carries the id the notebook is going to adopt, so the
    kernel can name a cell it created without an answer from the frontend.

    >>> payload = tool_payload("insert_cells",
    ...              {"cells": [{"cell_type": "code", "source": "1"}]}, [])
    >>> payload["type"], payload["cells"][0]["source"]
    ('insert_cells', '1')
    >>> len(payload["cells"][0]["cell_id"])
    32
    >>> tool_payload("replace_cells",
    ...              {"cells": [{"cell_id": "a", "source": "1"}]}, [{"cell_id": "a"}])
    {'type': 'replace_cells', 'cells': [{'cell_id': 'a', 'source': '1'}]}
    >>> tool_payload("replace_cells",
    ...              {"cells": [{"cell_id": "gone", "source": "1"}]}, [])
    Traceback (most recent call last):
    RuntimeError: Yukti received an invalid action: no cell_id gone in the notebook
    >>> tool_payload("run_cells", {"cells": [{"cell_id": "a"}]}, [],
    ...              [{"cell_id": "a", "cell_type": "code", "source": "1"}])
    {'type': 'run_cells', 'cells': [{'cell_id': 'a'}]}
    >>> tool_payload("delete_cells", {"cells": []}, [])
    Traceback (most recent call last):
    RuntimeError: Yukti received an invalid action: no tool named delete_cells
    """
    if tool not in {INSERT_CELLS, REPLACE_CELLS, RUN_CELLS}:
        raise RuntimeError(f"{INVALID}: no tool named {tool}")
    written = _written(arguments)

    if tool == RUN_CELLS:
        return {"type": tool, "cells": _run(written, inserted)}

    if tool == INSERT_CELLS:
        if not all(_inserted_cell(cell) for cell in written):
            raise RuntimeError(
                f"{INVALID}: each cell needs a cell_type of "
                f"{' or '.join(CELL_TYPES)} and a source string"
            )
        return {
            "type": tool,
            "cells": [{**cell, "cell_id": uuid4().hex} for cell in written],
        }

    allowed = {
        cell["cell_id"]
        for cell in [*cells, *inserted]
        if isinstance(cell.get("cell_id"), str)
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
    >>> action_line({"type": "run_cells", "cells": [{}]})
    'run 1 cell'
    """
    count = len(payload["cells"])
    verb = VERBS[payload["type"]]
    return f"{verb} {count} cell{'' if count == 1 else 's'}"
