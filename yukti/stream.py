"""Split the Codex delta stream into message and action events.

``YuktiMagics.ask`` used to hold this state machine in four ``nonlocal``
variables, so no test could reach it without faking the kernel, the comm, the
App Server and the display. It is now string in, events out.

Pro: every marker and holdback case is a plain unit test.
Con: callers learn one more module, and must remember to call ``finish``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Sequence, Union

from .actions import parse_action


MARKER = "\n%%action\n"


@dataclass(frozen=True)
class Message:
    """Markdown for the cell output, cumulative inside one message block.

    A block restarts empty after every action, because the notebook keeps one
    display handle and each update replaces the whole block.
    """

    text: str


@dataclass(frozen=True)
class Action:
    """One validated action, ready for the notebook comm."""

    payload: dict


Event = Union[Message, Action]


class ActionStream:
    """Turn Codex deltas into message and action events.

    The model streams Markdown, ends a message with a line holding only
    ``%%action``, then writes one JSON action on one line. A bare JSON line is
    also accepted, because the model sometimes skips the marker.

    >>> stream = ActionStream()
    >>> stream.feed("Adding a cell.\\n%%action\\n")
    [Message(text='Adding a cell.')]
    >>> stream.feed('{"type":"answer","source":"hi"}\\n')
    [Action(payload={'type': 'answer', 'source': 'hi'})]
    >>> stream.feed("Done.")
    []
    >>> stream.finish()
    [Message(text='Done.')]
    """

    def __init__(self, cells: Sequence[Mapping[str, Any]] = ()) -> None:
        self._cells = list(cells)
        self._pending = ""
        self._text = ""
        self._in_action = False
        self.received_delta = False

    def feed(self, delta: str) -> List[Event]:
        """Consume one delta and return the events it completed."""
        self.received_delta = True
        self._pending += delta
        events: List[Event] = []
        while True:
            if self._in_action:
                if "\n" not in self._pending:
                    return events
                line, self._pending = self._pending.split("\n", 1)
                if line.strip():
                    events.append(Action(parse_action(line, self._cells)))
                self._text = ""
                self._in_action = False
                continue
            if MARKER in self._pending:
                visible, self._pending = self._pending.split(MARKER, 1)
                events.append(self._grow(visible))
                self._in_action = True
                continue
            implicit = self._pending.find("\n{")
            if self._pending.lstrip().startswith("{"):
                self._pending = self._pending.lstrip()
                self._in_action = True
                continue
            if implicit >= 0:
                events.append(self._grow(self._pending[:implicit]))
                self._pending = self._pending[implicit + 1 :]
                self._in_action = True
                continue
            # Hold back the tail that could still turn out to be a split
            # marker, so "done\n%%act" never renders as visible Markdown.
            held = max(0, len(self._pending) - len(MARKER) + 1)
            visible, self._pending = self._pending[:held], self._pending[held:]
            if visible:
                events.append(self._grow(visible))
            return events

    def finish(self) -> List[Event]:
        """Flush whatever the stream still holds after the last delta."""
        pending, self._pending = self._pending, ""
        if self._in_action:
            return [Action(parse_action(pending, self._cells))] if pending.strip() else []
        return [self._grow(pending)]

    def _grow(self, visible: str) -> Message:
        self._text += visible
        return Message(self._text)
