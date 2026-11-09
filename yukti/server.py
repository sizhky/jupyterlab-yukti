"""The Jupyter server routes that host ``%%ask``, ``%%yukti`` and the diff.

The kernel never sees these cells, so the notebook's language does not matter.
One WebSocket carries one turn: the frontend opens it with the question, the
server streams the answer, and each ``run`` waits for the outputs the frontend
sends back after the notebook's kernel ran the cell.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from jupyter_core.utils import ensure_async
from jupyter_server.auth.decorator import ws_authenticated
from jupyter_server.base.handlers import APIHandler, JupyterHandler
from jupyter_server.base.websocket import WebSocketMixin
from jupyter_server.utils import url_path_join
from tornado import web, websocket

from .settings import DEFAULTS
from .staged import staged_cells
from .turn import ask, yukti_cell

# The settings each notebook's ``%%yukti`` cells wrote, by notebook path. The
# server holds them, so they last until the server stops, across kernel restarts.
SETTINGS: dict[str, dict[str, Any]] = {}


class Closed(RuntimeError):
    """The notebook closed the turn while a cell was running."""


def notebook_file(handler: JupyterHandler, path: str) -> Path:
    """The notebook on disk, from its path relative to the server root."""
    root = Path(handler.settings["server_root_dir"]).expanduser().resolve()
    full = (root / path).resolve()
    if root not in full.parents and full != root:
        raise web.HTTPError(403, f"{path} is outside the server root")
    return full


class AskSocket(WebSocketMixin, websocket.WebSocketHandler, JupyterHandler):
    """One WebSocket per ``%%ask`` turn."""

    auth_resource = "kernels"

    def set_default_headers(self) -> None:
        """Skip JupyterHandler's page headers, which a WebSocket does not take."""

    @ws_authenticated
    async def get(self, *args: Any, **kwargs: Any) -> None:
        # The agent can run code in the kernel, so asking needs the right to execute.
        if not await ensure_async(self.authorizer.is_authorized(self, self.current_user, "execute", "kernels")):
            raise web.HTTPError(403)
        await super().get(*args, **kwargs)

    def open(self, *args: Any, **kwargs: Any) -> None:
        super().open()
        self.loop = asyncio.get_running_loop()
        self.waiting: dict[str, Future] = {}
        self.started = False

    def emit(self, payload: dict[str, Any]) -> None:
        """Send one message from any thread; a closed socket drops it."""
        def put() -> None:
            if self.ws_connection is not None:
                self.write_message(json.dumps(payload, default=str))
        self.loop.call_soon_threadsafe(put)

    def run(self, cell_id: str, source: str) -> list:
        """Ask the notebook to run one cell, and wait for its outputs."""
        done: Future = Future()
        self.waiting[cell_id] = done
        self.emit({"type": "run", "cell_id": cell_id, "source": source})
        return done.result()

    def on_message(self, message: str | bytes) -> None:
        data = json.loads(message)
        if data.get("type") == "ran" and data.get("cell_id") in self.waiting:
            outputs = data.get("outputs")
            self.waiting.pop(data["cell_id"]).set_result(outputs if isinstance(outputs, list) else [])
        elif data.get("type") == "ask" and not self.started:
            self.started = True
            asyncio.ensure_future(self.turn(data))

    async def turn(self, data: dict[str, Any]) -> None:
        path = str(data.get("path", ""))
        settings = {**SETTINGS.get(path, DEFAULTS)}
        # The dropdown decides the voice for this turn only.
        if isinstance(data.get("mode"), str) and data["mode"]:
            settings["mode"] = data["mode"]
        cells = data.get("cells") if isinstance(data.get("cells"), list) else []
        try:
            folder = notebook_file(self, path).parent
            await self.loop.run_in_executor(
                None, ask, str(data.get("source", "")), cells, settings, folder, self.emit, self.run
            )
            self.emit({"type": "done"})
        except Exception as error:  # every failure reaches the cell, as a kernel error would
            self.log.debug("yukti turn failed", exc_info=True)
            self.emit({"type": "error", "ename": type(error).__name__, "evalue": str(error),
                        "traceback": traceback.format_exception_only(type(error), error)})
        finally:
            self.loop.call_soon_threadsafe(self.close)

    def on_close(self) -> None:
        for done in self.waiting.values():
            done.set_exception(Closed("the notebook closed the turn"))
        self.waiting.clear()


class SettingsHandler(APIHandler):
    """Apply one ``%%yukti`` cell to the notebook it belongs to."""

    @web.authenticated
    def post(self) -> None:
        body = self.get_json_body() or {}
        path = str(body.get("path", ""))
        try:
            after, shown = yukti_cell(str(body.get("source", "")), SETTINGS.get(path, DEFAULTS))
        except ValueError as error:
            raise web.HTTPError(400, str(error)) from None
        SETTINGS[path] = after
        self.finish({"markdown": shown})


class StagedHandler(APIHandler):
    """The cells git holds for one notebook, for the Diff button."""

    @web.authenticated
    def get(self) -> None:
        full = notebook_file(self, self.get_query_argument("path"))
        try:
            self.finish(json.dumps(staged_cells(str(full), self.get_query_argument("rev", ""))))
        except RuntimeError as error:
            raise web.HTTPError(400, str(error)) from None


def setup_handlers(web_app: Any) -> None:
    base = url_path_join(web_app.settings["base_url"], "yukti")
    web_app.add_handlers(".*$", [
        (url_path_join(base, "ask"), AskSocket),
        (url_path_join(base, "settings"), SettingsHandler),
        (url_path_join(base, "staged"), StagedHandler),
    ])
