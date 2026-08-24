"""The session settings ``%%yukti`` writes and ``%%ask`` reads.

Codex privileges live in one module, so a ``%%yukti`` line cannot promise a
privilege that ``thread/start`` never receives. The vocabulary follows the
Codex App Server protocol schema of codex-cli 0.146: three sandbox modes, and
approval requests routed either to the user or to the ``auto_review`` subagent.

Pro: one grammar, one validator, and ``%%ask --debug`` shows the result.
Con: a Codex release that renames a mode needs an edit here.
"""

from __future__ import annotations

from typing import Any, Mapping


# The complete SandboxMode enum of the App Server protocol.
SANDBOXES = ("read-only", "workspace-write", "danger-full-access")

# A notebook has no prompt, so a policy that asks the user would stall the
# kernel. ``never`` asks nobody; ``auto_review`` lets a Codex subagent decide.
APPROVAL_PARAMS = {
    "never": {"approvalPolicy": "never"},
    "auto_review": {"approvalPolicy": "on-request", "approvalsReviewer": "auto_review"},
}

FLAGS = {"on": True, "off": False, "true": True, "false": False, "yes": True, "no": False}

# ``cwd`` is empty for the disposable temp directory and "." for the kernel's
# own directory, so a profile name is the only thing a reader must know.
PROFILES = {
    "sandboxed": {"sandbox": "read-only", "cwd": "", "network": False, "tools": False},
    "elevated": {"sandbox": "workspace-write", "cwd": ".", "network": False, "tools": True},
    "full": {"sandbox": "danger-full-access", "cwd": ".", "network": True, "tools": True},
}

# ``run`` sits outside the profiles: a sandbox bounds what Codex may do on
# disk, and running a cell happens in the kernel instead, where the user's own
# namespace is. It is on, so an inserted cell can report what it printed, and
# one line turns it off.
DEFAULTS: dict[str, Any] = {
    "permissions": "sandboxed",
    "approvals": "never",
    "run": True,
    "writable_roots": [],
    **PROFILES["sandboxed"],
}

# The help text reads these, so a new key cannot go undocumented.
KEY_HELP = {
    "permissions": " | ".join(PROFILES),
    "sandbox": " | ".join(SANDBOXES),
    "approvals": " | ".join(APPROVAL_PARAMS),
    "cwd": "a path, or empty for a disposable directory",
    "network": "on | off",
    "tools": "on | off",
    "run": "on | off, to run the code cells Yukti inserts",
    "writable_roots": "paths separated by commas",
}
PROFILE_KEYS = ("sandbox", "cwd", "network", "tools")


def _shown(value: Any) -> str:
    """Render one setting for a reader.

    >>> [_shown(True), _shown(False), _shown(""), _shown("."), _shown(["/tmp"])]
    ['on', 'off', 'disposable', 'kernel directory', '/tmp']
    """
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, list):
        return ", ".join(value) or "none"
    if value == ".":
        return "kernel directory"
    return str(value) or "disposable"


def summary(current: Mapping[str, Any]) -> str:
    """Render the settings in force as a Markdown table.

    ``KEY_HELP`` fixes the order, so a key appears here the day it is
    documented, and the reader sees words instead of the JSON payload.

    Pro: the echo of a ``%%yukti`` cell reads like the help table above it.
    Con: a machine reader of the cell output must parse Markdown.

    >>> "| `sandbox` | read-only |" in summary(DEFAULTS)
    True
    >>> "| `network` | off |" in summary(DEFAULTS)
    True
    >>> "| `run` | on |" in summary(DEFAULTS)
    True
    """
    rows = [
        f"| `{key}` | {_shown(current.get(key, DEFAULTS.get(key, '')))} |"
        for key in KEY_HELP
    ]
    return "\n".join(
        [
            "**Yukti** — every later `%%ask` cell is now going to run with:",
            "",
            "| setting | value |",
            "| --- | --- |",
            *rows,
            "",
            "Write `%%yukti` alone for the full vocabulary.",
        ]
    )


def help_text(current: Mapping[str, Any]) -> str:
    """Render the vocabulary and the settings in force as Markdown.

    The table is built from ``PROFILES``, so it cannot drift from the profiles
    the parser applies.

    >>> "`elevated`" in help_text(DEFAULTS)
    True
    >>> "read-only" in help_text(DEFAULTS)
    True
    """
    header = "| profile | " + " | ".join(PROFILE_KEYS) + " |"
    rule = "| --- " * (len(PROFILE_KEYS) + 1) + "|"
    rows = [
        f"| `{name}` | " + " | ".join(_shown(profile[key]) for key in PROFILE_KEYS) + " |"
        for name, profile in PROFILES.items()
    ]
    keys = [f"- `{key}`: {allowed}" for key, allowed in KEY_HELP.items()]
    now = ", ".join(
        f"{key} = {_shown(current.get(key, default))}"
        for key, default in sorted(DEFAULTS.items())
    )
    return "\n".join(
        [
            "**`%%yukti`** sets the privileges every later `%%ask` cell runs with.",
            "Write one `key: value` per line. A `permissions` line applies a whole",
            "profile, and a later line overrides one of its values.",
            "",
            header,
            rule,
            *rows,
            "",
            *keys,
            "",
            f"In force now: {now}.",
            "",
            "```python",
            "%%yukti",
            "permissions: elevated",
            "```",
        ]
    )


def _choice(key: str, raw: str, allowed: tuple) -> str:
    if raw not in allowed:
        raise ValueError(f"{key} must be one of {', '.join(allowed)}")
    return raw


def _value(key: str, raw: str) -> Any:
    if key == "permissions":
        return _choice(key, raw, tuple(PROFILES))
    if key == "sandbox":
        return _choice(key, raw, SANDBOXES)
    if key == "approvals":
        return _choice(key, raw, tuple(APPROVAL_PARAMS))
    if key in {"network", "tools", "run"}:
        return FLAGS[_choice(key, raw, tuple(FLAGS))]
    if key == "writable_roots":
        return [root.strip() for root in raw.split(",") if root.strip()]
    if key == "cwd":
        return raw
    raise ValueError(f"unknown setting {key}; use one of {', '.join(sorted(DEFAULTS))}")


def parse_settings(cell: str, current: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the ``key: value`` lines of one ``%%yukti`` cell to ``current``.

    A ``permissions`` line applies a whole profile, and a later line overrides
    one of its values.

    >>> parse_settings("permissions: elevated", DEFAULTS)["sandbox"]
    'workspace-write'
    >>> parse_settings("permissions: elevated\\nnetwork: on", DEFAULTS)["network"]
    True
    >>> parse_settings("writable_roots: /tmp/a, /tmp/b", DEFAULTS)["writable_roots"]
    ['/tmp/a', '/tmp/b']
    >>> parse_settings("run: off", DEFAULTS)["run"]
    False
    >>> parse_settings("sandbox: everything", DEFAULTS)
    Traceback (most recent call last):
    ValueError: Yukti setting: sandbox must be one of read-only, workspace-write, danger-full-access
    """
    settings = dict(current)
    for line in cell.splitlines():
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        if ":" not in text:
            raise ValueError(
                f"Yukti setting: write one 'key: value' per line, not {text!r}"
            )
        key, _, raw = text.partition(":")
        key, raw = key.strip().replace("-", "_"), raw.strip()
        try:
            settings[key] = _value(key, raw)
        except (KeyError, ValueError) as error:
            raise ValueError(f"Yukti setting: {error}") from None
        if key == "permissions":
            settings.update(PROFILES[raw])
    return settings
