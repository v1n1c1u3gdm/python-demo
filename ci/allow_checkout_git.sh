#!/usr/bin/env bash
set -euo pipefail
checkout_root="$(cd -- "${1:?checkout path is required}" && pwd)"
git config --global --add safe.directory "$checkout_root"
git config --global --add safe.directory "$checkout_root/.git"
