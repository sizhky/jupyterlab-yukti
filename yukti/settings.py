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

# OpenAI models available via Codex. GPT-5.6 tiers: Astra (flagship/GPT-6),
# Sol (state-of-the-art), Terra (balanced), Luna (fast/cheap). Legacy: 4o/4-turbo/4/3.5-turbo.
MODELS = (
    "gpt-6-astra",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4",
    "gpt-3.5-turbo",
)

# Claude models the ``claude`` CLI accepts by alias or by id.
CLAUDE_MODELS = (
    "opus",
    "sonnet",
    "haiku",
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
)

# A backend line works like a profile: it sets the model that backend can run,
# and a later ``model`` line overrides it. ``codex`` runs ``codex app-server``
# on the ChatGPT subscription; ``claude`` runs the user's own signed-in
# ``claude`` CLI on the Claude subscription.
BACKENDS = {
    "codex": {"model": "gpt-5.6-luna", "models": MODELS},
    "claude": {"model": "sonnet", "models": CLAUDE_MODELS},
}

# Reasoning effort levels for models that support it (Sol, Terra, Luna, Astra).
REASONING_EFFORT = ("low", "medium", "high")

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
    "backend": "codex",
    "permissions": "sandboxed",
    "approvals": "never",
    "run": True,
    "model": "gpt-5.6-luna",
    "reasoning_effort": "medium",
    "writable_roots": [],
    **PROFILES["sandboxed"],
}

# The help text reads these, so a new key cannot go undocumented.
KEY_HELP = {
    "backend": " | ".join(BACKENDS),
    "permissions": " | ".join(PROFILES),
    "sandbox": " | ".join(SANDBOXES),
    "approvals": " | ".join(APPROVAL_PARAMS),
    "cwd": "a path, or empty for a disposable directory",
    "network": "on | off",
    "tools": "on | off",
    "run": "on | off, to run the code cells Yukti inserts",
    "model": " | ".join(MODELS + CLAUDE_MODELS),
    "reasoning_effort": " | ".join(REASONING_EFFORT),
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


def changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    """Name the settings one ``%%yukti`` cell changed, in one line.

    ``summary`` is the full table, which ``%%yukti --verbose`` still shows.

    >>> changes(DEFAULTS, {**DEFAULTS, "backend": "claude", "model": "sonnet"})
    '**Yukti**: backend = claude, model = sonnet'
    >>> changes(DEFAULTS, DEFAULTS)
    '**Yukti**: no change'
    """
    changed = [
        f"{key} = {_shown(after.get(key))}"
        for key in KEY_HELP
        if after.get(key) != before.get(key)
    ]
    return "**Yukti**: " + (", ".join(changed) or "no change")


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
            "profile, and a later line overrides one of its values. The cell then",
            "names what it changed; `%%yukti --verbose` shows every setting.",
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
    if key == "backend":
        return _choice(key, raw, tuple(BACKENDS))
    if key == "model":
        return _choice(key, raw, MODELS + CLAUDE_MODELS)
    if key == "reasoning_effort":
        return _choice(key, raw, REASONING_EFFORT)
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
    >>> parse_settings("backend: claude", DEFAULTS)["model"]
    'sonnet'
    >>> parse_settings("backend: claude\\nmodel: opus", DEFAULTS)["model"]
    'opus'
    >>> parse_settings("model: opus", DEFAULTS)
    Traceback (most recent call last):
    ValueError: Yukti setting: backend codex cannot run model opus
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
        if key == "backend":
            settings["model"] = BACKENDS[raw]["model"]
    backend = settings.get("backend", DEFAULTS["backend"])
    if settings["model"] not in BACKENDS[backend]["models"]:
        raise ValueError(
            f"Yukti setting: backend {backend} cannot run model {settings['model']}"
        )
    return settings
