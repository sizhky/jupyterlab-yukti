"""The cells of a notebook as git's index holds them, for the diff view."""

import json
import subprocess
from pathlib import Path
from typing import Any


def staged_cells(path: str, rev: str = "") -> list[dict[str, str]]:
    """Read the copy of the notebook at ``path`` that git holds at ``rev``.

    An empty ``rev`` reads the index, the staged copy. ``"HEAD"`` reads the
    last commit.

    A notebook the index does not hold yet has no staged cells, so every live
    cell reads as added. A folder outside any repository raises instead.

    >>> import tempfile
    >>> root = Path(tempfile.mkdtemp())
    >>> staged_cells(str(root / "a.ipynb"))
    Traceback (most recent call last):
    RuntimeError: fatal: not a git repository (or any of the parent directories): .git
    >>> _ = subprocess.run(["git", "init", "-q", str(root)], check=True)
    >>> staged_cells(str(root / "a.ipynb"))
    []
    >>> _ = (root / "a.ipynb").write_text(json.dumps({"cells": [
    ...     {"id": "x", "cell_type": "code", "source": ["a = 1\\n", "a"]}]}))
    >>> _ = subprocess.run(["git", "-C", str(root), "add", "a.ipynb"], check=True)
    >>> staged_cells(str(root / "a.ipynb"))
    [{'id': 'x', 'cell_type': 'code', 'source': 'a = 1\\na'}]
    >>> staged_cells(str(root / "a.ipynb"), "HEAD")
    []
    """
    file = Path(path).expanduser()
    result = subprocess.run(
        ["git", "show", f"{rev}:./{file.name}"],
        cwd=file.parent,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if "not a git repository" in result.stderr:
            raise RuntimeError(result.stderr.strip())
        return []
    return [
        {
            "id": str(cell.get("id", "")),
            "cell_type": str(cell.get("cell_type", "")),
            "source": "".join(cell.get("source", "")),
        }
        for cell in json.loads(result.stdout).get("cells", [])
    ]


def staged_json(path: str, rev: str = "") -> Any:
    """Wrap the cells at ``rev`` so a ``user_expressions`` reply carries JSON."""
    from IPython.display import JSON

    return JSON(staged_cells(path, rev))
