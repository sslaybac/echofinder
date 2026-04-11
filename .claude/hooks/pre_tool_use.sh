#!/usr/bin/env bash
set -euo pipefail

cmd=$(jq -r '.tool_input.command // ""')

if echo "$cmd" | grep -Eq '\bpip\s+install\b'; then
    echo "Blocked: use 'uv add <package>' instead of 'pip install'." >&2
    exit 2
fi

if echo "$cmd" | grep -Eq '\bpython\s+\S+\.py\b'; then
    echo "Blocked: use 'uv run <script>.py' instead of 'python <script>.py'." >&2
    exit 2
fi

exit 0
