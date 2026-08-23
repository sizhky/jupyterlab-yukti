"""One JSON-lines trace file per ``%%ask --trace`` turn.

``Trace.disabled()`` satisfies the same interface with no file, so the magic
carries no ``if trace_file is None`` branches. Two adapters, one real seam.

Pro: the trace format and the file lifecycle live in one module.
Con: a turn without ``--trace`` still allocates a Trace.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional


TRACE_DIR = Path.home() / ".cache" / "yukti" / "traces"


class Trace:
    """Append ``{"kind": ..., "payload": ...}`` lines, or nothing at all.

    >>> trace = Trace.disabled()
    >>> trace.write("input", {"question": "why?"})
    >>> trace.path is None
    True
    >>> trace.close()
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self._file = path.open("w", encoding="utf-8") if path is not None else None

    @classmethod
    def disabled(cls) -> "Trace":
        return cls(None)

    @classmethod
    def enabled(cls) -> "Trace":
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        return cls(TRACE_DIR / f"{time.time_ns()}.jsonl")

    def write(self, kind: str, payload: Any) -> None:
        if self._file is None:
            return
        line = json.dumps({"kind": kind, "payload": payload}, default=str)
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
