#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo 'Python 3 is required.' >&2; exit 1; }
command -v c++ >/dev/null || { echo 'A C++20 compiler is required (macOS: xcode-select --install).' >&2; exit 1; }
# Prefer a previously bootstrapped toolchain so repeat runs work offline.
if [[ -x .venv/bin/cmake && -x .venv/bin/ctest && -x .venv/bin/ninja ]]; then
  export PATH="$PWD/.venv/bin:$PATH"
fi
tools_valid() {
  command -v cmake >/dev/null && command -v ctest >/dev/null && command -v ninja >/dev/null || return 1
  python3 -c 'import re,subprocess,sys; v=re.search(r"(\d+)\.(\d+)",subprocess.check_output(["cmake","--version"],text=True)); sys.exit(not v or tuple(map(int,v.groups())) < (3,20))' &&
  python3 -c 'import re,subprocess,sys; v=re.search(r"(\d+)\.(\d+)",subprocess.check_output(["ninja","--version"],text=True)); sys.exit(not v or tuple(map(int,v.groups())) < (1,10))'
}
if ! tools_valid; then
  echo 'Installing pinned CMake and Ninja into repository-local .venv (network required; no sudo).'
  python3 -m venv .venv
  .venv/bin/python -m pip install 'cmake==3.31.10' 'ninja==1.11.1.4'
  export PATH="$PWD/.venv/bin:$PATH"
  tools_valid || { echo 'Installed tools failed version checks.' >&2; exit 1; }
fi
CMAKE=$(command -v cmake)
CTEST=$(command -v ctest)
"$CMAKE" --preset release
"$CMAKE" --build --preset release
"$CTEST" --preset release
if [[ -f tools/demo.py ]]; then python3 tools/demo.py --server "$PWD/build/release/asterbook"; fi
echo 'AsterBook setup, tests, and demo completed.'
