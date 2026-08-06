#!/usr/bin/env bash
# Run the LLM4HLS harness inside the Vitis Docker container.
# Vitis 2025.2 is mounted from the host WSL2 at /tools/Xilinx.
#
# Usage:
#   export OPENROUTER_API_KEY=sk-or-...
#   ./scripts/run-docker.sh                                          # interactive shell
#   ./scripts/run-docker.sh python scripts/run_poc.py tasks/projection_bugfix --backend scripted
#   ./scripts/run-docker.sh python scripts/run_batch.py --tasks-dir tasks_all --models "qwen/qwen3.5-122b-a10b"

set -euo pipefail

IMAGE=${IMAGE:-vitis_runtime}
WORK_DIR=${WORK_DIR:-/mnt/e/FPGA/project/FPT/fpt26-harness}

# Attach TTY only when we have one
TTY=()
[ -t 0 ] && [ -t 1 ] && TTY=(-it)

# Command to run inside the container
if [ "$#" -eq 0 ]; then
  INNER='exec bash'
else
  INNER="exec $(printf '%q ' "$@")"
fi

echo "run-docker: image=$IMAGE  work=$WORK_DIR" >&2

exec docker run --rm "${TTY[@]}" \
  -v /tools/Xilinx:/tools/Xilinx:ro \
  -v "$WORK_DIR:$WORK_DIR" \
  -w "$WORK_DIR" \
  -e LLM4HLS_VITIS_HLS_ROOT=/tools/Xilinx/Vitis/2025.2 \
  -e OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}" \
  "$IMAGE" bash -lc "
    source /tools/Xilinx/Vitis/2025.2/settings64.sh
    cd '$WORK_DIR'
    $INNER
  "