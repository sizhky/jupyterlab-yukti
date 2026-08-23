"""Collect Codex message deltas into the blocks one ``%%ask`` cell renders.

``YuktiMagics.ask`` used to hold this state in ``nonlocal`` variables, so no
test could reach it without faking the kernel, the comm, the App Server and
the display. It is now string in, message out.

Codex changes the notebook by calling the tools in ``actions``, so nothing
here reads the text for a hidden protocol. A message is only ever a message.

Pro: prose that contains JSON is prose, and the state machine is three lines.
Con: callers must call ``close`` when a tool call ends a block.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    """Markdown for one cell output, cumulative inside one block.

    Text restarts empty after every action, so ``block`` counts the blocks.
    A caller that renders every block into one output would erase the earlier
    ones; give each block its own output instead.
    """

    text: str
    block: int


class MessageStream:
    """Grow one message block per delta, and start a new one after an action.

    >>> stream = MessageStream()
    >>> stream.feed("Adding ")
    Message(text='Adding ', block=0)
    >>> stream.feed("a cell.")
    Message(text='Adding a cell.', block=0)
    >>> stream.close()
    >>> stream.feed("Done.")
    Message(text='Done.', block=1)

    A brace in the prose stays in the prose:

    >>> MessageStream().feed('{"information": {"a": 1}}')
    Message(text='{"information": {"a": 1}}', block=0)
    """

    def __init__(self) -> None:
        self._text = ""
        self._block = 0
        self.received_delta = False

    def feed(self, delta: str) -> Message:
        """Add one delta to the open block and return the block to render."""
        self.received_delta = True
        self._text += delta
        return Message(self._text, self._block)

    def close(self) -> None:
        """End the open block, so the next delta opens its own output."""
        self._text = ""
        self._block += 1
