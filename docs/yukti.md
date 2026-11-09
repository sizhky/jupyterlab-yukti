---
title: Yukti — one %%ask turn
code_root: ..
code_extensions: py,ts
---

# Yukti

Yukti turns a notebook question into one Codex turn. This walkthrough follows the smallest useful path: the frontend sends the cells above `%%ask`, the Jupyter server builds one bounded transcript, and the answer returns to the cell.

The graph leaves out settings profiles, editing tools, and retry behavior. Those are separate questions. Start here when you want to know what one ordinary `%%ask` request carries and where it goes.

```items
---
items_schema: yukti.kg/kg.schema
---
```

## The reader's test

After one pass, you should be able to answer three questions:

- Which participant runs the turn, and why can the notebook use any kernel? (Proposed view, pending your review.)
- Which function turns cells and their outputs into the model transcript?
- Why can a large output not fill the whole request?

The `ask` and `action` views are proposed and pending your review. `%%ask` and `%%yukti` run on the Jupyter server, not in the IPython kernel. The `ask-socket` lane is the WebSocket in `yukti/server.py`, and the `turn` lane is `ask` in `yukti/turn.py`. The `notebook-kernel` lane appears only when the model runs a cell, so Yukti works on any kernel. `yukti/ask.py`, `yukti/comm.py` and `yukti/execute.py` are retired and have no node.

The `claude` view shows the same turn with `backend: claude`. `ClaudeCode` checks the Claude subscription login, starts the user's own `claude -p`, answers notebook tool calls in the turn, and shows built-in tools as Codex items. Anthropic permits subscription use only through the unmodified Claude Code binary, so Yukti never reads or forwards the OAuth credential ([policy](https://code.claude.com/docs/en/legal-and-compliance)).

The `image` view shows a pasted image. `trackImagePastes` saves the image under `yukti-images` and writes a Markdown reference into the cell. `%%ask` reads the paths with `question_images`, stops on a missing file, and passes the paths to the backend. Codex receives a `localImage` path and Claude Code receives a base64 image block.

Hover an arrow for the source statement that performs that exchange. The transcript view isolates the two limits that protect one turn: 8 KB per output and 512 KB for the complete request.
