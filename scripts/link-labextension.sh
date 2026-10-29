#!/usr/bin/env bash
# Point a Jupyter environment at this repo's labextension build.
# An editable pip install does not refresh hatch shared-data, so the
# environment keeps the copy it had at install time.
#
# Usage: scripts/link-labextension.sh [--apply] [PREFIX]
# PREFIX defaults to ~/.venv. Without --apply it lists what it would change.
set -euo pipefail

apply=0
if [[ "${1:-}" == "--apply" ]]; then apply=1; shift; fi
prefix="${1:-$HOME/.venv}"
repo="$(cd "$(dirname "$0")/.." && pwd)"
source="$repo/yukti_frontend/labextension"
target="$prefix/share/jupyter/labextensions/yukti"
backup="$target.bak-$(date +%Y%m%d-%H%M)"

[[ -f "$source/package.json" ]] || { echo "no build at $source; run npm run build:prod"; exit 1; }

echo "source: $source"
echo "target: $target"
if [[ -L "$target" ]]; then
  echo "target is already a symlink to $(readlink "$target")"
  exit 0
fi
if [[ -e "$target" ]]; then
  echo "move:   $target -> $backup"
  find "$target" -type f | sed 's/^/  /'
fi
echo "link:   $target -> $source"

if [[ $apply -eq 0 ]]; then
  echo "dry run; pass --apply to make these changes"
  exit 0
fi
# A rename keeps the old copy, so nothing is deleted.
[[ -e "$target" ]] && mv "$target" "$backup"
ln -s "$source" "$target"
echo "done; restart Jupyter"
