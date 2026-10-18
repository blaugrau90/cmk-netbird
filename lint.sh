#!/bin/bash
# Lint, type-check and test the plugin. Usage: ./lint.sh
# mypy runs per file because several plugin files share the module name "netbird".
# For the type check the real `cmk` package must be importable (PYTHONPATH or run in a site).
set -euo pipefail
cd "$(dirname "$0")"

ruff check . netbird/libexec/agent_netbird
for f in netbird/libexec/agent_netbird netbird/*/netbird.py netbird/server_side_calls/special_agent.py; do
    mypy "$f"
done
pytest -q
