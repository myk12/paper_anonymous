#!/usr/bin/env bash
set -euo pipefail

# Purpose:
#   Provision the build/runtime dependencies for NOPaxos on a host.
#
# Usage:
#   chmod +x infra/server_host/setenv_nopaxos.sh
#   ./infra/server_host/setenv_nopaxos.sh
#   ./infra/server_host/setenv_nopaxos.sh --no-update
#   ./infra/server_host/setenv_nopaxos.sh --with-bazel
#
# Notes:
# - Assumes Debian/Ubuntu (apt).
# - Installs a high-coverage dependency set commonly needed by NOPaxos forks.
# - If later compilation complains about a missing library, paste the error and
#   we’ll refine this list.

NO_UPDATE=0
WITH_BAZEL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-update) NO_UPDATE=1; shift 1;;
    --with-bazel) WITH_BAZEL=1; shift 1;;
    -h|--help)
      cat <<EOF
Usage: $0 [--no-update] [--with-bazel]
  --no-update   Skip 'apt-get update'
  --with-bazel  Install bazelisk (provides 'bazel') for WORKSPACE-based builds
EOF
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

echo "[INFO] Provisioning NOPaxos build/runtime dependencies (no build/run)."

if [[ "${NO_UPDATE}" -eq 0 ]]; then
  echo "[STEP] apt-get update"
  sudo apt-get update -y
else
  echo "[STEP] Skipping apt-get update (--no-update)"
fi

echo "[STEP] Installing base toolchain + common deps"
sudo apt-get install -y \
  build-essential \
  clang \
  cmake \
  ninja-build \
  pkg-config \
  ca-certificates \
  autoconf automake libtool \
  libssl-dev \
  libevent-dev \
  zlib1g-dev \
  libgflags-dev \
  libgtest-dev \
  libprotobuf-dev protobuf-compiler \
  libunwind-dev \
  libnuma-dev \
  iproute2 iputils-ping \
  tcpdump netcat-openbsd \
  jq

if [[ "${WITH_BAZEL}" -eq 1 ]]; then
  echo "[STEP] Installing bazelisk (optional)"
  sudo apt-get install -y bazelisk || true
  if command -v bazelisk >/dev/null 2>&1 && ! command -v bazel >/dev/null 2>&1; then
    sudo ln -sf "$(command -v bazelisk)" /usr/local/bin/bazel
  fi
  echo "[INFO] bazel: $(command -v bazel 2>/dev/null || echo 'not found')"
fi

echo "[STEP] Quick sanity checks"
for cmd in gcc g++ cmake make git python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[WARN] missing: $cmd"
  else
    echo "[OK] $cmd -> $(command -v "$cmd")"
  fi
done

echo "[SUCCESS] NOPaxos Environment provisioning complete."
