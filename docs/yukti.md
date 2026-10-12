---
title: Yukti — one %%ask turn
code_root: ..
code_extensions: py,ts
---

# Yukti

Yukti turns a notebook question into one Codex turn. This walkthrough follows the smallest useful path: the frontend sends the cells above `%%ask`, the kernel builds one bounded transcript, and the answer returns to the cell.

The graph leaves out settings profiles, editing tools, and retry behavior. Those are separate questions. Start here when you want to know what one ordinary `%%ask` request carries and where it goes.

```items
---
items_schema: yukti.kg/kg.schema
---
```

## The reader's test

After one pass, you should be able to answer three questions:

- Where does the notebook prefix wait while the kernel runs `%%ask`?
- Which function turns cells and their outputs into the model transcript?
- Why can a large output not fill the whole request?

The `claude` view shows the same turn with `backend: claude`. `ClaudeCode` checks the Claude subscription login, starts the user's own `claude -p`, answers notebook tool calls in the kernel, and shows built-in tools as Codex items. Anthropic permits subscription use only through the unmodified Claude Code binary, so Yukti never reads or forwards the OAuth credential ([policy](https://code.claude.com/docs/en/legal-and-compliance)).

Hover an arrow for the source statement that performs that exchange. The transcript view isolates the two limits that protect one turn: 8 KB per output and 512 KB for the complete request.
