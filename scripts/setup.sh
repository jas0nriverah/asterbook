#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
benchmark_option=OFF
check_only=false
case "${1:-}" in
  '') ;;
  --enable-google-benchmark) benchmark_option=ON ;;
  --check) check_only=true ;;
  *) echo 'usage: ./start.sh [--check | --enable-google-benchmark]' >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then echo 'Only one option is supported.' >&2; exit 2; fi
install_help() {
  cat >&2 <<'HELP'
Install the missing dependencies, then run ./start.sh again:

macOS:
  xcode-select --install
  brew install python cmake ninja openssl@3
  If Homebrew is missing, follow https://brew.sh

Ubuntu / Debian:
  sudo apt-get update
  sudo apt-get install build-essential python3 python3-venv cmake ninja-build libssl-dev openssl

Fedora:
  sudo dnf install gcc-c++ python3 cmake ninja-build openssl-devel openssl

If your distribution's CMake is older than 3.24:
  python3 -m venv .venv
  .venv/bin/python -m pip install 'cmake==3.31.10' 'ninja==1.11.1.4'

Requires Python 3.10+, CMake 3.24+, Ninja 1.10+, a C++20 compiler,
and OpenSSL development headers/libraries plus its command-line tool.
HELP
}
# Reuse locally installed tools, without installing anything automatically.
if [[ -x .venv/bin/cmake && -x .venv/bin/ctest && -x .venv/bin/ninja ]]; then
  export PATH="$PWD/.venv/bin:$PATH"
fi
if [[ -z "${OPENSSL_ROOT_DIR:-}" ]]; then
  for prefix in "$PWD/.deps/openssl" /opt/homebrew/opt/openssl@3 /usr/local/opt/openssl@3 "$HOME/.local/openssl"; do
    if [[ -f "$prefix/include/openssl/ssl.h" ]]; then export OPENSSL_ROOT_DIR="$prefix"; break; fi
  done
fi
if [[ -n "${OPENSSL_ROOT_DIR:-}" ]]; then
  export CMAKE_PREFIX_PATH="$OPENSSL_ROOT_DIR${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
  if [[ -x "$OPENSSL_ROOT_DIR/bin/openssl" ]]; then export PATH="$OPENSSL_ROOT_DIR/bin:$PATH"; fi
fi
missing=false
for tool in python3 "${CXX:-c++}" cmake ctest ninja openssl; do
  if ! command -v "$tool" >/dev/null; then echo "Missing dependency: $tool" >&2; missing=true; fi
done
if "$missing"; then install_help; exit 1; fi
if ! python3 - <<'PY'
import re
import subprocess
import sys
valid = True
if sys.version_info < (3, 10):
    print('Python 3.10 or newer is required.', file=sys.stderr)
    valid = False
for tool, minimum in [('cmake', (3, 24)), ('ctest', (3, 24)), ('ninja', (1, 10))]:
    text = subprocess.check_output([tool, '--version'], text=True)
    match = re.search(r'(\d+)\.(\d+)', text)
    if not match or tuple(map(int, match.groups())) < minimum:
        print(f'{tool} {minimum[0]}.{minimum[1]} or newer is required.', file=sys.stderr)
        valid = False
sys.exit(0 if valid else 1)
PY
then install_help; exit 1; fi
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
cat > "$scratch/check.cpp" <<'CPP'
#include <span>
#include <concepts>
static_assert(std::integral<int>);
int main() { int values[] = {1}; return std::span(values).size() != 1; }
CPP
if ! "${CXX:-c++}" -std=c++20 "$scratch/check.cpp" -o "$scratch/check"; then
  echo 'The compiler or C++20 standard library is unavailable.' >&2
  install_help
  exit 1
fi
# CMake checks OpenSSL headers and libraries, not just the openssl executable.
if ! cmake --preset release -DASTERBOOK_GOOGLE_BENCHMARK="$benchmark_option"; then
  echo 'Configuration failed. Review the error above; dependency installation commands follow.' >&2
  install_help
  exit 1
fi
if "$check_only"; then echo 'Dependencies and build configuration are ready.'; exit 0; fi
cmake --build --preset release
ctest --preset release
./scripts/secure-demo.sh "$PWD/build/release/asterbook"
echo 'AsterBook setup, tests, and demo completed.'
