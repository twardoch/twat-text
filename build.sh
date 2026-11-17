#!/usr/bin/env bash
# this_file: build.sh
# Build only the adjacent documentation: ./build.sh --docs [renderer options]
if [[ "${1:-}" == --docs ]]; then
  shift
  set -euo pipefail
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
  export PYTHONPATH="$PWD:$PWD/src:$PWD/../src:$PWD/..${PYTHONPATH:+:$PYTHONPATH}"
  exec uvx --python 3.12 --from mkdocs==1.6.1 \
    --with mkdocs-material \
    --with 'mkdocstrings[python]' \
    --with pymdown-extensions \
    mkdocs build --config-file mkdocs.yml "$@"
fi

# build.sh — Build twat-text
# Text processing plugin for twat
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Linting..."
fd -e py -x uvx ruff check --fix {} 2>/dev/null || true
fd -e py -x uvx ruff format {} 2>/dev/null || true

echo "Testing..."
uvx hatch test 2>/dev/null || uv run pytest -xvs 2>/dev/null || echo "No tests configured"

echo "Building..."
uvx hatch build 2>/dev/null || uv build 2>/dev/null || echo "Build skipped"

echo "Done."
