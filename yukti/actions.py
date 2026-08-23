"""The action vocabulary Codex uses to change the notebook.

One module owns what an action is, so the validator cannot drift from its
callers. ``app_server.BASE_INSTRUCTIONS`` still spells the same shapes out in
prose for the model, and ``yukti_frontend`` re-checks them in TypeScript.
Folding those two copies into this module is a separate change.

Pro: adding an action type touches one file on the Python side.
Con: the shapes are still written twice more, in prose and in TypeScript.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence


ANSWER = "answer"
INSERT_CELLS = "insert_cells"
REPLACE_CELLS = "replace_cells"

# A tuple, not a set: containment then uses ``==`` and never hashes a value the
# model made up, so a list-valued cell_type is rejected instead of raising.
CELL_TYPES = ("code", "markdown")

INVALID = "Yukti received an invalid action"


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


def parse_action(
    answer: str, cells: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Validate one JSON action line against the cells the notebook sent.

    ``cells`` is the notebook prefix; ``replace_cells`` may only name a
    cell_id that appeared in it.

    >>> parse_action('{"type":"insert_cells","cells":'
    ...              '[{"cell_type":"code","source":"1"}]}', [])
    {'type': 'insert_cells', 'cells': [{'cell_type': 'code', 'source': '1'}]}
    >>> parse_action('{"type":"replace_cells","cells":'
    ...              '[{"cell_id":"a","source":"1"}]}', [{"cell_id": "a"}])
    {'type': 'replace_cells', 'cells': [{'cell_id': 'a', 'source': '1'}]}
    >>> parse_action('{"type":"replace_cells","cells":'
    ...              '[{"cell_id":"gone","source":"1"}]}', [])
    Traceback (most recent call last):
    RuntimeError: Yukti received an invalid action
    """
    try:
        action = json.loads(answer)
    except json.JSONDecodeError as error:
        raise RuntimeError(INVALID) from error
    if not isinstance(action, dict):
        raise RuntimeError(INVALID)

    kind = action.get("type")
    if kind == ANSWER:
        if set(action) == {"type", "source"} and isinstance(action["source"], str):
            return action
    elif kind == INSERT_CELLS:
        inserted = action.get("cells")
        if (
            set(action) == {"type", "cells"}
            and isinstance(inserted, list)
            and inserted
            and all(_inserted_cell(cell) for cell in inserted)
        ):
            return action
    elif kind == REPLACE_CELLS:
        replacements = action.get("cells")
        allowed = {
            cell["cell_id"] for cell in cells if isinstance(cell.get("cell_id"), str)
        }
        if (
            set(action) == {"type", "cells"}
            and isinstance(replacements, list)
            and replacements
            and all(_replacement(item, allowed) for item in replacements)
        ):
            ids = [item["cell_id"] for item in replacements]
            if len(ids) == len(set(ids)):
                return action
    raise RuntimeError(INVALID)
