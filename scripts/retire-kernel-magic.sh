#!/usr/bin/env bash
# Remove the IPython %%ask path that the Jupyter server extension replaced.
# Dry run by default; pass --apply to remove. Commit or drop the uncommitted
# held-cells change in yukti/ask.py first: yukti/turn.py already carries it.
set -euo pipefail
cd "$(dirname "$0")/.."
TARGETS=(yukti/ask.py yukti/comm.py yukti/execute.py yukti/tests/test_ask.py yukti/tests/test_execute.py)
for path in "${TARGETS[@]}"; do
  [ -e "$path" ] && echo "remove $path ($(wc -l < "$path") lines)"
done
if [ "${1:-}" != "--apply" ]; then
  echo "dry run; rerun with --apply to remove these files from git and disk"
  exit 0
fi
git rm -q -- "${TARGETS[@]}"
echo "done; next drop ipython and ipykernel from pyproject.toml dependencies"
