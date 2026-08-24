"""Throwaway probe: who is silent for 11 seconds after Yukti answers a tool call?

The turn is driven here, not from a kernel, so the notebook, the comm and the
frontend are out of the picture. Two clocks are recorded on one axis:

* every line read from the App Server's stdout, and the moment this script
  wrote its reply to the App Server's stdin;
* every line the App Server wrote to stderr, with ``RUST_LOG`` turned up.

The report then prints, for each tool call, how long the App Server stayed
silent after the reply was flushed, and what it logged during that silence.

The tool results are faked: this measures the protocol, not the code, so no
cell is inserted and nothing is executed.

    python scripts/tmp/probe_app_server.py
    python scripts/tmp/probe_app_server.py --no-mcp
    python scripts/tmp/probe_app_server.py --rust-log codex_core=trace
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from yukti.actions import tool_specs  # noqa: E402
from yukti.app_server import base_instructions  # noqa: E402


ASK = """[markdown cell_id=intro]
A guessing probe.

[user]
Insert one code cell that prints 1 and run it. Then insert a second code cell
that prints 2 and run that one too. Answer with the two numbers you saw."""


def now() -> float:
    return time.time()


class Probe:
    def __init__(self, rust_log: str, no_mcp: bool, out: Path, fresh_home: bool) -> None:
        command = ["codex", "app-server", "--stdio", "-c", "project_doc_max_bytes=0"]
        if no_mcp:
            command += ["-c", "mcp_servers={}"]
        self.log = (out / "stderr.log").open("w", encoding="utf-8")
        self.marks: list[tuple[float, str, str]] = []
        self.errors: list[tuple[float, str]] = []
        self.command = command
        # Everything AppServer builds around the process, and nothing else, so
        # a slow run here blames the environment and not the notebook.
        # An empty --rust-log leaves the variable unset, which is how the
        # notebook runs codex, so logging itself can be ruled in or out.
        environment = {**os.environ}
        environment.pop("RUST_LOG", None)
        if rust_log:
            environment["RUST_LOG"] = rust_log
        self.workdir = Path.cwd()
        if fresh_home:
            home = Path(tempfile.mkdtemp(prefix="probe-")) / "codex-home"
            home.mkdir(parents=True)
            source = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
            if source.is_file():
                (home / "auth.json").symlink_to(source)
            environment["CODEX_HOME"] = str(home)
            self.workdir = home.parent / "work"
            self.workdir.mkdir()
            print(f"CODEX_HOME={home}")
        print("$ " + " ".join(command))
        self.process = subprocess.Popen(
            command,
            cwd=self.workdir,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.started = now()
        # A pipe nobody reads fills up and blocks the child, and RUST_LOG makes
        # that certain, so stderr is drained on its own thread from the start.
        threading.Thread(target=self._drain, daemon=True).start()
        self.next_id = 0
        self.cells: dict[str, str] = {}

    def _drain(self) -> None:
        assert self.process.stderr
        for line in self.process.stderr:
            at = now()
            self.errors.append((at, line.rstrip()))
            self.log.write(f"{at - self.started:8.3f} {line}")
            self.log.flush()

    def mark(self, kind: str, what: str) -> float:
        at = now()
        self.marks.append((at, kind, what))
        return at

    def send(self, message: dict) -> float:
        assert self.process.stdin
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        return self.mark("sent", message.get("method") or f'reply id={message.get("id")}')

    def read(self) -> dict:
        assert self.process.stdout
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("the App Server stopped")
        message = json.loads(line)
        method = message.get("method") or f'result id={message.get("id")}'
        item = (message.get("params") or {}).get("item") or {}
        if item.get("type"):
            method += f' {item["type"]}'
            if item.get("durationMs") is not None:
                method += f' durationMs={item["durationMs"]}'
        self.mark("read", method)
        return message

    def request(self, method: str, params: dict) -> dict:
        self.next_id += 1
        wanted = self.next_id
        self.send({"method": method, "id": wanted, "params": params})
        while True:
            message = self.read()
            if message.get("id") == wanted and "method" not in message:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})

    def answer(self, message: dict) -> None:
        """Reply to one item/tool/call the way Yukti replies, but instantly."""
        params = message.get("params", {})
        tool, arguments = params.get("tool"), params.get("arguments") or {}
        if tool == "insert_cells":
            written = arguments["cells"][0]
            cell_id = uuid4().hex
            self.cells[cell_id] = written.get("source", "")
            text = f"insert 1 cell: finished, cell_id {cell_id}"
        elif tool == "run_cells":
            cell_id = arguments["cells"][0]["cell_id"]
            source = self.cells.get(cell_id, "")
            printed = "1" if "1" in source else "2"
            text = f"run 1 cell: finished\n[cell_id {cell_id} printed]\n{printed}"
        else:
            text = f"{tool}: finished"
        self.send(
            {
                "id": message["id"],
                "result": {
                    "success": True,
                    "contentItems": [{"type": "inputText", "text": text}],
                },
            }
        )

    def run(self) -> None:
        self.request(
            "initialize",
            {
                "clientInfo": {"name": "probe", "title": "Probe", "version": "0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.request("account/read", {})
        thread = self.request(
            "thread/start",
            {
                "ephemeral": True,
                "cwd": str(self.workdir),
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "modelProvider": "openai",
                "baseInstructions": base_instructions(False, True),
                "developerInstructions": "",
                "dynamicTools": tool_specs(True),
                "config": {"project_doc_max_bytes": 0},
            },
        )
        self.next_id += 1
        turn = self.next_id
        self.send(
            {
                "method": "turn/start",
                "id": turn,
                "params": {
                    "threadId": thread["thread"]["id"],
                    "input": [{"type": "text", "text": ASK}],
                },
            }
        )
        while True:
            message = self.read()
            if message.get("method") == "item/tool/call" and "id" in message:
                self.answer(message)
                continue
            if message.get("method") in (
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
            ):
                self.send({"id": message["id"], "result": {"decision": "acceptForSession"}})
                continue
            if message.get("method") == "turn/completed":
                return

    def report(self) -> None:
        print("\n== timeline ==")
        previous = self.started
        for at, kind, what in self.marks:
            gap = at - previous
            previous = at
            if gap >= 0.4 or kind == "sent":
                print(f"{at - self.started:8.2f}s +{gap:5.2f}s {kind:5} {what[:70]}")

        print("\n== silence after each reply ==")
        for index, (at, kind, what) in enumerate(self.marks):
            if kind != "sent" or not what.startswith("reply"):
                continue
            after = next(
                ((t, k, w) for t, k, w in self.marks[index + 1 :] if k == "read"), None
            )
            if after is None:
                continue
            quiet = after[0] - at
            print(f"replied at {at - self.started:7.2f}s, next line {quiet:6.2f}s later")
            logged = [
                f"    {t - self.started:8.3f} {line[:100]}"
                for t, line in self.errors
                if at < t < after[0]
            ]
            print("\n".join(logged) or "    the App Server logged nothing")

        print(f"\nstderr lines: {len(self.errors)} -> {self.log.name}")

    def close(self) -> None:
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()
        self.log.close()


def via_yukti() -> None:
    """Drive the real ``AppServer`` instead, with no kernel around it.

    The standalone probe answers a tool call in microseconds and the App Server
    answers back in the same millisecond. If this mode is slow, the difference
    is something ``AppServer`` sets up: its own CODEX_HOME, its working
    directory, or its thread params.
    """
    import tempfile

    from yukti.app_server import AppServer
    from yukti.settings import DEFAULTS

    started = now()
    calls: list[tuple[float, float, str]] = []

    def on_event(event: dict) -> None:
        item = (event.get("params") or {}).get("item") or {}
        if item.get("type") == "dynamicToolCall" and item.get("durationMs") is not None:
            print(
                f"{now() - started:8.2f}s  {item.get('tool')} "
                f"durationMs={item['durationMs']}"
            )

    cells: dict[str, str] = {}

    def on_action(tool: str, arguments) -> str:
        at = now()
        if tool == "insert_cells":
            cell_id = uuid4().hex
            cells[cell_id] = arguments["cells"][0].get("source", "")
            text = f"insert 1 cell: finished, cell_id {cell_id}"
        elif tool == "run_cells":
            cell_id = arguments["cells"][0]["cell_id"]
            printed = "1" if "1" in cells.get(cell_id, "") else "2"
            text = f"run 1 cell: finished\n[cell_id {cell_id} printed]\n{printed}"
        else:
            text = f"{tool}: finished"
        calls.append((at - started, now() - at, tool))
        return text

    with tempfile.TemporaryDirectory(prefix="probe-") as root:
        with AppServer(root, dict(DEFAULTS)) as server:
            server.run(ASK, on_event=on_event, on_action=on_action)

    print("\n== answers ==")
    for at, took, tool in calls:
        print(f"answered {tool:13} at {at:7.2f}s in {took * 1000:.1f}ms")
    print(f"turn: {now() - started:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rust-log", default="debug")
    parser.add_argument("--no-mcp", action="store_true")
    parser.add_argument("--via-yukti", action="store_true")
    parser.add_argument("--fresh-home", action="store_true")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    written = parser.parse_args()
    written.out.mkdir(parents=True, exist_ok=True)

    if written.via_yukti:
        via_yukti()
        return

    probe = Probe(written.rust_log, written.no_mcp, written.out, written.fresh_home)
    try:
        probe.run()
    except Exception as error:  # a probe reports, it does not raise
        print(f"stopped: {error!r}")
    finally:
        probe.report()
        probe.close()


if __name__ == "__main__":
    main()
