from typing import Any


COMM_TARGET = "yukti.notebook_prefix"


class NotebookPrefixCache:
    def __init__(self) -> None:
        self._request: dict[str, Any] | None = None
        self._comm: Any = None

    def open(self, comm: Any, message: dict[str, Any]) -> None:
        data = message.get("content", {}).get("data", {})
        if (
            data.get("type") == "notebook_prefix"
            and isinstance(data.get("request_id"), str)
            and isinstance(data.get("cells"), list)
        ):
            self._request = data
            self._comm = comm
            return
        comm.close()

    def take(self) -> tuple[str, list[dict[str, Any]], str, Any]:
        """Hand the waiting request to ``%%ask``, and forget it.

        The mode rides on the same message as the cells, so the ``AI Mode``
        dropdown reaches the kernel without a comm of its own and without a
        round trip while the kernel is busy inside the ``%%ask`` cell.

        Pro: the mode in force is the one shown when the cell ran.
        Con: an old frontend sends no mode, so the name is validated where the
        instruction is built, not here.
        """
        request, self._request = self._request, None
        comm, self._comm = self._comm, None
        if request is None or comm is None:
            raise RuntimeError("Yukti did not receive the notebook prefix")
        mode = request.get("mode")
        return (
            request["request_id"],
            request["cells"],
            mode if isinstance(mode, str) else "",
            comm,
        )


def register_prefix_comm(shell: Any, cache: NotebookPrefixCache) -> None:
    kernel = getattr(shell, "kernel", None)
    if kernel is not None:
        kernel.comm_manager.register_target(COMM_TARGET, cache.open)
