"""One JSON-lines trace file per ``%%ask --trace`` turn, and its timing view.

``Trace.disabled()`` satisfies the same interface with no file, so the magic
carries no ``if trace_file is None`` branches. Two adapters, one real seam.

Every line carries ``at``, the wall clock in milliseconds, which is the clock a
Codex notification stamps as ``emittedAtMs``. One axis, so ``timeline`` can say
which part of a slow turn belonged to the model, to Codex, and to Yukti.

Read a trace back with::

    python -m yukti.trace ~/.cache/yukti/traces/*.jsonl

Pro: the trace format, the file lifecycle and the timing view live in one
module, so a new line kind cannot go unreadable.
Con: the view knows the App Server item shapes, so it reads a second protocol
vocabulary that ``app_server`` also owns.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


TRACE_DIR = Path.home() / ".cache" / "yukti" / "traces"

# A gap under half a second is not what a reader opens a trace for.
GAP_MS = 500


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
        stamped = {"at": int(time.time() * 1000), "kind": kind, "payload": payload}
        self._file.write(json.dumps(stamped, default=str) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def _at(row: Mapping[str, Any]) -> Optional[int]:
    """When one line happened, in wall-clock milliseconds.

    Yukti stamps every line it writes. A Codex notification also carries
    ``emittedAtMs`` on the same clock, so a trace written before the stamp
    existed still reads.

    >>> _at({"at": 5})
    5
    >>> _at({"payload": {"emittedAtMs": 7}})
    7
    >>> _at({"payload": {}}) is None
    True
    """
    payload = row.get("payload") or {}
    return row.get("at") or payload.get("emittedAtMs")


# Bookkeeping notifications: they arrive with a slow line, so naming them
# would blame the messenger for a wait that belongs to what came before.
NOISE = (
    "thread/tokenUsage/updated",
    "account/rateLimits/updated",
    "mcpServer/startupStatus/updated",
    "thread/status/changed",
)

# How much of one message the table shows before the reader has the point.
SAID_WIDTH = 64


def _label(row: Mapping[str, Any]) -> str:
    """What one line is, in a few words, or "" for a line the table skips.

    A skipped line keeps its seconds: ``timeline`` adds them to the next line
    it does name, so a wait is charged to the work that was waiting, not to
    the notification that happened to end it.

    Prose is skipped here and named by ``timeline`` instead, which has the
    deltas of the whole message in hand.

    >>> _label({"kind": "notebook_send", "payload": {"type": "insert_cells"}})
    'notebook insert_cells'
    >>> _label({"kind": "tool_result", "payload": {"line": "insert 1 cell"}})
    'answered insert 1 cell'
    >>> _label({"kind": "codex_event", "payload": {"method": "item/completed",
    ...         "params": {"item": {"type": "dynamicToolCall", "status": "completed",
    ...                             "tool": "run_cells", "durationMs": 11063}}}})
    'run_cells stayed open 11.1s'
    >>> _label({"kind": "codex_event",
    ...         "payload": {"method": "thread/tokenUsage/updated"}})
    ''
    >>> _label({"kind": "codex_event",
    ...         "payload": {"method": "item/agentMessage/delta"}})
    ''
    """
    kind = row.get("kind")
    payload = row.get("payload") or {}
    if kind == "input":
        return "question sent"
    if kind == "notebook_send":
        return f"notebook {payload.get('type')}"
    if kind == "tool_result":
        return f"answered {payload.get('line')}"

    method = str(payload.get("method", ""))
    if method.endswith("/delta") or method in NOISE:
        return ""
    params = payload.get("params") or {}
    if method == "item/tool/call":
        return f"{params.get('tool')} called"
    item = params.get("item") or {}
    shape = item.get("type")
    if shape == "agentMessage":
        return ""
    if shape == "dynamicToolCall":
        held = item.get("durationMs")
        if held is None:
            return f"{item.get('tool')} arguments arrived"
        return f"{item.get('tool')} stayed open {held / 1000:.1f}s"
    if shape:
        return f"{shape} {method.rsplit('/', 1)[-1]}"
    return method


def _delta(row: Mapping[str, Any]) -> Optional[str]:
    """One word of a streamed message, or None for any other line."""
    payload = row.get("payload") or {}
    if not str(payload.get("method", "")).endswith("/delta"):
        return None
    return str((payload.get("params") or {}).get("delta", ""))


def _spoken(said: str) -> str:
    """One streamed message as a table row, cut to ``SAID_WIDTH``.

    The prose is what shows a turn that announced work instead of doing it, so
    the table quotes it.

    >>> _spoken("  I will run the guess of 5 now. ")
    'said "I will run the guess of 5 now."'
    >>> _spoken("x" * 80).endswith('…"')
    True
    """
    written = " ".join(said.split())
    if len(written) > SAID_WIDTH:
        written = written[: SAID_WIDTH - 1].rstrip() + "…"
    return f'said "{written}"'


def _spent(rows: Sequence[Mapping[str, Any]]) -> tuple[float, float, float]:
    """Split one turn into the seconds Yukti, the unread calls and the model took.

    ``durationMs`` is Codex's measure of how long one tool call stayed open,
    which is how long Yukti took to answer it. Yukti's own work, from reading
    the call to answering it, comes out of that. What is left is a call that
    had arrived and was waiting to be read, which is the shape of the stall
    that ``app_server._line`` exists to prevent, so ``unread`` near zero is the
    number to expect.

    Pro: the three numbers add up to the turn, so no cost hides.
    Con: the model bucket also holds the network, so it is an upper bound.
    """
    stamps = [at for at in (_at(row) for row in rows) if at is not None]
    turn = (max(stamps) - min(stamps)) / 1000 if stamps else 0.0

    called: Optional[int] = None
    yukti = 0.0
    calls = 0.0
    for row in rows:
        payload = row.get("payload") or {}
        at = _at(row)
        if payload.get("method") == "item/tool/call" and at is not None:
            called = at
        if row.get("kind") == "tool_result" and called is not None and at is not None:
            yukti += (at - called) / 1000
            called = None
        item = (payload.get("params") or {}).get("item") or {}
        if item.get("type") == "dynamicToolCall" and item.get("durationMs"):
            calls += item["durationMs"] / 1000
    return yukti, max(calls - yukti, 0.0), max(turn - calls, 0.0)


def timeline(rows: Sequence[Mapping[str, Any]], gap_ms: int = GAP_MS) -> str:
    """Render one trace as the Markdown table of where its seconds went.

    Every row is a line of the trace that arrived at least ``gap_ms`` after the
    line before it, so a fast stretch stays one row and a slow one is named.

    >>> print(timeline([{"at": 1000, "kind": "input", "payload": {}},
    ...                 {"at": 3000, "kind": "notebook_send",
    ...                  "payload": {"type": "insert_cells"}}]))
    **2.0s turn** — model 2.0s, unread 0.0s, Yukti 0.0s
    <BLANKLINE>
    | at | waited | what |
    | --- | --- | --- |
    | 2.0s | 2.0s | notebook insert_cells |
    """
    stamped = [(at, row) for at, row in ((_at(row), row) for row in rows) if at]
    if not stamped:
        return "The trace holds no timestamps."

    start = stamped[0][0]
    lines = []
    previous = start
    said: list[str] = []
    for at, row in stamped:
        delta = _delta(row)
        if delta is not None:
            said.append(delta)
            continue
        label = _label(row)
        if not label and said:
            label, said = _spoken("".join(said)), []
        if not label:
            continue
        gap = at - previous
        previous = at
        if gap >= gap_ms:
            lines.append(f"| {(at - start) / 1000:.1f}s | {gap / 1000:.1f}s | {label} |")

    yukti, unread, model = _spent([row for _at_, row in stamped])
    return "\n".join(
        [
            f"**{(stamped[-1][0] - start) / 1000:.1f}s turn** — "
            f"model {model:.1f}s, unread {unread:.1f}s, Yukti {yukti:.1f}s",
            "",
            "| at | waited | what |",
            "| --- | --- | --- |",
            *lines,
        ]
    )


def read(path: Path, gap_ms: int = GAP_MS) -> str:
    """Render the timing table of a trace file, one line at a time.

    A malformed last line is dropped: a killed kernel can leave one behind, and
    the reader is most wanted exactly then.
    """
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return timeline(rows, gap_ms)


def main() -> None:
    """Print the timing table of every trace file named on the command line."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("traces", nargs="+", type=Path)
    parser.add_argument(
        "--gap-ms",
        type=int,
        default=GAP_MS,
        help=f"name a wait of at least this many milliseconds (default {GAP_MS})",
    )
    written = parser.parse_args()
    for path in written.traces:
        print(f"\n{path}\n")
        print(read(path, written.gap_ms))


if __name__ == "__main__":
    main()
