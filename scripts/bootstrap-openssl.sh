#!/usr/bin/env bash
# Build an isolated supported OpenSSL release; does not modify global packages.
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in perl make curl tar python3 cc; do command -v "$tool" >/dev/null || { echo "OpenSSL bootstrap needs $tool; install it and rerun setup." >&2; exit 1; }; done
root="$PWD/.deps"
prefix="$root/openssl"
[[ -f "$prefix/include/openssl/ssl.h" && -f "$prefix/lib/libssl.a" && -f "$prefix/ssl/openssl.cnf" ]] && exit 0
mkdir -p "$root"
archive="$root/openssl-3.5.8.tar.gz"
url=https://github.com/openssl/openssl/releases/download/openssl-3.5.8/openssl-3.5.8.tar.gz
expected=a8f84a39918ec6415ce765d9b429d313ba97b8143169c172e734b9514464f5b2
[[ -f "$archive" ]] || curl -fL --retry 2 "$url" -o "$archive"
actual=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$archive")
[[ "$actual" == "$expected" ]] || { echo 'OpenSSL source checksum mismatch' >&2; exit 1; }
tar -xf "$archive" -C "$root"
(cd "$root/openssl-3.5.8" && ./Configure --prefix="$prefix" --libdir=lib no-shared && make -j"${ASTERBOOK_BUILD_JOBS:-4}" && make install_sw && make install_ssldirs)
