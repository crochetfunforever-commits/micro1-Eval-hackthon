#!/usr/bin/env bash
# One-click runner: creates a venv, installs requirements quietly, runs the grader.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -d venv ]; then
  python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate
pip install --quiet --disable-pip-version-check -r requirements.txt

echo "=============================================="
echo " micro1 Eval - ADR Causality Benchmark"
echo "=============================================="
python src/grader.py
echo "=============================================="
