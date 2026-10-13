#!/bin/bash
# Host-side wrapper so claude -p (running on the host) can drive the in-container PoC
# REPL server. Routes isa_tool.py through docker exec; the file bridge lives on the
# mounted volume so requests/responses are shared between host and container.
#   bash isa_tool_host.sh state
#   bash isa_tool_host.sh try  '<tactic>'
#   bash isa_tool_host.sh commit '<tactic>'
#   bash isa_tool_host.sh stop
ROOT=/home/lijun/seL4-docker-main
exec docker compose -f "$ROOT/docker-compose.yml" exec -T \
  -e BRIDGE=/workspace/tools/seL4-proof-search/Isa-Repl/poc-bridge \
  l4v python3 /workspace/tools/seL4-proof-search/Isa-Repl/isa_tool.py "$@"
