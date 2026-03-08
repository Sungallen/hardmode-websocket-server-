#!/usr/bin/env bash
#
# Start the full multi-agent stack for local development.
#
# Usage:
#   ./python/dev.sh                                    # Disk mode (reads from recordings/)
#   ./python/dev.sh --user=a0975671396@gmail.com       # Specify user
#   ./python/dev.sh --demo                             # Webcam
#   ./python/dev.sh --image python/test_sunflower.jpg  # Static image
#   ./python/dev.sh --script=python/demos/example_demo.json  # Choreographed demo
#   ./python/dev.sh --no-claude                        # Deterministic only
#
# Ctrl-C to stop all agents.

set -e

# Resolve paths relative to the repo root (oslo/)
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_DIR="$REPO_ROOT/python"
cd "$REPO_ROOT"

# Load .env from repo root if it exists
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

CLAUDE_FLAG=""
USER_ID="a0975671396@gmail.com"
SOURCE_FLAG="--disk $REPO_ROOT/recordings/$USER_ID/latest.jpg"
SCRIPT_FLAG=""

for arg in "$@"; do
    case "$arg" in
        --no-claude)  CLAUDE_FLAG="--no-claude" ;;
        --demo)       SOURCE_FLAG="--demo" ;;
        --image=*)    SOURCE_FLAG="--image ${arg#*=}" ;;
        --script=*)   SCRIPT_FLAG="--demo-script ${arg#*=}" ;;
        --user=*)     USER_ID="${arg#*=}"
                      SOURCE_FLAG="--disk $REPO_ROOT/recordings/$USER_ID/latest.jpg" ;;
    esac
done

# Trap Ctrl-C to kill all background processes
cleanup() {
    echo ""
    echo "Stopping all agents..."
    kill $(jobs -p) 2>/dev/null
    wait 2>/dev/null
    echo "All agents stopped."
}
trap cleanup EXIT INT TERM

echo "=== Reachy Agents — Multi-Agent Stack ==="
echo "  Repo root: $REPO_ROOT"
echo "  Source: $SOURCE_FLAG"
echo "  User: $USER_ID"
echo "  Claude: ${CLAUDE_FLAG:-enabled}"
echo "  Script: ${SCRIPT_FLAG:-none}"
echo ""

# Start agents in background
python "$PYTHON_DIR/agents/rover_agent.py"     $CLAUDE_FLAG --http-port 8001 &
python "$PYTHON_DIR/agents/butterfly_agent.py" $CLAUDE_FLAG --http-port 8002 &
python "$PYTHON_DIR/agents/body_agent.py"      $CLAUDE_FLAG --http-port 8003 &
python "$PYTHON_DIR/agents/log_agent.py"                    --http-port 8004 &

# Give agents a moment to start their HTTP servers
sleep 1

echo ""
echo "=== All agents running. Starting core loop... ==="
echo ""

# Core loop in foreground
python "$PYTHON_DIR/core_loop.py" $SOURCE_FLAG $SCRIPT_FLAG \
    --http-agents http://localhost:8001 http://localhost:8002 http://localhost:8003 http://localhost:8004
