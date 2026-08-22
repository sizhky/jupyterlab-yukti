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

Yukti sends cells above that question through a Jupyter Comm channel. Outputs are
limited to 8 KB each, and the complete request is limited to 512 KB. Yukti starts
a disposable Codex App Server thread with its own visible base instruction, an
empty working directory, a read-only sandbox, and no project instruction files.

Yukti accepts only ChatGPT subscription authentication. It stops before the model
call when Codex reports API-key authentication.

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
