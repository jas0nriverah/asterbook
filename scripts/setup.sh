#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
benchmark_option=OFF
if [[ $# -gt 0 ]]; then
  if [[ $# -eq 1 && "$1" == "--enable-google-benchmark" ]]; then benchmark_option=ON
  else echo 'usage: scripts/setup.sh [--enable-google-benchmark]' >&2; exit 2; fi
fi
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
# OpenSSL headers and libraries are needed for the TLS gateway. Discover common
# user-managed locations without modifying the machine.
if [[ -z "${OPENSSL_ROOT_DIR:-}" ]]; then
  for prefix in "$PWD/.deps/openssl" /opt/homebrew/opt/openssl@3 /usr/local/opt/openssl@3 "$HOME/.local/openssl"; do
    if [[ -f "$prefix/include/openssl/ssl.h" ]]; then export OPENSSL_ROOT_DIR="$prefix"; break; fi
  done
fi
if [[ -n "${OPENSSL_ROOT_DIR:-}" ]]; then export CMAKE_PREFIX_PATH="$OPENSSL_ROOT_DIR${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"; fi
CMAKE=$(command -v cmake)
CTEST=$(command -v ctest)
configure_log=$(mktemp)
trap 'rm -f "$configure_log"' EXIT
if ! "$CMAKE" --preset release -DASTERBOOK_GOOGLE_BENCHMARK="$benchmark_option" 2>&1 | tee "$configure_log"; then
  if ! grep -Eq 'Could NOT find OpenSSL|OPENSSL_(SSL|CRYPTO)_LIBRARY|OPENSSL_INCLUDE_DIR' "$configure_log"; then
    echo 'Configuration failed for a reason other than OpenSSL; see output above.' >&2
    exit 1
  fi
  echo 'OpenSSL was not found; building it inside .deps.' >&2
  ./scripts/bootstrap-openssl.sh
  export OPENSSL_ROOT_DIR="$PWD/.deps/openssl"
  "$CMAKE" --preset release -DASTERBOOK_GOOGLE_BENCHMARK="$benchmark_option" -DOPENSSL_ROOT_DIR="$OPENSSL_ROOT_DIR" -DOPENSSL_USE_STATIC_LIBS=TRUE
fi
if [[ -n "${OPENSSL_ROOT_DIR:-}" && -x "$OPENSSL_ROOT_DIR/bin/openssl" ]]; then export PATH="$OPENSSL_ROOT_DIR/bin:$PATH"; fi
"$CMAKE" --build --preset release
"$CTEST" --preset release
./scripts/secure-demo.sh "$PWD/build/release/asterbook"
echo 'AsterBook setup, tests, and demo completed.'
