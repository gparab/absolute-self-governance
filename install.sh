#!/usr/bin/env bash
# Absolute Self-Governance is distributed on PyPI.
set -euo pipefail

echo "Install with pipx (recommended):"
echo "  pipx install absolute-self-governance"
echo ""
echo "Or with uv:"
echo "  uv tool install absolute-self-governance"
echo ""
echo "Server image: docker pull ghcr.io/gparab/absolute-self-governance:latest"

if command -v pipx &>/dev/null; then
    pipx install absolute-self-governance
elif command -v uv &>/dev/null; then
    uv tool install absolute-self-governance
else
    echo "Neither pipx nor uv found. Install pipx first: https://pipx.pypa.io" >&2
    exit 1
fi
