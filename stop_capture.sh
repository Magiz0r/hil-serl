#!/usr/bin/env bash
set -euo pipefail

capture_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
capture_python="${CAPTURE_PYTHON:-${HOME}/miniconda3/envs/hilserl/bin/python}"
if [[ ! -x "$capture_python" ]]; then
  echo "找不到 hilserl Python：$capture_python。可通过 CAPTURE_PYTHON 指定。" >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
exec "$capture_python" "$capture_repo/reproduction/capture_portal.py" stop "$@"
