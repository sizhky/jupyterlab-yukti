"""Split the Codex delta stream into message and action events.

``YuktiMagics.ask`` used to hold this state machine in four ``nonlocal``
variables, so no test could reach it without faking the kernel, the comm, the
App Server and the display. It is now string in, events out.

Pro: every marker and holdback case is a plain unit test.
Con: callers learn one more module, and must remember to call ``finish``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Union

from .actions import parse_action


MARKER = "\n%%action\n"


@dataclass(frozen=True)
class Message:
    """Markdown for the cell output, cumulative inside one message block.

    Text restarts empty after every action, so ``block`` counts the blocks.
    A caller that renders every block into one output would erase the earlier
    ones; give each block its own output instead.
    """

    text: str
    block: int


@dataclass(frozen=True)
class Action:
    """One validated action, ready for the notebook comm."""

    payload: dict


Event = Union[Message, Action]


class ActionStream:
    """Turn Codex deltas into message and action events.

    The model streams Markdown, ends a message with a line holding only
    ``%%action``, then writes one JSON action on one line. A bare JSON line is
    also accepted, because the model sometimes skips the marker. That guess is
    reversible: a line the validator rejects goes back to the message as prose,
    so a JSON example inside an answer cannot fail the cell.

    Each action closes the current message block, so the next message starts a
    new one:

    >>> stream = ActionStream()
    >>> stream.feed("Adding a cell.\\n%%action\\n")
    [Message(text='Adding a cell.', block=0)]
    >>> stream.feed('{"type":"answer","source":"hi"}\\n')
    [Action(payload={'type': 'answer', 'source': 'hi'})]
    >>> stream.feed("Done.")
    []
    >>> stream.finish()
    [Message(text='Done.', block=1)]

    A JSON line the validator rejects is prose, not an action:

    >>> ActionStream().feed("Try:\\n{'a': 1}\\n")[-1]
    Message(text="Try:\\n{'a': 1}\\n", block=0)
    """

    def __init__(self, cells: Sequence[Mapping[str, Any]] = ()) -> None:
        self._cells = list(cells)
        self._pending = ""
        self._text = ""
        self._block = 0
        self._in_action = False
        # Set with ``_in_action``: a guessed line falls back to prose, a line
        # the model announced with the marker must be a valid action.
        self._guessed = False
        self._skipped = ""
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
                    action = self._action(line)
                    if action is None:
                        events.append(self._grow(self._skipped + line + "\n"))
                        self._in_action = False
                        continue
                    events.append(Action(action))
                # The block ends with the action it announced. Reset the text
                # and the block index together, so a caller never updates a
                # closed block with the next block's text.
                self._text = ""
                self._block += 1
                self._in_action = False
                continue
            if MARKER in self._pending:
                visible, self._pending = self._pending.split(MARKER, 1)
                events.append(self._grow(visible))
                self._in_action, self._guessed, self._skipped = True, False, ""
                continue
            implicit = self._pending.find("\n{")
            stripped = self._pending.lstrip()
            if stripped.startswith("{"):
                self._skipped = self._pending[: len(self._pending) - len(stripped)]
                self._pending = stripped
                self._in_action = self._guessed = True
                continue
            if implicit >= 0:
                events.append(self._grow(self._pending[:implicit]))
                self._skipped = "\n"
                self._pending = self._pending[implicit + 1 :]
                self._in_action = self._guessed = True
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
            if not pending.strip():
                return []
            action = self._action(pending)
            if action is None:
                return [self._grow(self._skipped + pending)]
            return [Action(action)]
        return [self._grow(pending)]

    def _action(self, line: str) -> Optional[dict]:
        """Validate one action line, or return ``None`` for a guess that fails.

        Pro: prose that opens with a brace costs the reader nothing.
        Con: a real action the model malformed is shown as text.
        """
        try:
            return parse_action(line, self._cells)
        except RuntimeError:
            if self._guessed:
                return None
            raise

    def _grow(self, visible: str) -> Message:
        self._text += visible
        return Message(self._text, self._block)
