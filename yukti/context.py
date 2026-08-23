"""Build the transcript one Codex turn reads: the notebook prefix, then the
question.

``truncate_output`` and ``truncate_request`` used to be two near-identical
public functions that no test exercised. They differed by one word, so one
private helper carries both limits now.
"""

from collections.abc import Mapping, Sequence
from typing import Any


OUTPUT_LIMIT_BYTES = 8 * 1024
REQUEST_LIMIT_BYTES = 512 * 1024


def _truncate(content: str, limit: int, label: str) -> str:
    """Keep the head and the tail of ``content`` inside ``limit`` bytes.

    >>> _truncate("abc", 100, "output")
    'abc'
    >>> _truncate("abcdefgh", 4, "output")
    'ab\\n...\\n[output truncated: original 0 KB]\\n...\\ngh'
    """
    data = content.encode("utf-8")
    if len(data) <= limit:
        return content

    half = limit // 2
    head = data[:half].decode("utf-8", errors="ignore")
    tail = data[-half:].decode("utf-8", errors="ignore")
    size_kb = len(data) // 1024
    return f"{head}\n...\n[{label} truncated: original {size_kb} KB]\n...\n{tail}"


def build_transcript(cells: Sequence[Mapping[str, Any]], question: str) -> str:
    """Render the notebook prefix and the question as one bounded string."""
    blocks: list[str] = []
    for cell in cells:
        cell_type = cell["cell_type"]
        cell_id = cell.get("cell_id", "")
        blocks.append(f"[{cell_type} cell_id={cell_id}]\n{cell.get('source', '')}")
        for output in cell.get("outputs", []):
            content = _truncate(
                str(output.get("content", "")), OUTPUT_LIMIT_BYTES, "output"
            )
            blocks.append(f"[output]\n{content}")
    blocks.append(f"[user]\n{question.strip()}")
    return _truncate("\n\n".join(blocks), REQUEST_LIMIT_BYTES, "request")
