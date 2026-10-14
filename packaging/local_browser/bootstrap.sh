#!/bin/sh
# Fixed official uv release, with archive checksums checked before extraction.
# No sudo, profile edits, system Python changes or remote script evaluation.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
mode=${1:-start}
case "$mode" in setup|start|check) ;; *) echo 'Use setup, start, or check.' >&2; exit 2;; esac
platform=$(uname -s); machine=$(uname -m)
case "$platform/$machine" in
 Darwin/arm64) target=aarch64-apple-darwin; checksum=dc304b9ed1b24174572290fba60ac3f6fe63c73a671f0439e62a91375841964d;;
 Darwin/x86_64) target=x86_64-apple-darwin; checksum=e9ca61775532368fe518ab03e7a354c7ecab8ccb3c7d941c775fcc4a362b801b;;
 Linux/x86_64) target=x86_64-unknown-linux-gnu; checksum=f97935763c04be3e692460a7aaeaaab8fc3b78fcf8b389da820b38ae7423a638;;
 *) echo 'This CPU/OS is not included in this preview.' >&2; exit 2;;
esac
export UV_PYTHON_INSTALL_DIR="$PWD/.runtime/python"
export UV_CACHE_DIR="$PWD/.runtime/cache"
export UV_NO_CONFIG=1
uv="$PWD/.runtime/tools/uv"
if [ "$mode" = setup ]; then
 mkdir -p .runtime
 if [ ! -x "$uv" ]; then
  task_dir=$(mktemp -d "$PWD/.runtime/download.XXXXXX")
  url="https://github.com/astral-sh/uv/releases/download/0.12.15/uv-$target.tar.gz"
  echo 'Downloading the pinned environment manager from GitHub (Astral).'
  curl --proto '=https' --tlsv1.2 --fail --location "$url" -o "$task_dir/uv.tar.gz"
  if command -v shasum >/dev/null 2>&1; then actual=$(shasum -a 256 "$task_dir/uv.tar.gz" | cut -d ' ' -f1)
  else actual=$(sha256sum "$task_dir/uv.tar.gz" | cut -d ' ' -f1); fi
  [ "$actual" = "$checksum" ] || { echo 'Download checksum mismatch; stopping.' >&2; exit 1; }
  tar -xzf "$task_dir/uv.tar.gz" -C "$task_dir"
  mkdir -p .runtime/tools
  cp "$task_dir/uv-$target/uv" "$uv"
  chmod 755 "$uv"
 fi
 if [ ! -x .venv/bin/python ]; then "$uv" venv --python 3.12 --managed-python .venv; fi
 .venv/bin/python manage.py setup --uv "$uv"
else
 [ -x .venv/bin/python ] || { echo 'Run Setup first.' >&2; exit 1; }
 .venv/bin/python manage.py "$mode"
fi
