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
- `run`: `on` or `off`, whether Yukti runs the code cells it inserts
- `writable_roots`: extra writable directories, separated by commas
- `approvals`: `never`, or `auto_review` to let a Codex subagent decide

With `elevated` or `full`, Codex may edit files and run commands. Yukti prints
one line per command and per file it changes. A notebook cannot show an approval
prompt, so Yukti accepts any approval request that still reaches it: the sandbox
and the working directory remain the real limits. Project instruction files stay
off in every profile, so an `AGENTS.md` never competes with Yukti's instruction.

## Let Yukti run the cells it writes

Yukti runs a code cell it has just inserted and reads what that cell printed, so
one question can end in a cell that works instead of a cell that looks right. The
run appears where the code is: the prompt shows `[*]` while the kernel is inside
the cell, and the outputs land under it. If the run fails, Yukti reads the
traceback, rewrites the cell, and runs it again.

The code runs in your kernel, in the namespace your earlier cells built, so it
sees your variables and can change them. Yukti runs only the code cells it
inserted in the same turn, never a cell of your own, and never a cell that starts
with `%%ask` or `%%yukti`. One turn runs at most 20 cells.

That cell holds no execute request of its own, so `input()` and live widgets do
not work in it, and it shows the same execution count as the `%%ask` cell that
ran it. Running is on by default, and one line turns it off:

```python
%%yukti
run: off
```

## See the exact Codex request

Add `--debug` to show the authentication type, base instruction, thread settings,
and complete notebook transcript in the cell output:

```python
%%ask --debug
Why did the previous query return these rows?
```

Debug mode starts the local App Server protocol and stops before the model turn.

## Measure a slow turn

Add `--trace` to write every message of the turn to
`~/.cache/yukti/traces/<time>.jsonl`, and to print the timing table under the
answer:

```python
%%ask --trace
Find the number check() is hiding.
```

The table names each wait of half a second or more, and the header splits the
turn three ways: `model` is Codex thinking and streaming, `Yukti` is the
notebook work itself, and `unread` is a tool call that had arrived and was
waiting to be read, which should stay near zero. Read any earlier trace the
same way:

```bash
python -m yukti.trace ~/.cache/yukti/traces/*.jsonl
```

Pass `--gap-ms 2000` to name only the waits over two seconds.

## Copy a cell

Click a cell. Three buttons join the cell toolbar above it. Each button shows
the cell as two bars, and the solid bar marks the part it copies:

- top bar solid: the source
- bottom bar solid: the output text
- both bars solid: the source, then the output

Every button wraps what it copies in a code fence, and the source fence carries
the kernel's language. A cell without output copies nothing for the middle
button.

The three buttons also appear in the command palette as `Copy Input`,
`Copy Output`, and `Copy Input and Output`, so you can bind a keyboard shortcut
to each one. The buttons follow the active cell, because JupyterLab keeps one
cell toolbar and moves it to the cell you click.

Copied output holds the same text that `%%ask` sends to Codex, so a rich output
such as a DataFrame arrives as its plain-text form.

## Verify the installation

```bash
jupyter labextension list
python -m compileall -q yukti
```

The extension list should show `yukti` as enabled.
