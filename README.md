# Yukti

Yukti lets you ask Codex about everything visible above the current notebook cell.
The notebook stays the conversation: markdown, code, outputs, earlier questions,
and earlier answers all become context for the next `%%ask` question.

## Install for development

You need JupyterLab 4, Node.js, and an authenticated Codex CLI.

```bash
codex login
cd /Users/yeshwanth/Code/Personal/yukti
npm install
npm run build:prod
uv pip install -e .
```

Restart JupyterLab after installation.

## Use Yukti

Load the kernel extension once near the top of the notebook:

```python
%load_ext yukti
```

Then ask a question in a cell:

```python
%%ask
Why did the previous query return these rows?
```

Yukti chooses how to apply the response. Questions remain rendered answers in the
`%%ask` cell. Requests for new notebook content add code and markdown cells below
it. Requests to change earlier cells update those cells.

Yukti sends cells above that question through a Jupyter Comm channel. Outputs are
limited to 8 KB each, and the complete request is limited to 512 KB. Yukti starts
a disposable Codex App Server thread with its own visible base instruction, an
empty working directory, a read-only sandbox, and no project instruction files.

Yukti accepts only ChatGPT subscription authentication. It stops before the model
call when Codex reports API-key authentication.

## Change what `%%ask` may do

By default Codex only reads the transcript. A `%%yukti` cell raises that limit
for every later `%%ask` cell in the same kernel session:

```python
%%yukti
permissions: elevated
```

The cell prints the settings it applied. An empty `%%yukti` cell, or `%yukti`,
prints the help with the settings in force. Three profiles set them together:

| Profile | Sandbox | Working directory | Network | Tools |
| --- | --- | --- | --- | --- |
| `sandboxed` (default) | `read-only` | disposable temp directory | off | no |
| `elevated` | `workspace-write` | the kernel's directory | off | yes |
| `full` | `danger-full-access` | the kernel's directory | on | yes |

Any line after `permissions` overrides one value:

```python
%%yukti
permissions: elevated
cwd: ~/Code/report
writable_roots: /tmp/scratch
network: on
approvals: auto_review
```

- `sandbox`: `read-only`, `workspace-write`, or `danger-full-access`
- `cwd`: the directory Codex works in, or empty for a disposable one
- `network`, `tools`: `on` or `off`
- `writable_roots`: extra writable directories, separated by commas
- `approvals`: `never`, or `auto_review` to let a Codex subagent decide

With `elevated` or `full`, Codex may edit files and run commands. Yukti prints
one line per command and per file it changes. A notebook cannot show an approval
prompt, so Yukti accepts any approval request that still reaches it: the sandbox
and the working directory remain the real limits. Project instruction files stay
off in every profile, so an `AGENTS.md` never competes with Yukti's instruction.

## See the exact Codex request

Add `--debug` to show the authentication type, base instruction, thread settings,
and complete notebook transcript in the cell output:

```python
%%ask --debug
Why did the previous query return these rows?
```

Debug mode starts the local App Server protocol and stops before the model turn.

## Verify the installation

```bash
jupyter labextension list
python -m compileall -q yukti
```

The extension list should show `yukti` as enabled.
