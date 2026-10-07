#!/usr/bin/env bash
# Install the video compressor on Ubuntu without prompting.
set -euo pipefail

if command -v ffmpeg >/dev/null 2>&1; then
  if ffmpeg -version >/dev/null 2>&1; then
    echo "ffmpeg is available."
    exit 0
  fi
  echo "ERROR: ffmpeg exists but cannot run. Repair the server installation." >&2
  exit 1
fi

if [[ $EUID -ne 0 ]] || ! command -v apt-get >/dev/null 2>&1; then
  echo "ERROR: ffmpeg is missing. On Ubuntu, run: sudo apt-get update && sudo apt-get install -y --no-install-recommends ffmpeg" >&2
  exit 1
fi

echo "Installing ffmpeg for vehicle video uploads..."
export DEBIAN_FRONTEND=noninteractive
if ! apt-get -o DPkg::Lock::Timeout=60 update \
    || ! apt-get -o DPkg::Lock::Timeout=60 install -y --no-install-recommends ffmpeg; then
  echo "ERROR: ffmpeg installation failed. Check apt/network/disk errors above; video uploads remain unavailable." >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! ffmpeg -version >/dev/null 2>&1; then
  echo "ERROR: ffmpeg is still unavailable after installation." >&2
  exit 1
fi
echo "ffmpeg installed and verified."
