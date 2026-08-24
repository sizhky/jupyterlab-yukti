"""Run the source of one cell in the kernel that asked the question.

A kernel runs one request at a time and the ``%%ask`` cell holds it, so the
notebook cannot run a cell on Yukti's behalf: the ``execute_request`` would
queue behind the cell that is still waiting for it. Yukti runs the source here
instead, and sends the outputs to the notebook, which paints them into the cell
it inserted. The code runs in the user's kernel, in the namespace the earlier
cells built; only the route the outputs take is different.

Pro: the agent reads what its own cell printed, so one turn can insert a cell,
run it, and then fix it.
Con: that cell owns no ``execute_request``, so ``input()`` and live widgets do
not work in it; one buffer per stream means stdout arrives before a rich output
that was written first; and the value of the last expression is captured, not
displayed, so it does not bind ``_``.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from IPython.core.displayhook import CapturingDisplayHook
from IPython.utils.capture import capture_output

from .context import OUTPUT_LIMIT_BYTES, truncate


# JupyterLab renders the escapes an IPython traceback carries, so they stay in
# the output and are stripped only from the text the model reads.
ANSI = re.compile(r"\x1b\[[0-9;]*m")

EMPTY = "the cell ran and printed nothing"


def _stream(name: str, content: str) -> dict[str, Any]:
    """One captured buffer as an nbformat stream output.

    >>> _stream("stdout", "hi\\n")
    {'output_type': 'stream', 'name': 'stdout', 'text': 'hi\\n'}
    """
    return {"output_type": "stream", "name": name, "text": content}


def _rich(data: Any, metadata: Any) -> dict[str, Any]:
    """One captured display as an nbformat output.

    A ``display`` call and the value of the last expression are both captured,
    so both become ``display_data`` and neither carries an ``Out[n]`` prompt.

    >>> _rich({"text/plain": "5"}, {})
    {'output_type': 'display_data', 'data': {'text/plain': '5'}, 'metadata': {}}
    """
    return {"output_type": "display_data", "data": data, "metadata": metadata}


def _said(output: Mapping[str, Any]) -> str:
    """What one output says to the model.

    An image is named, not sent: the model reads a transcript of text, and a
    base64 payload would spend the whole request on one picture.

    >>> _said({"output_type": "stream", "name": "stdout", "text": "hi"})
    'hi'
    >>> _said({"output_type": "display_data", "data": {"text/plain": "3"}})
    '3'
    >>> _said({"output_type": "display_data", "data": {"image/png": "iVBO"}})
    '[image/png]'
    >>> _said({"output_type": "error", "ename": "ValueError", "evalue": "no",
    ...        "traceback": ["\\x1b[0;31mValueError\\x1b[0m: no"]})
    'ValueError: no'
    """
    kind = output["output_type"]
    if kind == "stream":
        return str(output.get("text", ""))
    if kind == "error":
        return ANSI.sub("", "\n".join(output.get("traceback", []))).strip()
    data = output.get("data", {})
    for mime in ("text/plain", "text/markdown", "text/html"):
        if mime in data:
            return str(data[mime])
    return f"[{', '.join(data) or kind}]"


def run_source(shell: Any, source: str) -> tuple[list[dict[str, Any]], str]:
    """Run ``source`` in ``shell`` and return its outputs and what they said.

    The outputs are nbformat, so the notebook adds them to the cell that holds
    the source. The text is the tool result, so the model reads the same run
    the reader sees.

    ``store_history`` stays off, because the ``%%ask`` cell already owns the
    execution count this run reports, and a second history entry under that
    same number would make ``In`` and ``Out`` disagree with the notebook.

    >>> class Shell:
    ...     _showtraceback = None
    ...     def run_cell(self, source, store_history=False):
    ...         print("ran", source)
    >>> outputs, said = run_source(Shell(), "1")
    >>> said
    'ran 1'
    >>> outputs[0]["name"]
    'stdout'
    """
    failure: list[dict[str, Any]] = []
    value: list[dict[str, Any]] = []

    # ZMQInteractiveShell publishes a traceback on iopub, which would land in
    # the %%ask cell. The hook keeps it instead, so the failure reaches the
    # cell that caused it.
    def keep(etype: Any, evalue: Any, stb: list[str]) -> None:
        failure.append(
            {
                "output_type": "error",
                "ename": getattr(etype, "__name__", str(etype)),
                "evalue": str(evalue),
                "traceback": list(stb),
            }
        )

    # capture_output takes stdout, stderr and every display call, but run_cell
    # runs inside shell.display_trap, which reinstalls the shell's own display
    # hook: in a kernel that hook publishes the value of the last expression on
    # iopub, where it would land in the %%ask cell. The trap keeps its hook in
    # one attribute, so swapping that attribute is what redirects the value.
    trap = getattr(shell, "display_trap", None)
    shown = shell._showtraceback
    shell._showtraceback = keep
    try:
        with capture_output() as captured:
            hook = getattr(trap, "hook", None)
            if trap is not None:
                trap.hook = CapturingDisplayHook(shell=shell, outputs=value)
            try:
                shell.run_cell(source, store_history=False)
            finally:
                if trap is not None:
                    trap.hook = hook
    finally:
        shell._showtraceback = shown

    outputs: list[dict[str, Any]] = []
    if captured.stdout:
        outputs.append(_stream("stdout", captured.stdout))
    if captured.stderr:
        outputs.append(_stream("stderr", captured.stderr))
    outputs.extend(_rich(output.data, output.metadata) for output in captured.outputs)
    outputs.extend(_rich(last["data"], last["metadata"]) for last in value)
    outputs.extend(failure)

    said = "\n".join(_said(output) for output in outputs).strip()
    return outputs, truncate(said or EMPTY, OUTPUT_LIMIT_BYTES, "output")
