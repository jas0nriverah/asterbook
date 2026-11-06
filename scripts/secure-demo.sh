#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
server="${1:-$PWD/build/release/asterbook}"
python3 tools/secure_demo.py --server "$server"
