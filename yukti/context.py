from collections.abc import Mapping, Sequence
from typing import Any


OUTPUT_LIMIT_BYTES = 8 * 1024
REQUEST_LIMIT_BYTES = 512 * 1024


def truncate_output(content: str, limit: int = OUTPUT_LIMIT_BYTES) -> str:
    data = content.encode("utf-8")
    if len(data) <= limit:
        return content

    half = limit // 2
    head = data[:half].decode("utf-8", errors="ignore")
    tail = data[-half:].decode("utf-8", errors="ignore")
    size_kb = len(data) // 1024
    return f"{head}\n...\n[output truncated: original {size_kb} KB]\n...\n{tail}"


def truncate_request(content: str, limit: int = REQUEST_LIMIT_BYTES) -> str:
    data = content.encode("utf-8")
    if len(data) <= limit:
        return content

    half = limit // 2
    head = data[:half].decode("utf-8", errors="ignore")
    tail = data[-half:].decode("utf-8", errors="ignore")
    size_kb = len(data) // 1024
    marker = f"\n...\n[request truncated: original {size_kb} KB]\n...\n"
    return f"{head}{marker}{tail}"


def build_transcript(
    cells: Sequence[Mapping[str, Any]], question: str
) -> str:
    blocks: list[str] = []
    for cell in cells:
        cell_type = cell["cell_type"]
        cell_id = cell.get("cell_id", "")
        blocks.append(f"[{cell_type} cell_id={cell_id}]\n{cell.get('source', '')}")
        for output in cell.get("outputs", []):
            content = truncate_output(str(output.get("content", "")))
            blocks.append(f"[output]\n{content}")
    blocks.append(f"[user]\n{question.strip()}")
    return truncate_request("\n\n".join(blocks))
